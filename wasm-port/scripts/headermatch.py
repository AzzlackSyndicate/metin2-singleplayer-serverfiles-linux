#!/usr/bin/env python3
"""Match header NUMBER to expected size on both sides.

A struct-name comparison is the wrong unit: both sides deliberately route some
header numbers to a struct the other side calls something else (the client sends
TARGET_CREATE_NEW under 43; the server calls 119 REFINE_INFORMATION while the
client calls the same number REFINE_INFORMATION_NEW). What actually decides
whether the stream desyncs is, per header number: how many bytes the server
writes vs how many the client reads.

Client side is exact -- its registration table is compiled and printed, together
with the STATIC/DYNAMIC flag, because a dynamic packet carries its length on the
wire and therefore cannot desync the stream no matter what the struct says.

Server side comes from its send sites, which is the one hand-read part; each is
printed with its source line so it can be checked.

The earlier version only recognised `Packet(&x, sizeof(TYP))` and silently
skipped the far more common `Packet(&p, sizeof(p))` -- 108 of the send sites in
this tree, among them TPacketGCTime, which is exactly how that bug survived a
clean run. This version resolves `sizeof(variable)` through the variable's local
declaration, sums the several writes that make up one header (shop start is a
4-byte head plus a 1724-byte body), and says so out loud when a size expression
is something it cannot evaluate, instead of dropping the site.
"""
import io, os, re, subprocess

CLIENT_H = "/opt/m2wasm/src/NetworkLib/include/NetworkLib/Packet.h"
CLIENT_MAP = "/opt/m2wasm/src/PyLib/src/bindings/net/PythonNetworkStream.cpp"
SERVER_BIN = "/opt/m2port/server40250/share/bin/game"
SERVER_SRC = "/opt/m2port/port40250/server/game/src"
WORK = "/tmp/hdrmatch"
INC = ["-I/opt/m2wasm/src/NetworkLib/include", "-I/opt/m2wasm/src",
       "-I/opt/m2wasm/src/EterBase", "-I/opt/m2wasm/src/EterBase/Platform",
       "-I/opt/m2wasm/src/GameLib/include", "-I/opt/m2wasm/src/EterLib/include",
       "-I/opt/m2wasm/src/EngineLib/include"]
os.makedirs(WORK, exist_ok=True)


