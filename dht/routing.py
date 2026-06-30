"""node_id 工具、紧凑节点编解码、有界节点表。"""
import os
from collections import deque

ID_SIZE = 20


def random_id() -> bytes:
    return os.urandom(ID_SIZE)


def distance(a: bytes, b: bytes) -> int:
    return int.from_bytes(a, "big") ^ int.from_bytes(b, "big")


def neighbor(target: bytes, self_id: bytes, prefix: int = 6) -> bytes:
    """生成与 target 共享前 prefix 字节的 id（水平扩散：更容易被对端收进路由表）。"""
    return target[:prefix] + self_id[prefix:]


def decode_nodes(data: bytes):
    """紧凑 nodes：每 26 字节 = 20 id + 4 ip + 2 port。"""
    nodes = []
    for i in range(0, len(data) - 25, 26):
        chunk = data[i:i + 26]
        nid = chunk[:20]
        ip = ".".join(str(x) for x in chunk[20:24])
        port = int.from_bytes(chunk[24:26], "big")
        if port == 0:
            continue
        nodes.append((nid, ip, port))
    return nodes


def encode_nodes(nodes) -> bytes:
    """nodes: 可迭代 (nid, ip, port)。"""
    out = []
    for nid, ip, port in nodes:
        try:
            ipb = bytes(int(x) for x in ip.split("."))
        except ValueError:
            continue
        if len(ipb) != 4:
            continue
        out.append(nid + ipb + port.to_bytes(2, "big"))
    return b"".join(out)


def decode_peers(values):
    """compact peer info：每 6 字节 = 4 ip + 2 port。values 为 bytes 列表。"""
    peers = []
    for v in values or []:
        if not isinstance(v, (bytes, bytearray)) or len(v) < 6:
            continue
        ip = ".".join(str(x) for x in v[:4])
        port = int.from_bytes(v[4:6], "big")
        if port:
            peers.append((ip, port))
    return peers


class KTable:
    """有界、去重的节点表，FIFO 消费。

    去重 + 从队首取节点，保证持续向**新**节点扩散，避免在单个"自我宣告"的
    坏节点上打转（否则会形成正反馈，整张表被一个节点占满，无法 fan-out）。
    """

    def __init__(self, maxsize: int):
        self.maxsize = maxsize
        self._dq = deque()
        self._seen = set()   # 当前在表中的 (ip, port)，用于去重

    def add(self, node):
        nid, ip, port = node
        if port <= 0 or port > 65535:
            return
        key = (ip, port)
        if key in self._seen:
            return
        self._seen.add(key)
        self._dq.append(node)
        if len(self._dq) > self.maxsize:
            old = self._dq.popleft()
            self._seen.discard((old[1], old[2]))

    def __len__(self):
        return len(self._dq)

    def pop_batch(self, n):
        """从队首取出 n 个节点（取出即移出表，避免紧密重复查询同一节点）。"""
        out = []
        for _ in range(min(n, len(self._dq))):
            nid, ip, port = self._dq.popleft()
            self._seen.discard((ip, port))
            out.append((nid, ip, port))
        return out
