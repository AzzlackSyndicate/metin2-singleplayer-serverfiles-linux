#!/usr/bin/env python3
"""Compare every packet struct against the SERVER, which is the authority.

The first pass compared against the stock client and could not compute 38
structs whose sizes depend on constants (PART_MAX_NUM, CHARACTER_NAME_MAX_LEN,
...). Those are exactly where the remaining divergences can hide -- the character
packets carry equipment parts and coordinates, which is what we are chasing.

So: harvest constants from both trees, resolve array bounds, and compare client
against server field by field. Anything still unresolved is printed, never
silently dropped -- a silent skip reads as "no divergence" and would hide the
very thing being looked for.

Both peers use pack(1); the server is a 32-bit build, so long == 4 there, and the
client's LONG is int32_t. Sizes are therefore directly comparable.
"""
import io, os, re, sys

CLIENT_H = "/opt/m2wasm/src/NetworkLib/include/NetworkLib/Packet.h"
SERVER_H = "/opt/m2port/port40250/server/game/src/packet.h"
CLIENT_TREE = "/opt/m2wasm/src"
SERVER_TREE = "/opt/m2port/port40250/server"

SIZES = {
    "BYTE": 1, "char": 1, "bool": 1, "unsigned char": 1, "int8_t": 1, "signed char": 1,
    "WORD": 2, "short": 2, "unsigned short": 2, "int16_t": 2, "uint16_t": 2, "USHORT": 2,
    "DWORD": 4, "int": 4, "INT": 4, "unsigned int": 4, "long": 4, "unsigned long": 4,
    "LONG": 4, "ULONG": 4, "UINT": 4, "float": 4, "int32_t": 4, "uint32_t": 4,
    "time_t": 4, "DWORD_PTR": 4, "size_t": 4,
    "long long": 8, "double": 8, "int64_t": 8, "uint64_t": 8, "LONGLONG": 8, "ULONGLONG": 8,
}


def harvest_constants(root):
    """name -> int, from #define and from enum members across the tree."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        if any(p in dirpath for p in ("/test", "/build", "/.git")):
            continue
        for fn in files:
            if not fn.endswith((".h", ".hpp")):
                continue
            try:
                src = io.open(os.path.join(dirpath, fn), encoding="utf-8",
                              errors="replace").read()
            except OSError:
                continue
            for m in re.finditer(r'#define\s+([A-Z_][A-Z_0-9]*)\s+\(?\s*(\d+)\s*\)?\s*$',
                                 src, re.M):
                out.setdefault(m.group(1), int(m.group(2)))
            for m in re.finditer(r'\b([A-Z_][A-Z_0-9]*)\s*=\s*(\d+)\s*,', src):
                out.setdefault(m.group(1), int(m.group(2)))
    return out


STRUCT_RE = re.compile(
    r'(?:typedef\s+)?struct\s+(\w+)?\s*\{(.*?)\}\s*(\w+)?\s*;', re.S)


def parse(path):
    src = io.open(path, encoding="utf-8", errors="replace").read()
    src = re.sub(r'//[^\n]*', '', src)
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.S)
    out = {}
    for m in STRUCT_RE.finditer(src):
        tag, body, name = m.group(1), m.group(2), m.group(3)
        key = name or tag
        if not key:
            continue
        fields = []
        for line in body.split(";"):
            line = " ".join(line.split())
            if not line or line.startswith(("#", "static", "public", "private", "}")):
                continue
            am = re.match(r'^([A-Za-z_][\w :]*?)\s+([\w\[\]\s,:*]+)$', line)
            if not am:
                continue
            typ = am.group(1).strip()
            for decl in am.group(2).split(","):
                decl = decl.strip()
                arr = re.findall(r'\[([^\]]+)\]', decl)
                star = decl.startswith("*")
                fields.append((typ + ("*" if star else ""), arr))
        out[key] = fields
    return out


def size_of(fields, universe, consts, depth=0):
    total = 0
    for typ, arr in fields:
        if typ.endswith("*"):
            base = 4
        elif typ in SIZES:
            base = SIZES[typ]
        elif typ in universe and depth < 6:
            base = size_of(universe[typ], universe, consts, depth + 1)
            if base is None:
                return None
        else:
            return None
        n = 1
        for a in arr:
            a = a.strip()
            a = re.sub(r'^\w+::', '', a)          # CRaceData::PART_MAX_NUM
            m = re.match(r'^([A-Za-z_]\w*)\s*\+\s*(\d+)$', a)   # NAME_MAX_LEN + 1
            if a.isdigit():
                n *= int(a)
            elif a in consts:
                n *= consts[a]
            elif m and m.group(1) in consts:
                n *= consts[m.group(1)] + int(m.group(2))
            else:
                return None
        total += base * n
    return total


cc = harvest_constants(CLIENT_TREE)
sc = harvest_constants(SERVER_TREE)
print("Konstanten: Client %d, Server %d" % (len(cc), len(sc)))
for k in ("POINT_MAX_NUM", "PART_MAX_NUM", "CHARACTER_NAME_MAX_LEN", "ITEM_MAX_NUM"):
    print("  %-24s Client %-6s Server %s" % (k, cc.get(k), sc.get(k)))

client, server = parse(CLIENT_H), parse(SERVER_H)
common = sorted(set(client) & set(server))
print("\nStrukturen gemeinsam: %d" % len(common))

diffs, unresolved = [], []
for k in common:
    a = size_of(server[k], server, sc)
    b = size_of(client[k], client, cc)
    if a is None or b is None:
        unresolved.append((k, a, b))
    elif a != b:
        diffs.append((k, a, b))

print("\n=== ABWEICHUNGEN (Server -> Client) ===")
for k, a, b in sorted(diffs, key=lambda x: -abs(x[1] - x[2])):
    print("  %-42s %5d -> %5d  (%+d)" % (k, a, b, b - a))
if not diffs:
    print("  keine")

print("\n=== NICHT AUFLOESBAR (ungeprueft!) ===")
for k, a, b in unresolved:
    print("  %-42s Server %-6s Client %s" % (k, a, b))
