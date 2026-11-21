
from typing import List, Optional, Dict, Any
import json
import logging
import asyncio
import numpy as np
from ..vector_store import VectorStore, VectorRow

logger = logging.getLogger("vector_store.valkey")

class ValkeyVectorStore(VectorStore):
    def __init__(self, url: str, prefix: str = "om:vec:"):
        self.url = url
        self.prefix = prefix
        self.client = None

    async def _get_client(self):
        import redis.asyncio as redis
        if not self.client:
            self.client = redis.from_url(self.url)
        return self.client

    def _key(self, id: str, sector: Optional[str] = None) -> str:
        if sector:
            return f"{self.prefix}{id}:{sector}"
        return f"{self.prefix}{id}"

    @staticmethod
    def _parse_row(data: Dict[Any, Any]) -> Optional[VectorRow]:
        if not data:
            return None

        def dec(x):
            return x.decode("utf-8") if isinstance(x, bytes) else str(x)

        vec_bytes = data.get(b"v") or data.get("v")
        if vec_bytes is None:
            return None
        vec = [float(x) for x in np.frombuffer(vec_bytes, dtype=np.float32)]
        dim_raw = data.get(b"dim") or data.get("dim")
        dim = int(dec(dim_raw)) if dim_raw is not None else len(vec)
        return VectorRow(
            dec(data.get(b"id") or data.get("id")),
            dec(data.get(b"sector") or data.get("sector")),
            vec,
            dim,
        )

    async def storeVector(self, id: str, sector: str, vector: List[float], dim: int, user_id: Optional[str] = None):
        client = await self._get_client()
        key = self._key(id, sector)
        vec_bytes = np.array(vector, dtype=np.float32).tobytes()

        mapping = {
            "id": id,
            "sector": sector,
            "dim": dim,
            "v": vec_bytes,
            "user_id": user_id or ""
        }
        await client.hset(key, mapping=mapping)

    async def getVectorsById(self, id: str) -> List[VectorRow]:
        client = await self._get_client()
        cursor = 0
        rows_by_sector: Dict[str, VectorRow] = {}

        while True:
            cursor, keys = await client.scan(cursor, match=f"{self.prefix}{id}:*", count=100)
            if keys:
                pipe = client.pipeline()
                for key in keys:
                    pipe.hgetall(key)
                items = await pipe.execute()
                for item in items:
                    row = self._parse_row(item)
                    if row is not None and (not row.id or row.id == id):
                        rows_by_sector[row.sector] = row
            if cursor == 0:
                break

        legacy_data = await client.hgetall(self._key(id))
        legacy_row = self._parse_row(legacy_data)
        if legacy_row is not None and legacy_row.sector not in rows_by_sector:
            rows_by_sector[legacy_row.sector] = legacy_row

        return list(rows_by_sector.values())

    async def getVector(self, id: str, sector: str) -> Optional[VectorRow]:
        client = await self._get_client()
        data = await client.hgetall(self._key(id, sector))
        row = self._parse_row(data)
        if row is not None:
            return row

        legacy_data = await client.hgetall(self._key(id))
        legacy_row = self._parse_row(legacy_data)
        if legacy_row is not None and legacy_row.sector == sector:
            return legacy_row
        return None

    async def getVectorsBySector(self, sector: str) -> List[VectorRow]:
        client = await self._get_client()
        cursor = 0
        res: List[VectorRow] = []

        def dec(x):
            return x.decode('utf-8') if isinstance(x, bytes) else str(x)

        while True:
            cursor, keys = await client.scan(cursor, match=f"{self.prefix}*", count=100)
            if keys:
                pipe = client.pipeline()
                for key in keys:
                    pipe.hgetall(key)
                items = await pipe.execute()

                for item in items:
                    if not item:
                        continue
                    i_sector = dec(item.get(b'sector') or item.get('sector'))
                    if i_sector != sector:
                        continue
                    vec_bytes = item.get(b'v') or item.get('v')
                    if vec_bytes is None:
                        continue
                    vec = [float(x) for x in np.frombuffer(vec_bytes, dtype=np.float32)]
                    dim_raw = item.get(b'dim') or item.get('dim')
                    dim = int(dec(dim_raw)) if dim_raw is not None else len(vec)
                    res.append(VectorRow(
                        dec(item.get(b'id') or item.get('id')),
                        i_sector,
                        vec,
                        dim
                    ))
            if cursor == 0:
                break

        return res

    async def deleteVectors(self, id: str):
        client = await self._get_client()
        keys_to_delete = [self._key(id)]
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor, match=f"{self.prefix}{id}:*", count=100)
            if keys:
                keys_to_delete.extend(keys)
            if cursor == 0:
                break
        if keys_to_delete:
            await client.delete(*keys_to_delete)

    async def search(self, vector: List[float], sector: str, k: int, filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:

        client = await self._get_client()
        query_vec = np.array(vector, dtype=np.float32)
        q_norm = np.linalg.norm(query_vec)

        cursor = 0
        results = []

        while True:
            cursor, keys = await client.scan(cursor, match=f"{self.prefix}*", count=100)
            if keys:
                pipe = client.pipeline()
                for key in keys:
                    pipe.hgetall(key)
                items = await pipe.execute()

                for item in items:
                    if not item: continue
                    def dec(x): return x.decode('utf-8') if isinstance(x, bytes) else str(x)

                    i_sector = dec(item.get(b'sector') or item.get('sector'))
                    if i_sector != sector: continue

                    if filter and filter.get("user_id"):
                        i_uid = dec(item.get(b'user_id') or item.get('user_id'))
                        target_uid = filter["user_id"]
                        if "%" in target_uid or "_" in target_uid:
                            import re
                            regex_pat = "^" + "".join(
                                ".*" if c == "%" else
                                "." if c == "_" else
                                re.escape(c)
                                for c in target_uid
                            ) + "$"
                            if not re.match(regex_pat, i_uid):
                                continue
                        elif i_uid != target_uid:
                            continue

                    v_bytes = item.get(b'v') or item.get('v')
                    v = np.frombuffer(v_bytes, dtype=np.float32)

                    dot = np.dot(query_vec, v)
                    norm = np.linalg.norm(v)
                    sim = dot / (q_norm * norm) if (q_norm * norm) > 0 else 0

                    results.append({
                        "id": dec(item.get(b'id') or item.get('id')),
                        "similarity": float(sim)
                    })

            if cursor == 0: break

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:k]
