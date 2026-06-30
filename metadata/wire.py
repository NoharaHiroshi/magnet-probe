"""BitTorrent peer wire 协议 + BEP-10 扩展握手 + BEP-9 (ut_metadata) 元数据下载。

对单个 peer：TCP 握手 -> 扩展握手 -> 分片请求 metadata -> 拼接 -> SHA1 校验 -> bdecode。
"""
import asyncio
import os
import struct

from bencode import bencode, bdecode_all, sha1

BT_PROTOCOL = b"BitTorrent protocol"
BLOCK = 16 * 1024
MAX_METADATA = 5 * 1024 * 1024  # 5MB 上限，防御异常 metadata_size

# msg_type in ut_metadata
UT_REQUEST = 0
UT_DATA = 1
UT_REJECT = 2


def _handshake(info_hash: bytes) -> bytes:
    reserved = bytearray(8)
    reserved[5] |= 0x10  # 支持扩展协议 (BEP-10)
    peer_id = os.urandom(20)
    return (bytes([len(BT_PROTOCOL)]) + BT_PROTOCOL + bytes(reserved)
            + info_hash + peer_id)


def _ext_handshake() -> bytes:
    payload = bencode({b"m": {b"ut_metadata": 1}})
    body = b"\x14\x00" + payload  # 20=extended, 0=handshake
    return struct.pack(">I", len(body)) + body


def _ext_request(ut_metadata_id: int, piece: int) -> bytes:
    payload = bencode({b"msg_type": UT_REQUEST, b"piece": piece})
    body = bytes([20, ut_metadata_id]) + payload
    return struct.pack(">I", len(body)) + body


async def _read_exactly(reader, n, timeout):
    return await asyncio.wait_for(reader.readexactly(n), timeout)


async def _read_message(reader, timeout):
    """读一条 peer wire 消息，返回 payload（不含 4 字节长度前缀）。keep-alive 返回 b''。"""
    header = await _read_exactly(reader, 4, timeout)
    (length,) = struct.unpack(">I", header)
    if length == 0:
        return b""
    if length > MAX_METADATA + 1024:
        raise ValueError("message too large")
    return await _read_exactly(reader, length, timeout)


async def fetch_metadata(ip: str, port: int, info_hash: bytes, timeout: float) -> bytes:
    """从单个 peer 下载并校验 metadata（info 字典的原始 bencode bytes）。失败抛异常。"""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    try:
        # 1) BT 握手
        writer.write(_handshake(info_hash))
        await writer.drain()
        resp = await _read_exactly(reader, 68, timeout)
        if resp[1:20] != BT_PROTOCOL:
            raise ValueError("not a bittorrent peer")
        if not (resp[20 + 5] & 0x10):
            raise ValueError("peer has no extension support")
        if resp[28:48] != info_hash:
            raise ValueError("info_hash mismatch")

        # 2) 扩展握手
        writer.write(_ext_handshake())
        await writer.drain()

        ut_metadata_id = None
        metadata_size = None
        # 循环读消息直到拿到对端扩展握手
        for _ in range(20):
            payload = await _read_message(reader, timeout)
            if len(payload) < 2 or payload[0] != 20:
                continue
            if payload[1] == 0:  # 扩展握手
                info, _ = bdecode_all(payload[2:])
                m = info.get(b"m") or {}
                ut_metadata_id = m.get(b"ut_metadata")
                metadata_size = info.get(b"metadata_size")
                break
        if not ut_metadata_id or not metadata_size:
            raise ValueError("peer does not serve ut_metadata")
        if metadata_size <= 0 or metadata_size > MAX_METADATA:
            raise ValueError("bad metadata_size")

        num_pieces = (metadata_size + BLOCK - 1) // BLOCK
        pieces = [None] * num_pieces

        # 3) 请求所有分片
        for i in range(num_pieces):
            writer.write(_ext_request(ut_metadata_id, i))
        await writer.drain()

        # 4) 收集 data 分片
        received = 0
        guard = 0
        while received < num_pieces and guard < num_pieces * 4 + 20:
            guard += 1
            payload = await _read_message(reader, timeout)
            if len(payload) < 2 or payload[0] != 20 or payload[1] != ut_metadata_id:
                continue
            header, consumed = bdecode_all(payload[2:])
            if header.get(b"msg_type") != UT_DATA:
                continue
            idx = header.get(b"piece")
            data = payload[2 + consumed:]
            if isinstance(idx, int) and 0 <= idx < num_pieces and pieces[idx] is None:
                pieces[idx] = data
                received += 1

        if any(p is None for p in pieces):
            raise ValueError("incomplete metadata")

        metadata = b"".join(pieces)[:metadata_size]
        if sha1(metadata) != info_hash:
            raise ValueError("metadata sha1 mismatch")
        return metadata
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1)
        except Exception:
            pass
