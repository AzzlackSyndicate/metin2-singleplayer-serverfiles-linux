#!/usr/bin/env python3
"""cipher.h asserts but never included <cassert>.

In the stock client it came in through stdafx.h, the precompiled header this
tree does not have. The same class of breakage will apply to anything else
ported out of that source, so it is fixed at the top of the file rather than by
adding a stray include at the use site.
"""
import io
P = "/opt/m2wasm/src/NetworkLib/include/NetworkLib/cipher.h"
s = io.open(P, encoding="utf-8", newline="").read()
if "<cassert>" in s:
    print("  schon vorhanden")
else:
    anchor = "#include <cryptopp/cryptlib.h>\n"
    assert s.count(anchor) == 1, "Anker nicht eindeutig"
    add = ("// The stock client got assert() from its precompiled header; this tree has none.\n"
           "#include <cassert>\n")
    io.open(P, "w", encoding="utf-8", newline="").write(s.replace(anchor, add + anchor, 1))
    print("  <cassert> ergaenzt")
