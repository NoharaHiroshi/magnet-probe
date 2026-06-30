"""轻量 bencode 编解码 + 常用工具。无第三方依赖。

解码默认把字符串保持为 bytes（torrent/KRPC 二进制安全）。
"""
import hashlib


class BencodeError(ValueError):
    pass


def bdecode(data: bytes):
    """解码 bencode；返回 (obj)。多余尾字节会被忽略。"""
    if not isinstance(data, (bytes, bytearray)):
        raise BencodeError("bdecode expects bytes")
    obj, _ = _decode(bytes(data), 0)
    return obj


def bdecode_all(data: bytes):
    """解码并返回 (obj, consumed_length)。"""
    return _decode(bytes(data), 0)


def _decode(data, i):
    if i >= len(data):
        raise BencodeError("unexpected end of data")
    c = data[i:i + 1]
    if c == b"i":
        end = data.index(b"e", i)
        return int(data[i + 1:end]), end + 1
    if c.isdigit():
        colon = data.index(b":", i)
        length = int(data[i:colon])
        start = colon + 1
        end = start + length
        if end > len(data):
            raise BencodeError("string length out of range")
        return data[start:end], end
    if c == b"l":
        i += 1
        out = []
        while data[i:i + 1] != b"e":
            v, i = _decode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while data[i:i + 1] != b"e":
            k, i = _decode(data, i)
            v, i = _decode(data, i)
            out[k] = v
        return out, i + 1
    raise BencodeError("invalid bencode prefix %r at %d" % (c, i))


def bencode(obj) -> bytes:
    out = []
    _encode(obj, out)
    return b"".join(out)


def _encode(obj, out):
    if isinstance(obj, bool):
        raise BencodeError("bool not supported")
    if isinstance(obj, int):
        out.append(b"i%de" % obj)
    elif isinstance(obj, bytes):
        out.append(b"%d:" % len(obj))
        out.append(obj)
    elif isinstance(obj, str):
        b = obj.encode("utf-8")
        out.append(b"%d:" % len(b))
        out.append(b)
    elif isinstance(obj, (list, tuple)):
        out.append(b"l")
        for v in obj:
            _encode(v, out)
        out.append(b"e")
    elif isinstance(obj, dict):
        out.append(b"d")
        # bencode 要求 key 按字典序
        for k in sorted(obj.keys(), key=lambda x: x if isinstance(x, bytes) else x.encode()):
            kb = k if isinstance(k, bytes) else k.encode("utf-8")
            out.append(b"%d:" % len(kb))
            out.append(kb)
            _encode(obj[k], out)
        out.append(b"e")
    else:
        raise BencodeError("cannot bencode %r" % type(obj))


# ---- 工具 ----
def sha1(data: bytes) -> bytes:
    return hashlib.sha1(data).digest()


def to_hex(b: bytes) -> str:
    return b.hex()


def from_hex(s: str) -> bytes:
    return bytes.fromhex(s)
