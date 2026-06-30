"""DHT 嗅探器：加入 DHT 网络，被动收集 get_peers/announce_peer 中的 info_hash。

设计要点（对应"提高探测效率"）：
  - 每个 UDP 端口一个 DHTNode 实例，独立随机 node_id，覆盖更多 keyspace；
  - 回 find_node/get_peers 时使用"邻居 id"扩大被收录概率，吸引更多查询流量；
  - 进程内 LRU 提前去重，重复 info_hash 不入队；
  - announce_peer（自带 peer 地址）走高优先级队列，优先下载元数据。
"""
import asyncio
import logging
import time
from collections import OrderedDict

from . import krpc, routing

log = logging.getLogger("dht")


class LRUSet:
    """有界去重集合（近似 LRU）。"""

    def __init__(self, capacity):
        self.capacity = capacity
        self._d = OrderedDict()

    def add_if_absent(self, key) -> bool:
        """不存在则加入并返回 True；已存在返回 False。"""
        if key in self._d:
            self._d.move_to_end(key)
            return False
        self._d[key] = None
        if len(self._d) > self.capacity:
            self._d.popitem(last=False)
        return True


class TokenBucket:
    def __init__(self, rate_per_sec):
        self.rate = max(1, rate_per_sec)
        self.allowance = self.rate
        self.last = time.monotonic()

    def take(self) -> bool:
        now = time.monotonic()
        self.allowance += (now - self.last) * self.rate
        self.last = now
        if self.allowance > self.rate:
            self.allowance = self.rate
        if self.allowance < 1.0:
            return False
        self.allowance -= 1.0
        return True


class InfoHashSink:
    """双优先级队列：announce 源（带 peer 地址）优先于纯 get_peers。"""

    def __init__(self, maxsize):
        self.high = asyncio.Queue(maxsize=maxsize)
        self.low = asyncio.Queue(maxsize=maxsize)
        self.found = 0
        self.dropped = 0

    def push(self, info_hash: bytes, peer_addr):
        q = self.high if peer_addr else self.low
        try:
            q.put_nowait((info_hash, peer_addr))
            self.found += 1
        except asyncio.QueueFull:
            self.dropped += 1

    async def get(self):
        """优先取 high，否则取 low；都空则等待任一就绪。"""
        if not self.high.empty():
            return self.high.get_nowait()
        if not self.low.empty():
            return self.low.get_nowait()
        get_high = asyncio.ensure_future(self.high.get())
        get_low = asyncio.ensure_future(self.low.get())
        done, pending = await asyncio.wait(
            {get_high, get_low}, return_when=asyncio.FIRST_COMPLETED
        )
        result = None
        for fut in done:
            if result is None:
                result = fut.result()
            else:  # 两个同时完成，归还一个
                item = fut.result()
                target = self.high if fut is get_high else self.low
                target.put_nowait(item)
        for fut in pending:
            fut.cancel()
        return result


class DHTNode(asyncio.DatagramProtocol):
    def __init__(self, sink: InfoHashSink, seen: LRUSet, table: routing.KTable, bucket: TokenBucket):
        self.self_id = routing.random_id()
        self.sink = sink
        self.seen = seen
        self.table = table
        self.bucket = bucket
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def send(self, data, addr):
        try:
            self.transport.sendto(data, addr)
        except OSError:
            pass

    def bootstrap(self, nodes):
        for host, port in nodes:
            self.send(krpc.query_find_node(self.self_id, routing.random_id()), (host, port))

    def datagram_received(self, data, addr):
        msg = krpc.parse(data)
        if not msg:
            return
        y = msg.get(b"y")
        if y == b"r":
            self._on_response(msg)
        elif y == b"q":
            self._on_query(msg, addr)

    # ---- 处理应答（主要为 find_node 返回的 nodes）----
    def _on_response(self, msg):
        r = msg.get(b"r") or {}
        nodes = r.get(b"nodes")
        if nodes:
            for node in routing.decode_nodes(nodes):
                self.table.add(node)

    # ---- 处理查询 ----
    def _on_query(self, msg, addr):
        try:
            q = msg.get(b"q")
            t = msg.get(b"t", b"")
            a = msg.get(b"a") or {}
            if q == b"ping":
                self.send(krpc.resp_ping(t, self.self_id), addr)
            elif q == b"find_node":
                self.send(krpc.resp_find_node(t, self.self_id, b""), addr)
            elif q == b"get_peers":
                ih = a.get(b"info_hash")
                token = ih[:2] if ih else b"mp"
                self.send(krpc.resp_get_peers(t, self.self_id, token, b""), addr)
                self._collect(ih, None)
            elif q == b"announce_peer":
                ih = a.get(b"info_hash")
                self.send(krpc.resp_announce(t, self.self_id), addr)
                # implied_port=1 时用源端口，否则用 announce 的 port
                if a.get(b"implied_port"):
                    peer = (addr[0], addr[1])
                else:
                    port = a.get(b"port")
                    peer = (addr[0], int(port)) if port else None
                self._collect(ih, peer)
        except Exception as e:  # 单条报文异常不应影响整体
            log.debug("query handle error: %s", e)

    def _collect(self, info_hash, peer_addr):
        if not info_hash or len(info_hash) != 20:
            return
        # 同一 hash 反复出现 → 仅当首次时入队；重复说明热度高，但下游已处理
        if self.seen.add_if_absent(info_hash):
            self.sink.push(info_hash, peer_addr)
        elif peer_addr:
            # 已见过但这次带来了 peer 地址，仍尝试补一个高优先级源
            self.sink.push(info_hash, peer_addr)

    async def auto_find(self, interval):
        """持续向**新**节点发 find_node，向 keyspace 扩散、维持曝光。"""
        while True:
            await asyncio.sleep(interval)
            nodes = self.table.pop_batch(8)
            if not nodes:
                self.bootstrap_self()
                continue
            for nid, ip, port in nodes:
                if not self.bucket.take():   # 令牌桶按"包"计数
                    break
                target = routing.random_id()
                # 用邻居 id 提升被对端收录的概率
                self.self_id = routing.neighbor(nid, self.self_id, 6)
                self.send(krpc.query_find_node(self.self_id, target), (ip, port))

    def bootstrap_self(self):
        from config import BOOTSTRAP_NODES
        self.bootstrap(BOOTSTRAP_NODES)


async def run_crawler(sink: InfoHashSink, ports, host, bootstrap, max_nodes,
                      find_interval, max_per_second, seen_capacity):
    """在多个端口启动 DHTNode，返回 (nodes, tasks) 以便统一管理。"""
    loop = asyncio.get_event_loop()
    seen = LRUSet(seen_capacity)
    nodes = []
    tasks = []
    for port in ports:
        table = routing.KTable(max_nodes)
        bucket = TokenBucket(max_per_second)
        transport, proto = await loop.create_datagram_endpoint(
            lambda t=table, b=bucket: DHTNode(sink, seen, t, b),
            local_addr=(host, port),
        )
        proto.bootstrap(bootstrap)
        tasks.append(asyncio.ensure_future(proto.auto_find(find_interval)))
        nodes.append(proto)
        log.info("DHT node listening on %s:%d", host, port)
    return nodes, tasks, seen
