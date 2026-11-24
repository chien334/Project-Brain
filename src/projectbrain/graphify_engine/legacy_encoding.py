"""Legacy text encoding detection and decoding utilities (pure Python stdlib).

Handles Japanese and Windows legacy source/resource encodings commonly encountered
in older C/C++, MFC/Win32, COM/IDL, RC/DEF/REG, PLC Structured Text (IEC 61131-3 /
LASAL), VB6, Delphi, and COBOL repositories without raising ``UnicodeDecodeError``
or corrupting Japanese comments into replacement characters.

Detection priority:
1. UTF-8 with BOM (``utf-8-sig``), ISO-2022-JP escape-coded 7-bit streams
   (``iso2022_jp``), UTF-16 BOM (``utf-16-le`` / ``utf-16-be``), and BOM-less
   UTF-16 LE / BE via null-byte asymmetry heuristic.
2. Strict UTF-8 (``utf-8``).
3. Japanese legacy encodings: CP932 / Shift-JIS (``cp932``, ``shift_jis``),
   EUC-JP (``euc_jp``), and ISO-2022-JP (``iso2022_jp``), using a Japanese
   character naturalness heuristic whenever multiple candidate decoders succeed.
4. Windows-1252 / Latin-1 fallback (``cp1252`` strict, then ``latin-1`` /
   ``cp1252`` with ``errors="replace"`` as a last resort).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple, Union


# ISO-2022-JP 7-bit escape sequences (JIS X 0208 / JIS X 0201).
_ISO2022_MARKERS: Tuple[bytes, ...] = (
    b"\x1b$B",
    b"\x1b$@",
    b"\x1b(J",
    b"\x1b(I",
    b"\x1b(B",
)


def _looks_like_utf16_no_bom(raw: bytes) -> Optional[str]:
    """Heuristically detect BOM-less UTF-16 LE or UTF-16 BE via NUL-byte distribution.

    Windows ``.rc``, ``.def``, ``.reg``, and Visual Studio headers are frequently
    saved as UTF-16 LE without a BOM (or after BOM stripping). Because ``\\x00`` is
    technically valid UTF-8, checking this heuristic before strict UTF-8 prevents
    UTF-16 ASCII text from being mis-decoded with interleaved ``\\x00`` characters.
    """
    n = len(raw)
    if n < 4 or n % 2 != 0 or b"\x00" not in raw:
        return None

    half = n // 2
    even_nulls = raw[0::2].count(0)
    odd_nulls = raw[1::2].count(0)

    # Require meaningful NUL count and strong even/odd asymmetry.
    min_nulls = max(2, int(half * 0.20))
    if odd_nulls >= min_nulls and odd_nulls > even_nulls * 2:
        return "utf-16-le"
    if even_nulls >= min_nulls and even_nulls > odd_nulls * 2:
        return "utf-16-be"
    return None


def _japanese_naturalness_score(text: str) -> int:
    """Score decoded Japanese text to disambiguate CP932/Shift-JIS vs EUC-JP overlap.

    Pure EUC-JP hiragana/symbol sequences (bytes ``0xA1..0xFE``) can accidentally
    decode under CP932 as runs of half-width Katakana (``U+FF61..U+FF9F``).
    Rewarding full-width Kana/CJK ideographs over half-width Katakana and C0/C1
    control artifacts cleanly resolves overlapping byte streams.
    """
    score = 0
    for ch in text:
        code = ord(ch)
        if 0x3040 <= code <= 0x309F:  # Hiragana
            score += 4
        elif 0x30A0 <= code <= 0x30FF:  # Full-width Katakana
            score += 3
        elif 0x4E00 <= code <= 0x9FFF:  # CJK Unified Ideographs
            score += 3
        elif 0x3000 <= code <= 0x303F or 0xFF01 <= code <= 0xFF5E:  # CJK punct / full-width ASCII
            score += 2
        elif 0xFF61 <= code <= 0xFF9F:  # Half-width Katakana (common EUC-JP-as-CP932 artifact)
            score -= 1
        elif code < 0x20 and ch not in ("\n", "\r", "\t"):
            score -= 5
        elif 0x7F <= code <= 0x9F:  # C1 control characters
            score -= 5
    return score


def _has_cp932_undefined_pua(text: str) -> bool:
    """Detect Python cp932 round-trip PUA placeholders for undefined bytes (0x80, 0xA0, 0xFD-0xFF)."""
    return any(ch == "\x80" or 0xF8F0 <= ord(ch) <= 0xF8F3 for ch in text)


def _try_strict_decode(raw: bytes, encoding: str) -> Optional[str]:
    """Attempt strict decoding with ``encoding``; return ``None`` on failure."""
    try:
        return raw.decode(encoding, errors="strict")
    except (UnicodeDecodeError, LookupError):
        return None


def decode_legacy_bytes(raw: bytes) -> Tuple[str, str]:
    """Decode raw file bytes using legacy-aware encoding detection.

    Args:
        raw: Raw file bytes to decode.

    Returns:
        A ``(text, encoding)`` tuple where ``text`` is the decoded Unicode string
        and ``encoding`` is the codec name that produced it.
    """
    if not raw:
        return "", "utf-8"

    # 1a. UTF-8 with BOM
    if raw.startswith(b"\xef\xbb\xbf"):
        decoded = _try_strict_decode(raw, "utf-8-sig")
        if decoded is not None:
            return decoded, "utf-8-sig"

    # 1b. UTF-16 LE / BE with BOM (guard against UTF-32 LE BOM 0xFF 0xFE 0x00 0x00)
    if raw.startswith(b"\xff\xfe") and not raw.startswith(b"\xff\xfe\x00\x00"):
        decoded = _try_strict_decode(raw, "utf-16")
        if decoded is not None:
            return decoded, "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        decoded = _try_strict_decode(raw, "utf-16")
        if decoded is not None:
            return decoded, "utf-16-be"

    # 1c. ISO-2022-JP uses 7-bit ESC sequences that would otherwise pass ASCII/UTF-8
    if any(marker in raw for marker in _ISO2022_MARKERS):
        decoded = _try_strict_decode(raw, "iso2022_jp")
        if decoded is not None:
            return decoded, "iso2022_jp"

    # 2. BOM-less UTF-16 LE / BE via null-byte heuristic (before strict UTF-8)
    utf16_guess = _looks_like_utf16_no_bom(raw)
    if utf16_guess is not None:
        decoded = _try_strict_decode(raw, utf16_guess)
        if decoded is not None:
            return decoded, utf16_guess

    # 1d. Strict UTF-8 (when bytes don't look like BOM-less UTF-16)
    decoded_utf8 = _try_strict_decode(raw, "utf-8")
    if decoded_utf8 is not None:
        return decoded_utf8, "utf-8"

    # 3 & 4. Japanese legacy encodings: CP932 / Shift-JIS, EUC-JP, ISO-2022-JP
    jp_candidates: list[Tuple[str, str, int]] = []
    for enc in ("cp932", "shift_jis", "euc_jp", "iso2022_jp"):
        decoded = _try_strict_decode(raw, enc)
        if decoded is not None and not _has_cp932_undefined_pua(decoded):
            jp_candidates.append((enc, decoded, _japanese_naturalness_score(decoded)))

    if jp_candidates:
        # Pick the highest naturalness score; on ties preserve priority order
        # (cp932 > shift_jis > euc_jp > iso2022_jp).
        best_enc, best_text, _ = max(jp_candidates, key=lambda item: item[2])
        return best_text, best_enc

    # 5. CP1252 strict, then Latin-1 fallback with errors="replace" as last resort
    decoded_cp1252 = _try_strict_decode(raw, "cp1252")
    if decoded_cp1252 is not None:
        return decoded_cp1252, "cp1252"

    return raw.decode("latin-1", errors="replace"), "latin-1"


def read_text_with_encoding(path: Union[str, Path]) -> Tuple[str, str]:
    """Read a source or document file and decode it with legacy encoding detection.

    Args:
        path: File path to read.

    Returns:
        A ``(text, encoding)`` tuple from :func:`decode_legacy_bytes`.
    """
    raw = Path(path).read_bytes()
    return decode_legacy_bytes(raw)
