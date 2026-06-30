"""针对单个 info_hash 的 get_peers 迭代查找，返回可用 peer 地址列表。

供 metadata 下载器在没有 announce 源地址时补充 peer 来源。
"""
import asyncio
import os
import struct

from bencode import bencode, bdecode
from . import routing


def _get_peers_query(self_id: bytes, info_hash: bytes) -> bytes:
    return bencode({
        b"t": os.urandom(2),
        b"y": b"q",
        b"q": b"get_peers",
        b"a": {b"id": self_id, b"info_hash": info_hash},
    })


class _LookupProto(asyncio.DatagramProtocol):
    def __init__(self, info_hash, self_id):
        self.info_hash = info_hash
        self.self_id = self_id
        self.peers = set()
        self.new_nodes = []
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            msg = bdecode(data)
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        r = msg.get(b"r")
        if not isinstance(r, dict):
            return
        values = r.get(b"values")
        if values:
            for ip, port in routing.decode_peers(values):
                self.peers.add((ip, port))
        nodes = r.get(b"nodes")
        if nodes:
            self.new_nodes.extend(routing.decode_nodes(nodes))


async def find_peers(info_hash: bytes, bootstrap, timeout=8.0, limit=30):
    """迭代 get_peers，最多 timeout 秒，收集到 limit 个 peer 即返回。"""
    loop = asyncio.get_event_loop()
    self_id = routing.neighbor(info_hash, routing.random_id(), 4)
    transport, proto = await loop.create_datagram_endpoint(
        lambda: _LookupProto(info_hash, self_id), local_addr=("0.0.0.0", 0)
    )
    try:
        # 初始查询 bootstrap
        for host, port in bootstrap:
            try:
                proto.transport.sendto(_get_peers_query(self_id, info_hash), (host, port))
            except OSError:
                pass

        deadline = loop.time() + timeout
        queried = set()
        while loop.time() < deadline and len(proto.peers) < limit:
            await asyncio.sleep(0.2)
            pending = proto.new_nodes
            proto.new_nodes = []
            for nid, ip, port in pending:
                key = (ip, port)
                if key in queried:
                    continue
                queried.add(key)
                try:
                    proto.transport.sendto(_get_peers_query(self_id, info_hash), (ip, port))
                except OSError:
                    pass
                if len(queried) > 200:
                    break
        return list(proto.peers)[:limit]
    finally:
        transport.close()
