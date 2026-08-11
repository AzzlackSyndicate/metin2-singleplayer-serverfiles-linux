#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_effect_crc_key.py -- idempotent.

BUG
---
No skill effect is ever drawn on the bgfx backend. The warrior's sword-spin
plays its animation and stays completely dark.

BgfxEffectManager keys m_defs on

    CRC32( NormalisePath(name) )        NormalisePath: '/' -> '\\', lowercase

while the ONLY key the rest of the client ever produces is EterBase's

    GetCaseCRC32( StringPath(name) )    StringPath   : '\\' -> '/', lowercase
                                        GetCaseCRC32 : folds each byte to UPPER
                                                       CASE (CRC32.cpp:88-98)
                                        == CEffectManager::RegisterEffect's key
                                           (EffectManager.cpp:134-135)

TWO independent divergences, and either one alone is enough to miss:

  1. THE SLASH DIRECTION IS INVERTED. EterBase's StringPath (Utils.cpp:279-288,
     and CFileNameHelper::StringPath, Filename.h:111-121) maps BACKSLASH to
     FORWARD SLASH. NormalisePath mapped forward slash to BACKSLASH -- a
     spelling nothing else in the client produces.
  2. THE CASE FOLD RUNS THE WRONG WAY. The "Case" in GetCaseCRC32 is its UPPER
     macro (CRC32.cpp:89), applied to every byte BEFORE the table lookup. The
     file's own comment asserted it folded to lowercase and therefore hashed
     the lowercased path unfolded. CRC-32 over "d:/..." and over "D:/..." are
     of course different numbers.

WHAT IT COSTS
-------------
Every effect whose id is a CRC computed in C++ resolves to no def at all:

  * MOTION-EVENT EFFECTS -- i.e. EVERY SKILL EFFECT. CRaceMotionData's
    TMotionEventDataEffect::Load computes
    `dwEffectIndex = GetCaseCRC32(strEffectFileName)` and registers the same
    name (RaceMotionDataEvent.h:117-118); CActorInstance::MotionEventProcess
    hands that number to AttachEffectByID -> CreateEffectInstance.
    Measured on warrior/skill/samyeon.msa's own EffectFileName
    "d:/ymir work/pc/warrior/effect/samyeon_d.mse":
        GameLib asks for   0x31B3CE03
        this file stored   0x6BCE175E
    kwaegeom.mse: 0xC703FF48 vs 0xE6BB7F4C. gigongcham_swing.mse:
    0x9BED9494 vs 0xACE40C6A.
  * CArea::SetEffect's ambient property effects (Area.cpp:554).
  * CActorInstance::AttachEffectByName (ActorInstanceAttach.cpp:277-278).

CreateEffectInstance sets a slot alive for ANY crc, registered or not
(BgfxEffectManager.cpp:1063-1082), so the miss is silent: a live, updated,
frustum-tested instance that carries neither particles nor meshes and draws
nothing. That is the "N neither" column of the per-frame census line.

Effects created from a NAME (BgfxScene::CreateSpecialEffectInstance,
effect.CreateEffect) and effects whose crc came back OUT of RegisterEffect2
(CInstanceBase::RegisterEffect, CPythonPlayer::RegisterEffect, CRaceData) were
never affected -- they are self-consistent with whatever this file computes,
which is why the map's ambient effects and the +9 glows are on screen while the
skills are not.

The eter backend goes through CEffectManager, which uses GetCaseCRC32 itself,
so this is a bgfx-only regression.

FIX
---
Make the key the original's key, byte for byte: StringPath direction in
NormalisePath, and GetCaseCRC32's UPPER fold in a new CaseCRC32 that reuses the
existing (independently pinned) CRC32.

`path` is also what RegisterEffect hands to CEterPackManager::Get; forward
slashes are that map's native spelling (ConvertFileName, EterPackManager.cpp:
36-57, normalises to them anyway), so the load path is unaffected.

