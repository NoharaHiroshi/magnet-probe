"""metadata worker：消费 info_hash 队列，并行向多个 peer 拉取元数据并入库。"""
import asyncio
import logging

from bencode import bdecode, to_hex
from dht import lookup
from . import wire

log = logging.getLogger("meta")


def parse_metadata(info_hash: bytes, raw: bytes):
    """把 info 字典解析成存储记录。"""
    info = bdecode(raw)

    def dec(b, default=""):
        if isinstance(b, bytes):
            try:
                return b.decode("utf-8")
            except UnicodeDecodeError:
                return b.decode("utf-8", "replace")
        return default

    name = dec(info.get(b"name.utf-8") or info.get(b"name"))
    files = []
    total = 0
    if b"files" in info:  # 多文件
        for f in info[b"files"]:
            length = f.get(b"length", 0) or 0
            path_parts = f.get(b"path.utf-8") or f.get(b"path") or []
            path = "/".join(dec(p) for p in path_parts)
            files.append({"path": path, "length": length})
            total += length
    else:  # 单文件
        total = info.get(b"length", 0) or 0
        files.append({"path": name, "length": total})

    return {
        "infohash": to_hex(info_hash),
        "name": name,
        "length": int(total),
        "file_count": len(files),
        "files": files[:1000],  # 防止超大文件列表
    }


async def _try_peers(info_hash, peers, peer_concurrency, peer_timeout):
    """并行尝试多个 peer，首个成功即返回 raw metadata，其余取消。"""
    peers = peers[:max(peer_concurrency, 1)]
    tasks = [asyncio.ensure_future(wire.fetch_metadata(ip, port, info_hash, peer_timeout))
             for ip, port in peers]
    raw = None
    try:
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for d in done:
                try:
                    raw = d.result()
                    return raw
                except Exception:
                    continue
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    return raw


async def worker(sink, store, cfg):
    while True:
        info_hash, peer_addr = await sink.get()
        hexhash = to_hex(info_hash)
        try:
            if await store.exists(hexhash):
                await store.bump_hot(hexhash)
                continue

            peers = []
            if peer_addr:
                peers.append(peer_addr)
            if len(peers) < cfg.PEER_CONCURRENCY:
                try:
                    found = await asyncio.wait_for(
                        lookup.find_peers(info_hash, cfg.BOOTSTRAP_NODES,
                                          timeout=min(cfg.META_TIMEOUT, 8), limit=20),
                        cfg.META_TIMEOUT,
                    )
                    peers.extend(found)
                except (asyncio.TimeoutError, Exception):
                    pass
            if not peers:
                continue

            raw = await asyncio.wait_for(
                _try_peers(info_hash, peers, cfg.PEER_CONCURRENCY, cfg.PEER_TIMEOUT),
                cfg.META_TIMEOUT,
            )
            if not raw:
                continue
            record = parse_metadata(info_hash, raw)
            if not record["name"]:
                continue
            await store.save(record)
            log.info("saved %s  %s  (%d files)", hexhash[:12], record["name"][:60],
                     record["file_count"])
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            log.debug("fetch %s failed: %s", hexhash[:12], e)


async def run_workers(sink, store, cfg):
    tasks = [asyncio.ensure_future(worker(sink, store, cfg)) for _ in range(cfg.WORKERS)]
    return tasks
