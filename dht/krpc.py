"""KRPC（DHT over UDP）报文编解码与应答构造。"""
import os
from bencode import bencode, bdecode, BencodeError
from . import routing


def tid() -> bytes:
    return os.urandom(2)


# ---- 主动查询构造 ----
def query_find_node(self_id: bytes, target: bytes) -> bytes:
    return bencode({
        b"t": tid(),
        b"y": b"q",
        b"q": b"find_node",
        b"a": {b"id": self_id, b"target": target},
    })


def query_ping(self_id: bytes) -> bytes:
    return bencode({
        b"t": tid(),
        b"y": b"q",
        b"q": b"ping",
        b"a": {b"id": self_id},
    })


# ---- 应答构造 ----
def resp_ping(t: bytes, self_id: bytes) -> bytes:
    return bencode({b"t": t, b"y": b"r", b"r": {b"id": self_id}})


def resp_find_node(t: bytes, self_id: bytes, nodes: bytes) -> bytes:
    return bencode({b"t": t, b"y": b"r", b"r": {b"id": self_id, b"nodes": nodes}})


def resp_get_peers(t: bytes, self_id: bytes, token: bytes, nodes: bytes) -> bytes:
    # 不返回 peers，只回 nodes + token，对端即满意
    return bencode({
        b"t": t, b"y": b"r",
        b"r": {b"id": self_id, b"token": token, b"nodes": nodes},
    })


def resp_announce(t: bytes, self_id: bytes) -> bytes:
    return bencode({b"t": t, b"y": b"r", b"r": {b"id": self_id}})


def parse(data: bytes):
    """解析收到的报文，返回 dict 或 None。"""
    try:
        msg = bdecode(data)
    except (BencodeError, ValueError, IndexError):
        return None
    if not isinstance(msg, dict):
        return None
    return msg