effect_checks.inc keeps passing: EffectCRC("123456789") == 0xCBF43926 is over
digits, which no case fold touches, and the "/ and \\ agree" check compares two
spellings that still normalise to one.
"""

import io
import os
import sys

CPP = "/opt/m2wasm/src/EngineLib/src/bgfx/core/BgfxEffectManager.cpp"
HDR = "/opt/m2wasm/src/EngineLib/src/bgfx/core/BgfxEffectManager.h"

# (path, old, new, expected_count)
EDITS = [
    # ── 1. NormalisePath: the note ────────────────────────────────────────
    (
        CPP,
        "// Lowercase + backslash, i.e. EterBase's StringPath. Reimplemented here rather than included\n"
        "// because EterBase/Utils.h is a Windows-typed header and this TU is renderer-side; the\n"
        "// function is four lines and the CRC below is what actually has to agree with the original.\n",
        "// EterBase's StringPath (Utils.cpp:279-288), BYTE FOR BYTE: BACKSLASH -> FORWARD SLASH,\n"
        "// lowercase everything else. Reimplemented here rather than included because\n"
        "// EterBase/Utils.h is a Windows-typed header and this TU is renderer-side; the function is\n"
        "// four lines and the CRC below is what actually has to agree with the original.\n"
        "//\n"
        "// >>> THE DIRECTION IS THE WHOLE POINT, AND IT USED TO BE INVERTED. <<<\n"
        "//\n"
        "// This mapped '/' -> '\\\\', which is the OPPOSITE of StringPath and a spelling nothing\n"
        "// else in the client ever produces. Together with the missing case fold (see CaseCRC32)\n"
        "// it made this file's map key disagree with the only key GameLib ever hands back, so\n"
        "// every effect created from a C++-computed CRC resolved to NO DEF AT ALL — silently,\n"
        "// because CreateEffectInstance sets a slot alive for any crc, registered or not.\n"
        "//\n"
        "// THAT IS EVERY SKILL EFFECT. TMotionEventDataEffect::Load keys on\n"
        "// GetCaseCRC32(EffectFileName) straight out of the .msa (RaceMotionDataEvent.h:117) and\n"
        "// MotionEventProcess hands that number to AttachEffectByID. Measured against\n"
        "// warrior/skill/samyeon.msa's own \"d:/ymir work/pc/warrior/effect/samyeon_d.mse\":\n"
        "// GameLib asked for 0x31B3CE03, this file had registered it as 0x6BCE175E. The .mse\n"
        "// loaded, the instance lived, the particles never existed. Same for kwaegeom.mse\n"
        "// (0xC703FF48 vs 0xE6BB7F4C) and every other motion-event effect in the corpus.\n"
        "//\n"
        "// CArea::SetEffect (Area.cpp:554) and AttachEffectByName (ActorInstanceAttach.cpp:278)\n"
        "// key the same way and were lost the same way. Effects created from a NAME, and those\n"
        "// whose crc came back OUT of RegisterEffect2, were self-consistent and always worked —\n"
        "// which is why the map's ambient effects were on screen while the skills were not.\n",
        1,
    ),
    # ── 2. NormalisePath: the loop ────────────────────────────────────────
    (
        CPP,
        "        if (c == '/')\n"
        "            c = '\\\\';\n"
        "        else\n"
        "            c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));\n",
        "        if (c == '\\\\')\n"
        "            c = '/';\n"
        "        else\n"
        "            c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));\n",
        1,
    ),
    # ── 3. the CRC32 note, which asserted the wrong fold ──────────────────
    (
        CPP,
        "// GetCaseCRC32 (EterBase/CRC32.cpp) is the standard reflected CRC-32 with polynomial\n"
        "// 0xEDB88320, seeded 0xFFFFFFFF and finally inverted, over the ALREADY-lowercased path — the\n"
        "// \"Case\" in the name is that it folds case, which NormalisePath above has done. Reproduced\n"
        "// here for the same header-hygiene reason, and pinned by effect_checks.inc against a value\n"
        "// computed independently.\n",
        "// The standard reflected CRC-32: polynomial 0xEDB88320, seeded 0xFFFFFFFF, finally\n"
        "// inverted. Reproduced here for the same header-hygiene reason as NormalisePath, and\n"
        "// pinned by effect_checks.inc against a value computed independently.\n"
        "//\n"
        "// THIS IS NOT THE KEY FUNCTION — CaseCRC32 below is. The note that used to sit here\n"
        "// claimed the \"Case\" in GetCaseCRC32 meant \"folds case, which NormalisePath has already\n"
        "// done\", and that reading is what broke every skill effect: the fold is real, it is a\n"
        "// separate step, and it goes to UPPER case, not lower.\n",
        1,
    ),
    # ── 4. CaseCRC32 itself ───────────────────────────────────────────────
    (
        CPP,
        "    unsigned crc = 0xFFFFFFFFu;\n"
        "    for (size_t i = 0; i < len; ++i)\n"
        "        crc = s_table.v[(crc ^ static_cast<unsigned char>(data[i])) & 0xFFu] ^ (crc >> 8);\n"
        "    return crc ^ 0xFFFFFFFFu;\n"
        "}\n",
        "    unsigned crc = 0xFFFFFFFFu;\n"
        "    for (size_t i = 0; i < len; ++i)\n"
        "        crc = s_table.v[(crc ^ static_cast<unsigned char>(data[i])) & 0xFFu] ^ (crc >> 8);\n"
        "    return crc ^ 0xFFFFFFFFu;\n"
        "}\n"
        "\n"
        "// GetCaseCRC32 (EterBase/CRC32.cpp:98-127) — THE key function, and the one every other\n"
        "// translation unit in the client uses to name an effect.\n"
        "//\n"
        "// The \"Case\" is its UPPER macro (CRC32.cpp:89):\n"
        "//\n"
        "//     #define UPPER(c) (((c)>='a' && (c) <= 'z') ? ((c)+('A'-'a')) : (c))\n"
        "//     #define DO1CI(buf, i) crc = CRCTable[(crc ^ UPPER((buf)[(i)])) & 0xff] ^ (crc >> 8)\n"
        "//\n"
        "// i.e. each byte is folded to UPPER case before the table lookup, so the hash is\n"
        "// case-insensitive — NOT \"the caller already lowercased it\". ASCII a..z ONLY, which is\n"
        "// why this reproduces the macro rather than calling std::toupper: toupper is\n"
        "// locale-dependent above 0x7F and these paths can carry high bytes.\n"
        "//\n"
        "// Composed with NormalisePath above this is exactly CEffectManager::RegisterEffect's\n"
        "// `GetCaseCRC32(StringPath(name))` (EffectManager.cpp:134-135), which is what makes the\n"
        "// number GameLib computes and the number this file stores the same number again.\n"
        "unsigned CaseCRC32(std::string_view in)\n"
        "{\n"
        "    std::string folded(in);\n"
        "    for (char& c : folded)\n"
        "        if (c >= 'a' && c <= 'z')\n"
        "            c = static_cast<char>(c - ('a' - 'A'));\n"
        "    return CRC32(folded.c_str(), folded.size());\n"
        "}\n",
        1,
    ),
    # ── 5. EffectCRC ──────────────────────────────────────────────────────
    (
        CPP,
        "    const std::string path = NormalisePath(filename);\n"
        "    return CRC32(path.c_str(), path.size());\n",
        "    const std::string path = NormalisePath(filename);\n"
        "    return CaseCRC32(path);\n",
        1,
    ),
    # ── 6. RegisterEffect and RegisterEffect2 (same two lines, twice) ─────
    (
        CPP,
        "    const std::string path = NormalisePath(filename);\n"
        "    const unsigned crc = CRC32(path.c_str(), path.size());\n",
        "    const std::string path = NormalisePath(filename);\n"
        "    const unsigned crc = CaseCRC32(path);\n",
        2,
    ),
    # ── 7. stale note: DeriveSoundScriptPath's \"opposite direction\" ───────
    (
        CPP,
        "    // CFileNameHelper::StringPath (Filename.h:104-114), which CEffectData::LoadScript:33 runs\n"
        "    // over m_strFileName BEFORE the derivation reads it. BACKSLASH -> FORWARD SLASH, and\n"
        "    // lowercase everything else — the OPPOSITE direction from this file's NormalisePath,\n"
        "    // which produces backslashes for the CRC key. Using that one here would derive\n"
        "    // `sound/effect\\hit\\blow_1\\blow_1_low.mss`, which matches nothing in the pack, and the\n"
        "    // failure would be a silent absence of sound rather than an error.\n",
        "    // CFileNameHelper::StringPath (Filename.h:104-114), which CEffectData::LoadScript:33 runs\n"
        "    // over m_strFileName BEFORE the derivation reads it. BACKSLASH -> FORWARD SLASH, and\n"
        "    // lowercase everything else — the SAME direction as this file's NormalisePath, which is\n"
        "    // the same function under a different name. It was not always: NormalisePath used to run\n"
        "    // backwards, this loop was written to compensate, and the compensation is why the .mss\n"
        "    // derivation kept working while the CRC key did not. Left spelled out rather than\n"
        "    // delegated, because it is the ORIGINAL that derives from its own normalised copy here\n"
        "    // and the two must not become coupled by accident.\n",
        1,
    ),
    # ── 8. stale note in RegisterEffect: the .mss derivation ──────────────
    (
        CPP,
        "    // After the script's own elements and before the function returns (EffectData.cpp:83-96).\n"
        "    // The derivation runs on the RAW argument, not on `path`: `path` has been through\n"
        "    // NormalisePath and carries backslashes, while the original derives from a name that has\n"
        "    // been through CFileNameHelper::StringPath and carries forward slashes. See\n"
        "    // DeriveSoundScriptPath.\n",
        "    // After the script's own elements and before the function returns (EffectData.cpp:83-96).\n"
        "    // The derivation runs on the RAW argument and normalises it itself, which is what the\n"
        "    // original does too (CEffectData::LoadScript:32-33). `path` would now do just as well —\n"
        "    // NormalisePath and DeriveSoundScriptPath's own loop are the same transformation — but\n"
        "    // passing the raw name keeps this independent of the CRC key's spelling, which has\n"
        "    // already changed once.\n",
        1,
    ),
    # ── 9. stale note in RegisterEffect: the mesh loader ──────────────────
    (
        CPP,
        "    // The path is `filename` and NOT `path`: `path` has been through NormalisePath and\n"
        "    // carries BACKSLASHES for the CRC key, while CTextFileLoader goes to the pack with\n"
        "    // whatever it is handed. The same trap the .mss derivation above documents.\n",
        "    // The path is `filename` and NOT `path`, for the same reason the .mss derivation above\n"
        "    // takes the raw name: CTextFileLoader goes to the pack with whatever it is handed, and\n"
        "    // CEterPackManager::ConvertFileName (EterPackManager.cpp:36-57) normalises both\n"
        "    // spellings anyway. Either works today; the raw name is the one that stays correct if\n"
        "    // the key's spelling is ever touched again.\n",
        1,
    ),
    # ── 10. the header's quirk-1 note, which named the old direction ──────
    (
        HDR,
        "    //     forward-slashed, lowercased spelling — `\"D:\\\\Ymir Work\\\\Effect\\\\...\"` derives the\n"
        "    //     same path as `\"d:/ymir work/effect/...\"`. NOTE that this is the OPPOSITE direction\n"
        "    //     from this file's own NormalisePath, which produces backslashes for the CRC key;\n"
        "    //     they are two different functions that share a name, and using the CRC one here\n"
        "    //     would build `sound/effect\\hit\\...` and miss every file in the pack.\n",
        "    //     forward-slashed, lowercased spelling — `\"D:\\\\Ymir Work\\\\Effect\\\\...\"` derives the\n"
        "    //     same path as `\"d:/ymir work/effect/...\"`. This is the SAME direction NormalisePath\n"
        "    //     takes for the CRC key. It was not always — NormalisePath ran backwards and produced\n"
        "    //     backslashes, which is the defect that made every skill effect resolve to no def;\n"
        "    //     see NormalisePath's own note in the .cpp.\n",
        1,
    ),
]


def main():
    changed = 0
    already = 0
    failed = []

    for path, old, new, count in EDITS:
        if not os.path.isfile(path):
            failed.append("%s: missing" % path)
            continue
        with io.open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
            text = f.read()

        if new in text:
            already += 1
            print("[ok  ] already patched: %s" % os.path.basename(path))
            continue

        cnt = text.count(old)
        if cnt != count:
            failed.append("%s: anchor found %d times (expected %d):\n%s"
                          % (path, cnt, count, old[:200]))
            continue

        text = text.replace(old, new, count)
        with io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(text)
        changed += 1
        print("[edit] patched: %s" % os.path.basename(path))

    print("\nchanged=%d already=%d failed=%d" % (changed, already, len(failed)))
    for f in failed:
        print("FAIL " + f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
