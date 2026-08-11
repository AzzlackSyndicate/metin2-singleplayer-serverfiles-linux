#!/usr/bin/env python3
"""Decode a Metin2 client item_proto (MIPX/MCOZ, TEA + LZO1X) and print armour shape values.

Read-only diagnostic: prints VNUM / name / type / subtype / value0..5 for a
requested VNUM range. Nothing is written or copied.
"""
import ctypes, struct, sys, zipfile, os

KEY = (173217, 72619434, 408587239, 27973291)
DELTA = 0x9E3779B9
M32 = 0xFFFFFFFF


def tea_decode(z, y, key):
    total = (DELTA * 32) & M32
    for _ in range(32):
        z = (z - ((((y << 4) & M32) ^ (y >> 5)) + y ^ (total + key[(total >> 11) & 3]))) & M32
        total = (total - DELTA) & M32
        y = (y - ((((z << 4) & M32) ^ (z >> 5)) + z ^ (total + key[total & 3]))) & M32
    return y, z


def tea_decrypt(src: bytes, key) -> bytes:
    resize = (len(src) + 7) & ~7
    src = src + b"\0" * (resize - len(src))
    w = struct.unpack("<%dI" % (resize // 4), src)
    out = []
    for i in range(resize // 8):
        y, z = tea_decode(w[i * 2 + 1], w[i * 2], key)
        out += [y, z]
    return struct.pack("<%dI" % len(out), *out)


_lzo = None


def lzo1x_decompress(src: bytes, out_len: int) -> bytes:
    global _lzo
    if _lzo is None:
        _lzo = ctypes.CDLL("liblzo2.so.2")
        _lzo.__lzo_init_v2(ctypes.c_uint(1),
                           ctypes.c_int(-1), ctypes.c_int(-1), ctypes.c_int(-1),
                           ctypes.c_int(-1), ctypes.c_int(-1), ctypes.c_int(-1),
                           ctypes.c_int(-1), ctypes.c_int(-1), ctypes.c_int(-1))
    dst = ctypes.create_string_buffer(out_len)
    dl = ctypes.c_size_t(out_len)
    r = _lzo.lzo1x_decompress(src, ctypes.c_size_t(len(src)), dst,
                              ctypes.byref(dl), None)
    if r != 0:
        raise RuntimeError("lzo1x_decompress ret %d" % r)
    return dst.raw[:dl.value]


def load_proto(data: bytes):
    fourcc, ver, stride, elems, datasize = struct.unpack_from("<4sIIII", data, 0)
    assert fourcc == b"MIPX", fourcc
    blob = data[20:20 + datasize]
    mc, enc_size, comp_size, real_size = struct.unpack_from("<4sIII", blob, 0)
    assert mc == b"MCOZ", mc
    if enc_size:
        dec = tea_decrypt(blob[16:16 + enc_size], KEY)
        assert dec[:4] == b"MCOZ", "wrong key: %r" % dec[:4]
        raw = lzo1x_decompress(dec[4:4 + comp_size], real_size)
    else:
        raw = lzo1x_decompress(blob[20:20 + comp_size], real_size)
    assert len(raw) == real_size
    return stride, elems, raw


def rec(raw, i, stride):
    b = raw[i * stride:(i + 1) * stride]
    vnum, vrange = struct.unpack_from("<II", b, 0)
    name = b[8:33].split(b"\0")[0].decode("latin-1")
    loc = b[33:58].split(b"\0")[0].decode("latin-1")
    btype, bsub, bweight, bsize = struct.unpack_from("<BBBB", b, 58)
    values = struct.unpack_from("<6i", b, 111)
    specular = b[154]
    return dict(vnum=vnum, name=name, loc=loc, type=btype, sub=bsub,
                values=values, specular=specular)


def main():
    path = sys.argv[1]
    lo, hi = int(sys.argv[2]), int(sys.argv[3])
    if "::" in path:
        zp, inner = path.split("::")
        data = zipfile.ZipFile(zp).read(inner)
    else:
        data = open(path, "rb").read()
    stride, elems, raw = load_proto(data)
    print("# %s  stride=%d elements=%d" % (path, stride, elems))
    print("# %-6s %-26s t/s  %s" % ("vnum", "name", "value0..5"))
    for i in range(elems):
        r = rec(raw, i, stride)
        if lo <= r["vnum"] <= hi:
            print("%-8d %-26s %d/%-2d %s spec=%d" % (
                r["vnum"], r["name"], r["type"], r["sub"],
                " ".join("%6d" % v for v in r["values"]), r["specular"]))


if __name__ == "__main__":
    main()
