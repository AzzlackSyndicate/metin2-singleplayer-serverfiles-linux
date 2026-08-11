#!/usr/bin/env python3
"""`min` in the ported cipher is a Windows macro.

Our own Linux server port hit this exact line and solved it with a __GNUC__
guard; the same guard goes here, so both ends of the key derivation compute the
offset with the same expression. Not std::min unconditionally: the file must
still build for the Windows target of this tree.
"""
import io
P = "/opt/m2wasm/src/NetworkLib/src/cipher.cpp"
s = io.open(P, encoding="utf-8", newline="").read()

old = "\toffset = min(key_length_0, shared.size() - key_length_1);\n"
new = ("#ifdef __GNUC__\n"
       "\toffset = std::min(key_length_0, shared.size() - key_length_1);\n"
       "#else\n"
       "\toffset = min(key_length_0, shared.size() - key_length_1);\n"
       "#endif\n")

if "#ifdef __GNUC__" in s:
    print("  schon behandelt")
else:
    assert s.count(old) == 1, "min-Zeile nicht eindeutig (%d Treffer)" % s.count(old)
    s = s.replace(old, new, 1)
    if "#include <algorithm>" not in s:
        anchor = '#include "NetworkLib/cipher.h"\n'
        assert s.count(anchor) == 1
        s = s.replace(anchor, anchor + "#include <algorithm>   // std::min, see the __GNUC__ guard below\n", 1)
    io.open(P, "w", encoding="utf-8", newline="").write(s)
    print("  __GNUC__-Zweig und <algorithm> ergaenzt")

t = io.open(P, encoding="utf-8").read()
print("  std::min vorhanden : %s" % ("ok" if "std::min(key_length_0" in t else "FEHLT"))
print("  <algorithm>        : %s" % ("ok" if "#include <algorithm>" in t else "FEHLT"))
