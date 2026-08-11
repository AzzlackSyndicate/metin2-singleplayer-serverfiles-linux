#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_effect_check_name_spelling.py -- idempotent.

The registration check in tools/bgfxbackend/effect_checks.inc asserted that the
stored effect name contains NO FORWARD SLASH:

    regName->find('/') == std::string::npos

That is the OLD, WRONG normalisation frozen into an expectation. It was written
alongside a NormalisePath that mapped '/' -> '\\', so "normalised" and "carries
backslashes" had become the same sentence in this file.

WHAT THE ORIGINAL ACTUALLY STORES -- and this is the deciding evidence, not the
convenience of a green run:

    CEffectData::LoadScript (EffectLib/EffectData.cpp:28-29)
        m_strFileName = c_szFileName;
        CFileNameHelper::StringPath(m_strFileName);

    CFileNameHelper::StringPath (EterBase/Filename.h:104-114)
        if (str.at(i) == '\\') str.at(i) = '/';        // BACKSLASH -> SLASH
        else                   str.at(i) = tolower(...);

and the free StringPath that CEffectManager::RegisterEffect uses for the key
(EterBase/Utils.cpp:279-288) folds the identical way. So the name the original
keeps beside the key is FORWARD-SLASHED and lowercase. A backslash in it is the
defect, not the norm.

The key that check's own title demands -- GetCaseCRC32(StringPath(name)) -- is
now what BgfxEffectManager computes, measured against the .msa corpus:
samyeon_d.mse 0x31B3CE03, kwaegeom.mse 0xC703FF48, gigongcham_swing.mse
0x9BED9494, where the old spelling produced 0x6BCE175E / 0xE6BB7F4C /
0xACE40C6A and matched nothing GameLib ever asked for.

So the assertion is inverted to its intended meaning: NO BACKSLASH. The
uppercase half of the test is untouched -- it was always right.

The second edit is a comment forty lines below that still described
NormalisePath as mapping "slash -> BACKSLASH". Its assertions never depended on
that and still pass; the sentence is simply no longer true.
"""

import io
import os
import sys

INC = "/opt/m2wasm/tools/bgfxbackend/effect_checks.inc"

# (marker unique file-wide, old, new)
EDITS = [
    (
        "THE NAME CARRIES FORWARD SLASHES, AND THAT IS THE ORIGINAL'S SPELLING",
        "            const std::string* regName = em.RegisteredName(foundCrc);\n"
        "            Check(\"a registered effect keys on GetCaseCRC32(StringPath(name)), which is what \"\n"
        "                  \"CreateEffect(crc) will look up, and it keeps the NORMALISED name so a \"\n"
        "                  \"CRC collision can be reported instead of silently aliasing\",\n"
        "                  em.IsEffectRegistered(foundCrc) &&\n"
        "                  foundCrc == EM::EffectCRC(foundName) &&\n"
        "                  regName != nullptr &&\n"
        "                  regName->find('/') == std::string::npos &&\n"
        "                  regName->find_first_of(\"ABCDEFGHIJKLMNOPQRSTUVWXYZ\") == std::string::npos,\n"
        "                  regName ? *regName : foundName);\n",

        "            const std::string* regName = em.RegisteredName(foundCrc);\n"
        "            // >>> THE NAME CARRIES FORWARD SLASHES, AND THAT IS THE ORIGINAL'S SPELLING. <<<\n"
        "            //\n"
        "            // This line used to read `regName->find('/') == npos`, i.e. it demanded the\n"
        "            // absence of the very separator the original uses. It was written beside a\n"
        "            // NormalisePath that mapped '/' -> '\\\\', so \"normalised\" and \"carries\n"
        "            // backslashes\" had quietly become one sentence in this file — and the\n"
        "            // expectation outlived the defect that produced it.\n"
        "            //\n"
        "            // CEffectData::LoadScript is what the original stores beside the key:\n"
        "            //\n"
        "            //     m_strFileName = c_szFileName;                     // EffectData.cpp:28\n"
        "            //     CFileNameHelper::StringPath(m_strFileName);        // EffectData.cpp:29\n"
        "            //\n"
        "            // and StringPath maps BACKSLASH -> FORWARD SLASH while lowercasing\n"
        "            // (Filename.h:104-114; the free function CEffectManager::RegisterEffect uses\n"
        "            // for the key folds identically, Utils.cpp:279-288). So a backslash in this\n"
        "            // string is the defect and a forward slash is the norm.\n"
        "            //\n"
        "            // The title's key — GetCaseCRC32(StringPath(name)) — is now what the manager\n"
        "            // computes. Measured against the .msa corpus: samyeon_d.mse keys 0x31B3CE03\n"
        "            // where the backslash spelling produced 0x6BCE175E, which is a number GameLib\n"
        "            // never asks for, and every skill effect resolved to no def at all.\n"
        "            //\n"
        "            // The uppercase half below is unchanged: it was always right.\n"
        "            Check(\"a registered effect keys on GetCaseCRC32(StringPath(name)), which is what \"\n"
        "                  \"CreateEffect(crc) will look up, and it keeps the NORMALISED name so a \"\n"
        "                  \"CRC collision can be reported instead of silently aliasing\",\n"
        "                  em.IsEffectRegistered(foundCrc) &&\n"
        "                  foundCrc == EM::EffectCRC(foundName) &&\n"
        "                  regName != nullptr &&\n"
        "                  regName->find('\\\\') == std::string::npos &&\n"
        "                  regName->find_first_of(\"ABCDEFGHIJKLMNOPQRSTUVWXYZ\") == std::string::npos,\n"
        "                  regName ? *regName : foundName);\n",
    ),
    (
        "the SAME direction NormalisePath takes for the CRC key",
        "        // CFileNameHelper::StringPath runs FIRST (EffectData.cpp:33) and maps backslash ->\n"
        "        // FORWARD slash while lowercasing. This is the check that fails if someone reuses\n"
        "        // this file's NormalisePath, which maps slash -> BACKSLASH for the CRC key: that\n"
        "        // would build \"sound/effect\\hit\\...\" and match nothing.\n",

        "        // CFileNameHelper::StringPath runs FIRST (EffectData.cpp:33) and maps backslash ->\n"
        "        // FORWARD slash while lowercasing — the SAME direction NormalisePath takes for the\n"
        "        // CRC key. It was not always: NormalisePath ran the other way, this note warned\n"
        "        // against reusing it here, and the warning was correct while it lasted. The two\n"
        "        // are one transformation now, and this check pins the derivation either way.\n",
    ),
]


def main():
    if not os.path.isfile(INC):
        print("FAIL missing %s" % INC)
        return 1

    changed = 0
    already = 0
    failed = []

    for marker, old, new in EDITS:
        with io.open(INC, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
            text = f.read()

        if marker in text:
            already += 1
            print("[ok  ] already patched: %s" % marker[:52])
            continue

        cnt = text.count(old)
        if cnt != 1:
            failed.append("anchor found %d times (expected 1): %s" % (cnt, old[:120]))
            continue

        with io.open(INC, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(text.replace(old, new, 1))
        changed += 1
        print("[edit] patched: %s" % marker[:52])

    print("\nchanged=%d already=%d failed=%d" % (changed, already, len(failed)))
    for f in failed:
        print("FAIL " + f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
