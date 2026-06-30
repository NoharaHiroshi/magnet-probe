"""磁力探测工具入口。

  python main.py crawl    # 启动 DHT 嗅探 + metadata 下载（持续写入 MongoDB）
  python main.py web      # 启动搜索 Web 服务
"""
import argparse
import asyncio
import logging
import signal

import config

# 可选 uvloop 加速
try:
    import uvloop
    uvloop.install()
except Exception:
    pass


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


async def _resolve_bootstrap(log):
    """把 BOOTSTRAP_NODES 的主机名解析为 IPv4，原地替换（兼容 uvloop UDP）。"""
    import socket
    loop = asyncio.get_event_loop()
    resolved = []
    for host, port in config.BOOTSTRAP_NODES:
        try:
            infos = await loop.getaddrinfo(host, port, family=socket.AF_INET,
                                           type=socket.SOCK_DGRAM)
            ip = infos[0][4][0]
            resolved.append((ip, port))
        except Exception as e:
            log.warning("resolve %s failed: %s", host, e)
    if resolved:
        config.BOOTSTRAP_NODES = resolved
    log.info("bootstrap nodes: %s", config.BOOTSTRAP_NODES)


async def run_crawl():
    from dht.crawler import InfoHashSink, run_crawler
    from metadata.fetcher import run_workers
    from storage.db import Store

    log = logging.getLogger("main")
    store = Store(config.MONGO_URI, config.MONGO_DB, config.CACHE_TTL, config.CACHE_SIZE)
    await store.ensure_indexes()
    log.info("connected to MongoDB %s/%s", config.MONGO_URI, config.MONGO_DB)

    # uvloop 的 UDP sendto 不做 DNS 解析，先把 bootstrap 主机名解析成 IP
    await _resolve_bootstrap(log)

    sink = InfoHashSink(config.QUEUE_MAX)
    nodes, dht_tasks, seen = await run_crawler(
        sink, config.DHT_PORTS, config.DHT_HOST, config.BOOTSTRAP_NODES,
        config.MAX_NODES, config.FIND_INTERVAL, config.MAX_PER_SECOND,
        config.SEEN_CAPACITY,
    )
    worker_tasks = await run_workers(sink, store, config)

    stop = asyncio.Event()

    def _stop(*_):
        stop.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

    # 周期性打印统计
    async def reporter():
        while not stop.is_set():
            await asyncio.sleep(10)
            log.info("found=%d dropped=%d high=%d low=%d",
                     sink.found, sink.dropped, sink.high.qsize(), sink.low.qsize())

    rep = asyncio.ensure_future(reporter())
    await stop.wait()
    log.info("shutting down...")
    for t in dht_tasks + worker_tasks + [rep]:
        t.cancel()
    store.close()


def run_web():
    import uvicorn
    uvicorn.run("web.server:app", host=config.WEB_HOST, port=config.WEB_PORT, log_level="info")


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="磁力探测工具 (DHT crawler)")
    parser.add_argument("command", choices=["crawl", "web"], help="crawl=爬虫, web=搜索服务")
    args = parser.parse_args()
    if args.command == "crawl":
        asyncio.run(run_crawl())
    else:
        run_web()


if __name__ == "__main__":
    main()