def read(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


# ── client: header number -> registered size, straight from the compiler ─────
# Strip comments first: some registrations are commented out (TARGET_CREATE in
# favour of TARGET_CREATE_NEW), and counting those would invent entries the
# client does not actually have.
cmap = re.sub(r'//[^\n]*', '', read(CLIENT_MAP))
regs = re.findall(r'Set\((HEADER_GC_\w+),\s*CNetworkPacketHeaderMap::TPacketType\('
                  r'sizeof\((\w+)\)\s*,\s*(\w+)', cmap)


def emit(rs):
    lines = ['#include "NetworkLib/Packet.h"', '#include <cstdio>', 'int main(){']
    for hdr, typ, flag in rs:
        lines.append('  printf("%d %s %zu\\n", (int)' + hdr + ', "' + typ + '", sizeof(' + typ + '));')
    lines.append('  return 0; }')
    io.open(WORK + "/m.cpp", "w").write("\n".join(lines))


emit(regs)
for _attempt in range(10):
    r = subprocess.run(["g++", "-std=c++20", "-w", "-o", WORK + "/m", WORK + "/m.cpp"] + INC,
                       capture_output=True, text=True)
    if r.returncode == 0:
        break
    bad = set(re.findall(r"[‘']([A-Za-z_]\w*)[’'] was not declared", r.stderr))
    if not bad:
        print("!! Uebersetzung fehlgeschlagen:")
        print("\n".join(r.stderr.splitlines()[:12]))
        raise SystemExit(1)
    regs = [(h, t, f) for h, t, f in regs if h not in bad and t not in bad]
    emit(regs)
else:
    print("!! zu viele Uebersetzungsversuche")
    raise SystemExit(1)

dynamic = {t for _h, t, f in regs if "DYNAMIC" in f}
client = {}
for line in subprocess.run([WORK + "/m"], capture_output=True, text=True).stdout.splitlines():
    p = line.split()
    if len(p) == 3:
        client[int(p[0])] = (p[1], int(p[2]), p[1] in dynamic)
print("Client: %d registrierte Header (%d davon laengenbehaftet/DYNAMIC)"
      % (len(client), sum(1 for v in client.values() if v[2])))

# ── server: send sites ──────────────────────────────────────────────────────
# One header assignment names the variable that carries the header; the bytes
# that go out for that header number are the bytes of THAT variable's send.
# Several sends of the same variable in one function are the same packet going
# to several recipients, not a concatenation, so they are measured one by one.
# A second, different variable written straight after it is a body appended to a
# head that carries its own length -- reported separately, never summed into the
# fixed-length comparison.
HDRASSIGN = re.compile(r'\b(\w+)\s*(?:\.|->)\s*(?:bHeader|header)\s*=\s*(HEADER_GC_\w+)\s*;')
DECL = re.compile(r'^\s*(?:struct\s+)?((?:T[A-Z]\w*)|packet_\w+|SPacket\w+)\s+([A-Za-z_]\w*)\s*(?:;|=|\[)')
SEND = re.compile(r'\b(?:Buffered)?Packet(?:Around|View|To)?\s*\(\s*&?\s*([A-Za-z_]\w*)\s*,\s*([^;]*?)\)\s*;')
SIZEOF = re.compile(r'sizeof\s*\(\s*(?:struct\s+)?([A-Za-z_]\w*)\s*\)')

sends = {}   # header name -> list of (types|None, exprs, where, extra)
for fn in sorted(os.listdir(SERVER_SRC)):
    if not fn.endswith(".cpp"):
        continue
    ls = read(os.path.join(SERVER_SRC, fn)).splitlines()
    hdr_lines = [i for i, l in enumerate(ls) if HDRASSIGN.search(l)]

    def decl_type(name, upto, floor):
        """Type of `name` as declared nearest above line `upto`."""
        for j in range(upto, floor - 1, -1):
            d = DECL.match(ls[j])
            if d and d.group(2) == name:
                return d.group(1)
        return None

    for n, i in enumerate(hdr_lines):
        m = HDRASSIGN.search(ls[i])
        var, hdr = m.group(1), m.group(2)
        # enclosing function: between the two nearest column-0 closing braces
        start = 0
        for j in range(i - 1, -1, -1):
            if ls[j].startswith("}"):
                start = j + 1
                break
        end = len(ls)
        for j in range(i + 1, len(ls)):
            if ls[j].startswith("}"):
                end = j
                break
        # do not run into the next header being built in the same function
        nxt = hdr_lines[n + 1] if n + 1 < len(hdr_lines) else len(ls)
        if i < nxt < end:
            end = nxt

        def resolve(expr, at):
            """A size expression -> list of type names, or None if not a plain
            sum of sizeof()s over things we can name."""
            names = SIZEOF.findall(expr)
            if not names or re.sub(r'[#\s+]', '', SIZEOF.sub("#", expr)):
                return None
            return [decl_type(nm, at, start) or nm for nm in names]

        seen = set()
        for j in range(i, end):
            s = SEND.search(ls[j])
            if not s or s.group(1) != var:
                continue
            expr = s.group(2).strip()
            if expr in seen:      # same packet, another recipient
                continue
            seen.add(expr)
            # a body written right after the head, for the dynamic packets
            extra = []
            for k in range(j + 1, min(j + 4, end)):
                s2 = SEND.search(ls[k])
                if s2 and s2.group(1) != var:
                    e2 = resolve(s2.group(2).strip(), k)
                    extra += e2 if e2 else [s2.group(2).strip()]
            sends.setdefault(hdr, []).append(
                (resolve(expr, j), expr, "%s:%d" % (fn, j + 1), extra))

# header names -> numbers, from the server's enum
val, hdrnum = 0, {}
for line in read(SERVER_SRC + "/packet.h").splitlines():
    m = re.search(r'\b(HEADER_GC_[A-Z_0-9]+)\s*(=\s*([0-9xXa-fA-F]+))?\s*,', line)
    if not m:
        continue
    if m.group(3):
        try:
            val = int(m.group(3), 0)
        except ValueError:
            continue
    else:
        val += 1
    hdrnum.setdefault(m.group(1), val)

# server struct sizes from the binary
types = sorted({t for v in sends.values() for tps, _e, _w, ex in v
                for t in (tps or []) + ex if re.match(r'^[A-Za-z_]\w*$', t)})
args = ["gdb", "-batch", "-nx"]
for t in types:
    args += ["-ex", 'printf "%s=%d\\n", "' + t + '", sizeof(' + t + ')']
args.append(SERVER_BIN)
gdbout = subprocess.run(args, capture_output=True, text=True).stdout
ssize = {m.group(1): int(m.group(2)) for m in re.finditer(r'^(\w+)=(\d+)$', gdbout, re.M)}

print("Server: %d Header mit erkannten Sendestellen\n" % len(sends))

bad, info, unresolved, unknown = [], [], [], []
for hdr, entries in sorted(sends.items(), key=lambda x: hdrnum.get(x[0], 999)):
    num = hdrnum.get(hdr)
    if num is None:
        continue
    if num not in client:
        unknown.append((num, hdr, entries[0][2]))
        continue
    ctyp, csize, cdyn = client[num]
    for tps, expr, where, extra in entries:
        if tps is None or any(ssize.get(t) is None for t in tps):
            unresolved.append((num, hdr, expr, where, cdyn))
            continue
        total = sum(ssize[t] for t in tps)
        detail = " + ".join("%s(%d)" % (t, ssize[t]) for t in tps)
        if extra:
            detail += "  +Rumpf " + " + ".join(
                "%s(%s)" % (t, ssize.get(t, "?")) for t in extra)
        row = (num, hdr, detail, total, ctyp, csize, where)
        if total != csize:
            (info if cdyn else bad).append(row)

print("=== ECHTE ABWEICHUNG (Client liest feste Laenge): %d ===" % len(bad))
for num, hdr, detail, total, ctyp, csize, where in sorted(bad, key=lambda x: -abs(x[3] - x[5])):
    print("  %3d %-34s" % (num, hdr))
    print("      Server %5d = %-46s (%s)" % (total, detail, where))
    print("      Client %5d   %-34s  <-- DESYNC" % (csize, ctyp))
if not bad:
    print("  keine -- jede aufloesbare Sendestelle passt zur registrierten Laenge")

print("\n=== unkritisch: Client liest Laenge vom Draht (DYNAMIC): %d ===" % len(info))
for num, hdr, detail, total, ctyp, csize, where in sorted(info):
    print("  %3d %-28s Kopf %5d = %-50s / Client %s(%d) DYNAMIC"
          % (num, hdr, total, detail, ctyp, csize))

print("\n=== Sendestellen mit nicht auswertbarer Groesse (Handpruefung): %d ===" % len(unresolved))
for num, hdr, expr, where, cdyn in sorted(unresolved):
    print("  %3d %-30s %-40s (%s)%s"
          % (num, hdr, expr[:40], where, "  [Client DYNAMIC]" if cdyn else ""))

print("\n=== Server sendet, Client kennt die Nummer nicht: %d ===" % len(unknown))
for num, hdr, where in sorted(unknown):
    print("  %3d %-30s (%s)" % (num, hdr, where))
