"""无需 MongoDB / 网络的纯逻辑单测：bencode、紧凑编解码、分词、magnet。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bencode import bencode, bdecode, bdecode_all, sha1, to_hex
from dht import routing
from storage.db import tokenize
from web.server import magnet


def test_bencode_roundtrip():
    cases = [
        b"d3:cow3:moo4:spam4:eggse",
        b"d4:infod6:lengthi1234e4:name9:ubuntu.isoee",
        b"li1ei2ei3ee",
        b"d1:ai0e1:b2:hieee"[:-1] + b"",  # noop, keep simple
    ]
    obj = {b"name": b"ubuntu", b"length": 700, b"files": [{b"path": [b"a", b"b"], b"length": 10}]}
    enc = bencode(obj)
    dec = bdecode(enc)
    assert dec == obj
    # 重新编码应稳定（key 已排序）
    assert bencode(dec) == enc


def test_bdecode_all_consumed():
    data = bencode({b"msg_type": 1, b"piece": 0}) + b"RAWDATA"
    header, consumed = bdecode_all(data)
    assert header[b"msg_type"] == 1
    assert data[consumed:] == b"RAWDATA"


def test_compact_nodes():
    nid = bytes(range(20))
    enc = routing.encode_nodes([(nid, "1.2.3.4", 6881)])
    assert len(enc) == 26
    dec = routing.decode_nodes(enc)
    assert dec == [(nid, "1.2.3.4", 6881)]


def test_compact_peers():
    v = bytes([1, 2, 3, 4]) + (6881).to_bytes(2, "big")
    peers = routing.decode_peers([v])
    assert peers == [("1.2.3.4", 6881)]


def test_neighbor_prefix():
    target = os.urandom(20)
    me = os.urandom(20)
    n = routing.neighbor(target, me, 6)
    assert n[:6] == target[:6]
    assert n[6:] == me[6:]


def test_tokenize_latin_and_cjk():
    t = tokenize("Ubuntu 22.04 中文电影")
    assert "ubuntu" in t
    assert "22" in t and "04" in t
    # 中文 bi-gram
    assert "中文" in t and "文电" in t and "电影" in t


def test_tokenize_query_subset_matches_index():
    name_tokens = set(tokenize("高清中文电影合集"))
    query_tokens = set(tokenize("中文电影"))
    # 查询的所有 bigram 都应包含在名称分词里（$all 能命中）
    assert query_tokens.issubset(name_tokens)


def test_magnet():
    h = to_hex(sha1(b"x"))
    m = magnet(h, "中文 name")
    assert m.startswith("magnet:?xt=urn:btih:" + h)
    assert "dn=" in m


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print("\nAll %d tests passed." % len(fns))
