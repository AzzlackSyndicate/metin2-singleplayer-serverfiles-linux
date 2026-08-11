#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_damage_digit_textures2.py -- idempotent.

The Slot member fix_damage_digit_textures.py skipped.

Its idempotency marker was the bare word `particleTextureOverride`, and the
edit that ran just before it had inserted a COMMENT containing that word
("see Slot::particleTextureOverride"). The marker matched its own neighbour's
prose, the member was never declared, and the .cpp half failed to compile
against a struct that had no such field.

Third time this trap has fired in this file's patch history. The marker here is
the declaration itself, punctuation included, which no comment reproduces.
"""

import io
import os
import re
import sys

HDR = "/opt/m2wasm/src/EngineLib/src/bgfx/core/BgfxEffectManager.h"
MARKER = "std::vector<std::vector<uint16_t>> particleTextureOverride;"

PATTERN = re.compile(
    r"        std::vector<EffectVertex> geometry;\n"
    r"        EffectDraw                draw;\n"
)

REPL = (
    "        std::vector<EffectVertex> geometry;\n"
    "        EffectDraw                draw;\n"
    "\n"
    "        // THE TEXTURES THIS INSTANCE WAS BORN WITH, keyed [system][textureFrame] like\n"
    "        // EffectDef::particleTextures, and empty for the overwhelming majority of\n"
    "        // effects — only the damage-number path overrides a texture at all.\n"
    "        //\n"
    "        // PER SLOT BECAUSE THE NUMBER 123 IS THREE INSTANCES OF ONE DEF, created in one\n"
    "        // loop with a different texture set before each (CInstanceBase::AddDamageEffect).\n"
    "        // Anything hanging off the def is shared by all three, and they would all show\n"
    "        // the last digit written — a different picture from 000 and the same class of\n"
    "        // defect.\n"
    "        std::vector<std::vector<uint16_t>> particleTextureOverride;\n"
)


def main():
    if not os.path.isfile(HDR):
        print("FAIL missing %s" % HDR)
        return 1
    with io.open(HDR, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        text = f.read()

    if MARKER in text:
        print("[ok  ] already patched")
        return 0

    n = len(PATTERN.findall(text))
    if n != 1:
        print("FAIL pattern matched %d times (expected 1)" % n)
        return 1

    with io.open(HDR, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(PATTERN.sub(REPL, text, count=1))
    print("[edit] declared Slot::particleTextureOverride")
    return 0


if __name__ == "__main__":
    sys.exit(main())
