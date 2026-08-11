#!/usr/bin/env python3
"""Ask both compilers for every packet size and compare. No parsing, no guessing.

Two passes of hand-parsing headers each missed cases: constants that could not be
resolved, and a `std::int64_t` my size table did not know about. Both compilers
already know the answers exactly:

  server -- the binary carries debug info, so gdb reports sizeof() as built
  client -- compile a generated file against this tree's Packet.h and print sizeof()

Every mismatch is a desync waiting to happen. Types missing on either side are
reported rather than dropped silently.
"""
import io, os, re, subprocess, sys

CLIENT_H = "/opt/m2wasm/src/NetworkLib/include/NetworkLib/Packet.h"
SERVER_BIN = "/opt/m2port/server40250/share/bin/game"
WORK = "/tmp/wirediff"
INC = ["-I/opt/m2wasm/src/NetworkLib/include", "-I/opt/m2wasm/src",
       "-I/opt/m2wasm/src/EterBase", "-I/opt/m2wasm/src/EterBase/Platform",
       "-I/opt/m2wasm/src/GameLib/include", "-I/opt/m2wasm/src/EterLib/include",
       "-I/opt/m2wasm/src/EngineLib/include"]

os.makedirs(WORK, exist_ok=True)

src = io.open(CLIENT_H, encoding="utf-8", errors="replace").read()
names = sorted(set(re.findall(r'\}\s*(T(?:Packet|Player|Simple|Sub)\w*)\s*;', src)))
print("Typen aus Packet.h: %d" % len(names))


def emit(ns):
    lines = ['#include "NetworkLib/Packet.h"', '#include <cstdio>', 'int main(){']
    for n in ns:
        lines.append('  printf("%s %zu\\n", "' + n + '", sizeof(' + n + '));')
    lines.append('  return 0; }')
    io.open(WORK + "/sizes.cpp", "w").write("\n".join(lines))


# Some names sit in disabled #ifdef blocks. Let the compiler name them and drop
# those, instead of guessing which features this build has.
emit(names)
dropped = []
for _attempt in range(10):
    r = subprocess.run(["g++", "-std=c++20", "-w", "-o", WORK + "/sizes",
                        WORK + "/sizes.cpp"] + INC, capture_output=True, text=True)
    if r.returncode == 0:
        break
    bad = set(re.findall(r"[‘']([A-Za-z_]\w*)[’'] was not declared", r.stderr))
    if not bad:
        print("\n!! Client-Uebersetzung fehlgeschlagen:")
        print("\n".join("  " + l for l in r.stderr.strip().splitlines()[:15]))
        sys.exit(1)
    dropped += sorted(bad)
    names = [n for n in names if n not in bad]
    emit(names)
else:
    print("!! zu viele Uebersetzungsversuche")
    sys.exit(1)

if dropped:
    print("im Client abgeschaltet (#ifdef): %d" % len(dropped))

client = {}
for line in subprocess.run([WORK + "/sizes"], capture_output=True, text=True).stdout.split("\n"):
    p = line.split()
    if len(p) == 2:
        client[p[0]] = int(p[1])
print("Client geliefert: %d" % len(client))

args = ["gdb", "-batch", "-nx"]
for n in names:
    args += ["-ex", 'printf "%s=%d\\n", "' + n + '", sizeof(' + n + ')']
args.append(SERVER_BIN)
out = subprocess.run(args, capture_output=True, text=True).stdout
server = {}
for m in re.finditer(r'^(\w+)=(\d+)$', out, re.M):
    server[m.group(1)] = int(m.group(2))
print("Server geliefert: %d\n" % len(server))

both = sorted(set(client) & set(server))
diffs = [(n, server[n], client[n]) for n in both if server[n] != client[n]]

print("=== ABWEICHUNGEN (Server -> Client): %d von %d gemeinsamen ===" % (len(diffs), len(both)))
for n, a, b in sorted(diffs, key=lambda x: -abs(x[1] - x[2])):
    print("  %-44s %6d -> %6d   (%+d)" % (n, a, b, b - a))
if not diffs:
    print("  keine -- die Paketebene stimmt vollstaendig ueberein")

only_client = sorted(set(client) - set(server))
print("\n=== dem Server unbekannt (ungeprueft): %d ===" % len(only_client))
print("  " + ", ".join(only_client[:25]))
