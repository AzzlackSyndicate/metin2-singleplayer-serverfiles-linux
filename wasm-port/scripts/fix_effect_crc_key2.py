#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_effect_crc_key2.py -- idempotent. Second half of fix_effect_crc_key.py.

The slash direction in NormalisePath itself. It is split out because the loop
body alone is BYTE-IDENTICAL to the one in DeriveSoundScriptPath forty lines
below -- which already ran in the correct direction -- so an anchor that is
only the loop matches twice and an idempotency test that is only the loop
reports "already patched" against the wrong function. The anchor here carries
the signature.
"""

import io
import os
import sys

CPP = "/opt/m2wasm/src/EngineLib/src/bgfx/core/BgfxEffectManager.cpp"

OLD = (
    "std::string NormalisePath(std::string_view in)\n"
    "{\n"
    "    std::string out(in);\n"
    "    for (char& c : out)\n"
    "    {\n"
    "        if (c == '/')\n"
    "            c = '\\\\';\n"
)

NEW = (
    "std::string NormalisePath(std::string_view in)\n"
    "{\n"
    "    std::string out(in);\n"
    "    for (char& c : out)\n"
    "    {\n"
    "        if (c == '\\\\')\n"
    "            c = '/';\n"
)


def main():
    if not os.path.isfile(CPP):
        print("FAIL missing %s" % CPP)
        return 1
    with io.open(CPP, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        text = f.read()

    if NEW in text:
        print("[ok  ] already patched")
        return 0

    cnt = text.count(OLD)
    if cnt != 1:
        print("FAIL anchor found %d times (expected 1)" % cnt)
        return 1

    with io.open(CPP, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text.replace(OLD, NEW, 1))
    print("[edit] patched NormalisePath direction")
    return 0


if __name__ == "__main__":
    sys.exit(main())
