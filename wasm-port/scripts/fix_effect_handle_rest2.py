#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_effect_handle_rest2.py -- idempotent.

The one edit fix_effect_handle_rest.py skipped: resetting the three refine
INSTANCE handles in CInstanceBase::__Initialize. Its idempotency marker
(`m_hArmorRefineEffect = EngineLib::EffectHandle{};`) also occurs in
__ClearArmorRefineEffect, which that same run had just written, so the test
reported "already patched" against the wrong function.

It matters because __Initialize is the RE-initialisation of a recycled
CInstanceBase, not just construction. The members' own NSDMIs (Handle.h:26)
cover the first use; without this a reused instance carries the previous
character's handles.
"""

import io
import os
import re
import sys

CPP_INST = "/opt/m2wasm/src/GameLib/src/InstanceBase.cpp"
MARKER = "// NOT 0 — an EffectHandle's empty state is kInvalid, and 0 is a real slot."

PATTERN = re.compile(
    r"\tm_swordRefineEffectRight = 0;\n"
    r"\tm_swordRefineEffectLeft = 0;\n"
    r"\tm_armorRefineEffect = 0;\n"
    r"\n"
    r"\tm_sAlignment = 0;\n"
)

REPL = (
    "\tm_swordRefineEffectRight = 0;\n"
    "\tm_swordRefineEffectLeft = 0;\n"
    "\tm_armorRefineEffect = 0;\n"
    "\t// NOT 0 — an EffectHandle's empty state is kInvalid, and 0 is a real slot.\n"
    "\t// __Initialize re-initialises a RECYCLED instance, so the members' own\n"
    "\t// initialisers are not enough here.\n"
    "\tm_hSwordRefineEffectRight = EngineLib::EffectHandle{};\n"
    "\tm_hSwordRefineEffectLeft = EngineLib::EffectHandle{};\n"
    "\tm_hArmorRefineEffect = EngineLib::EffectHandle{};\n"
    "\n"
    "\tm_sAlignment = 0;\n"
)


def main():
    if not os.path.isfile(CPP_INST):
        print("FAIL missing %s" % CPP_INST)
        return 1
    with io.open(CPP_INST, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        text = f.read()

    if MARKER in text:
        print("[ok  ] already patched")
        return 0

    n = len(PATTERN.findall(text))
    if n != 1:
        print("FAIL pattern matched %d times (expected 1)" % n)
        return 1

    with io.open(CPP_INST, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(PATTERN.sub(REPL, text, count=1))
    print("[edit] patched __Initialize refine-handle reset")
    return 0


if __name__ == "__main__":
    sys.exit(main())
