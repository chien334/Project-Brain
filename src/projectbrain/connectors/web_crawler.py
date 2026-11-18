"""
web crawler connector for projectbrain
requires: httpx, beautifulsoup4
no auth required for public urls
"""
from typing import List, Dict, Optional, Set
import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse
from .base import base_connector


def _check_public_ip(ip_str: str, url: str) -> None:
    cleaned = ip_str.split("%", 1)[0] if "%" in ip_str else ip_str
    addr = ipaddress.ip_address(cleaned)
    candidates = [addr]
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        candidates.append(addr.ipv4_mapped)

    for candidate in candidates:
        if (
            candidate.is_private
            or candidate.is_loopback
            or candidate.is_link_local
            or candidate.is_multicast
            or candidate.is_reserved
            or candidate.is_unspecified
        ):
            raise ValueError(
                f"SSRF protection blocked URL '{url}': resolved IP '{cleaned}' is non-public."
            )


def _validate_public_url(url: str) -> str:
    """Validate that a crawl target URL uses http/https and resolves strictly to public IPs."""
    if not url or not isinstance(url, str):
        raise ValueError("Invalid URL: URL must be a non-empty string.")

    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Blocked URL '{url}': scheme must be http or https.")

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise ValueError(f"Blocked URL '{url}': missing hostname.")

    if os.getenv("PB_ALLOW_PRIVATE_CRAWL", "false").lower() in ("true", "1", "yes"):
        return url

    if (
        hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
    ):
        raise ValueError(f"SSRF protection blocked URL '{url}': internal hostname '{hostname}' is not permitted.")

    # Direct IP literal check before DNS resolution
    try:
        _check_public_ip(hostname, url)
        return url
    except ValueError as ve:
        if "SSRF protection blocked URL" in str(ve):
            raise
    except Exception:
        pass

    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        addr_infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Blocked URL '{url}': failed to resolve hostname '{hostname}' ({exc}).") from exc

    if not addr_infos:
        raise ValueError(f"Blocked URL '{url}': hostname '{hostname}' resolved to no addresses.")

    for _, _, _, _, sockaddr in addr_infos:
        if sockaddr and len(sockaddr) >= 1:
            _check_public_ip(str(sockaddr[0]), url)

    return url


async def _safe_fetch_with_redirects(
    client: "httpx.AsyncClient",
    url: str,
    headers: Dict[str, str],
    max_redirects: int = 5,
) -> "httpx.Response":
    """Fetch URL with follow_redirects disabled, validating every redirect hop via _validate_public_url."""
    current_url = url
    for _ in range(max_redirects + 1):
        _validate_public_url(current_url)
        resp = await client.get(current_url, headers=headers, follow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location")
            if not location:
                return resp
            next_url = urljoin(current_url, location)
            _validate_public_url(next_url)
            current_url = next_url
            continue
        return resp
    raise ValueError(f"Too many redirects (> {max_redirects}) while fetching '{url}'.")


class web_crawler_connector(base_connector):
    """connector for crawling web pages"""

    name = "web_crawler"

    def __init__(self, user_id: str = None, max_pages: int = 50, max_depth: int = 3):
        super().__init__(user_id)
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.visited: Set[str] = set()
        self.crawled: List[Dict] = []

    async def connect(self, **creds) -> bool:
        """no auth needed for public crawling"""
        self._connected = True
        return True

    async def list_items(self, start_url: str = None, follow_links: bool = True, **filters) -> List[Dict]:
        """
        crawl from starting url and list discovered pages

        args:
            start_url: url to start crawling from
            follow_links: whether to follow internal links
        """
        if not start_url:
            raise ValueError("start_url is required")

        _validate_public_url(start_url)

        try:
            import httpx
            from bs4 import BeautifulSoup
        except ImportError:
            raise ImportError("pip install httpx beautifulsoup4")

        self.visited.clear()
        self.crawled.clear()

        base_domain = urlparse(start_url).netloc
        to_visit = [(start_url, 0)]
        headers = {"User-Agent": "ProjectBrain-Crawler/1.0 (compatible)"}

        async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
            while to_visit and len(self.crawled) < self.max_pages:
                url, depth = to_visit.pop(0)

                if url in self.visited:
                    continue

                if depth > self.max_depth:
                    continue

                self.visited.add(url)

                try:
                    resp = await _safe_fetch_with_redirects(client, url, headers=headers)

                    if resp.status_code != 200:
                        continue

                    content_type = resp.headers.get("content-type", "")
                    if "text/html" not in content_type:
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")
                    title = soup.title.string if soup.title else url

                    self.crawled.append({
                        "id": url,
                        "name": title.strip() if title else url,
                        "type": "webpage",
                        "url": url,
                        "depth": depth
                    })
                    if follow_links and depth < self.max_depth:
                        for link in soup.find_all("a", href=True):
                            href = link["href"]
                            full_url = urljoin(url, href)
                            parsed = urlparse(full_url)
                            if (parsed.scheme or "").lower() in ("http", "https") and parsed.netloc == base_domain:
                                clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                                if clean_url not in self.visited:
                                    try:
                                        _validate_public_url(clean_url)
                                        to_visit.append((clean_url, depth + 1))
                                    except ValueError:
                                        continue

                except Exception as e:
                    print(f"[crawler] failed to fetch {url}: {e}")
                    continue

        return self.crawled

    async def fetch_item(self, item_id: str) -> Dict:
        """
        fetch and extract text from a url

        item_id is the url to fetch
        """
        _validate_public_url(item_id)

        try:
            import httpx
            from bs4 import BeautifulSoup
        except ImportError:
            raise ImportError("pip install httpx beautifulsoup4")

        headers = {"User-Agent": "ProjectBrain-Crawler/1.0 (compatible)"}
        async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
            resp = await _safe_fetch_with_redirects(client, item_id, headers=headers)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            for element in soup(["script", "style", "nav", "footer", "header"]):
                element.decompose()
            title = soup.title.string if soup.title else item_id
            main = soup.find("main") or soup.find("article") or soup.find("body")

            if main:
                text = main.get_text(separator="\n", strip=True)
            else:
                text = soup.get_text(separator="\n", strip=True)
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            text = "\n".join(lines)

        return {
            "id": item_id,
            "name": title.strip() if title else item_id,
            "type": "webpage",
            "text": text,
            "data": text,
            "meta": {
                "source": "web_crawler",
                "url": item_id,
                "char_count": len(text)
            }
        }
