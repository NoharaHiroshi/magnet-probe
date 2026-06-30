"""MongoDB 存储：去重 upsert + 分词索引（模糊查询）+ 多维排序 + 查询缓存。"""
import re
import time
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

# 拉丁/数字 run、CJK run（中日韩）
_LATIN = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]+")
_MAX_KEYWORDS = 64

# 排序字段映射：(field, default_direction)
_SORT_FIELDS = {
    "relevance": ("hot", -1),   # 以热度作为相关度代理
    "hot": ("hot", -1),
    "size": ("length", -1),
    "date": ("created_at", -1),
}


def tokenize(text: str):
    """拉丁按词切分；CJK 用 bi-gram 二元切分。供入库与查询共用，保证口径一致。"""
    if not text:
        return []
    text = text.lower()
    tokens = set()
    for m in _LATIN.findall(text):
        if len(m) >= 2 or m.isdigit():
            tokens.add(m)
    for run in _CJK.findall(text):
        if len(run) == 1:
            tokens.add(run)
        else:
            for i in range(len(run) - 1):
                tokens.add(run[i:i + 2])
    return list(tokens)[:_MAX_KEYWORDS]


class _TTLCache:
    def __init__(self, ttl, size):
        self.ttl = ttl
        self.size = size
        self._d = {}

    def get(self, key):
        item = self._d.get(key)
        if not item:
            return None
        ts, val = item
        if time.monotonic() - ts > self.ttl:
            self._d.pop(key, None)
            return None
        return val

    def put(self, key, val):
        if len(self._d) >= self.size:
            # 淘汰最旧的
            oldest = min(self._d.items(), key=lambda kv: kv[1][0])[0]
            self._d.pop(oldest, None)
        self._d[key] = (time.monotonic(), val)

    def clear(self):
        self._d.clear()


class Store:
    def __init__(self, uri, dbname, cache_ttl=30, cache_size=256):
        self.client = AsyncIOMotorClient(uri)
        self.col = self.client[dbname]["torrents"]
        self.cache = _TTLCache(cache_ttl, cache_size)

    async def ensure_indexes(self):
        await self.col.create_index("keywords")            # 模糊查询主力（multikey）
        await self.col.create_index([("name", "text")])    # 整词相关度（可选路径）
        await self.col.create_index([("hot", -1)])
        await self.col.create_index([("length", -1)])
        await self.col.create_index([("created_at", -1)])

    async def exists(self, infohash_hex: str) -> bool:
        return await self.col.count_documents({"_id": infohash_hex}, limit=1) > 0

    async def bump_hot(self, infohash_hex: str):
        await self.col.update_one(
            {"_id": infohash_hex},
            {"$inc": {"hot": 1}, "$set": {"updated_at": datetime.now(timezone.utc)}},
        )

    async def save(self, record: dict):
        now = datetime.now(timezone.utc)
        ih = record["infohash"]
        doc = {
            "name": record["name"],
            "name_lower": record["name"].lower(),
            "keywords": tokenize(record["name"]),
            "length": record["length"],
            "file_count": record["file_count"],
            "files": record["files"],
            "updated_at": now,
        }
        await self.col.update_one(
            {"_id": ih},
            {
                "$set": doc,
                "$setOnInsert": {"created_at": now},
                "$inc": {"hot": 1},
            },
            upsert=True,
        )
        self.cache.clear()  # 数据变化使缓存失效

    async def search(self, q: str, sort="relevance", order="desc", page=1, size=20):
        page = max(1, int(page))
        size = max(1, min(100, int(size)))
        sort = sort if sort in _SORT_FIELDS else "relevance"
        direction = -1 if order == "desc" else 1

        cache_key = (q, sort, order, page, size)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if q:
            tokens = tokenize(q)
            if tokens:
                query = {"keywords": {"$all": tokens}}
            else:
                # 无可分词内容（如单个符号），退回前缀正则
                query = {"name_lower": {"$regex": "^" + re.escape(q.lower())}}
        else:
            query = {}

        field, default_dir = _SORT_FIELDS[sort]
        sort_dir = direction if order in ("asc", "desc") else default_dir

        cursor = self.col.find(
            query,
            projection={"name": 1, "length": 1, "file_count": 1, "hot": 1,
                        "created_at": 1, "files": 1},
        ).sort(field, sort_dir).skip((page - 1) * size).limit(size)

        items = []
        async for d in cursor:
            items.append({
                "infohash": d["_id"],
                "name": d.get("name", ""),
                "length": d.get("length", 0),
                "file_count": d.get("file_count", 0),
                "hot": d.get("hot", 0),
                "created_at": (d.get("created_at") or datetime.now(timezone.utc)).isoformat(),
                "files": d.get("files", [])[:50],
            })
        result = {"total_estimate": len(items), "page": page, "size": size, "items": items}
        self.cache.put(cache_key, result)
        return result

    async def recent(self, limit=20):
        return await self.search("", sort="date", order="desc", page=1, size=limit)

    async def stats(self):
        total = await self.col.estimated_document_count()
        return {"total": total}

    def close(self):
        self.client.close()
