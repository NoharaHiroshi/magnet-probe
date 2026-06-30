"""集中配置。所有项均可由环境变量覆盖（前缀 MP_）。"""
import os


def _int(name, default):
    return int(os.environ.get(name, default))


def _str(name, default):
    return os.environ.get(name, default)


def _ports(name, default):
    raw = os.environ.get(name)
    if not raw:
        return default
    return [int(p) for p in raw.split(",") if p.strip()]


# ---- DHT 嗅探 ----
DHT_HOST = _str("MP_DHT_HOST", "0.0.0.0")
# 多端口 = 多 node_id 实例，覆盖更多 keyspace，提高探测效率
DHT_PORTS = _ports("MP_DHT_PORTS", [6881, 6882])
BOOTSTRAP_NODES = [
    ("router.bittorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("router.utorrent.com", 6881),
]
MAX_NODES = _int("MP_MAX_NODES", 2000)          # 每实例节点表上限（控内存）
FIND_INTERVAL = float(_str("MP_FIND_INTERVAL", "0.003"))  # auto_find 间隔（令牌桶节流）
MAX_PER_SECOND = _int("MP_MAX_PER_SECOND", 600)  # 每实例每秒最多主动外发查询

# ---- 去重 ----
SEEN_CAPACITY = _int("MP_SEEN_CAPACITY", 200000)  # 进程内 LRU 容量

# ---- metadata 下载 ----
WORKERS = _int("MP_WORKERS", 50)                  # metadata worker 数
PEER_CONCURRENCY = _int("MP_PEER_CONCURRENCY", 4)  # 每 infohash 并行尝试 peer 数
META_TIMEOUT = float(_str("MP_META_TIMEOUT", "15"))  # 单 infohash 总超时(s)
PEER_TIMEOUT = float(_str("MP_PEER_TIMEOUT", "5"))   # 单 peer 连接/握手超时(s)
QUEUE_MAX = _int("MP_QUEUE_MAX", 5000)            # 有界队列容量（背压）

# ---- MongoDB ----
MONGO_URI = _str("MP_MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = _str("MP_MONGO_DB", "magnet_probe")

# ---- 查询缓存 ----
CACHE_TTL = float(_str("MP_CACHE_TTL", "30"))     # 搜索结果缓存秒数
CACHE_SIZE = _int("MP_CACHE_SIZE", 256)

# ---- Web ----
WEB_HOST = _str("MP_WEB_HOST", "127.0.0.1")
WEB_PORT = _int("MP_WEB_PORT", 8080)
