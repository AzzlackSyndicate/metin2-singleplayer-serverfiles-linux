#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_affect_effect_handle.py -- idempotent.

BUG
---
The stun symbol stays over the character forever after the stun has ended.

CInstanceBase::__SetAffect attaches the affect effect (EFFECT_AFFECT + eAffect,
i.e. EFFECT_AFFECT+AFFECT_STUN for the stun) and stores ONLY the .index of the
returned EngineLib::EffectHandle in the DWORD array m_adwCRCAffectEffect.
When the affect flag is cleared it rebuilds the handle as {index, 0} and calls
__DetachEffect -> CActorInstance::DettachEffect, which compares with
EterBase::Handle::operator==  ->  index == o.index && generation == o.generation.

BgfxEffectManager::GetEmptyIndex hands out generation >= 1 ("0 is never handed
out", BgfxEffectManager.h:582), so the comparison ALWAYS fails: the entry is
never erased from m_AttachingEffectList and DestroyEffectInstance is never
reached. The effect keeps being drawn forever, while
m_GraphicThingInstance.SetSleep(false) does end the stun animation -- which is
exactly the reported symptom (character walks again, symbol stays).

Additionally, bgfx slot indices start at 0 and are RECYCLED, whereas the
original CEffectManager::GetEmptyIndex (EffectManager.cpp:390) starts at 1 and
never reuses an index. So `if (!m_adwCRCAffectEffect[eAffect])` -- the legacy
"0 means no effect" test -- misreads a perfectly valid slot 0 as "nothing
attached".

FIX
---
Store the whole handle, exactly like the already-repaired
SEffectContainer::Dict (InstanceBase.h:960, std::map<DWORD, EffectHandle>) that
fixed the identical defect for the selection rings. Validity is then tested
with Handle::valid() instead of the 0 sentinel, so slot 0 is no longer
mistaken for "empty".
"""

import io
import os
import sys

EDITS = [
    # ---- InstanceBase.h : the member itself -----------------------------
    (
        "/opt/m2wasm/src/GameLib/include/GameLib/InstanceBase.h",
        "\t\tDWORD\t\t\t\t\tm_adwCRCAffectEffect[AFFECT_NUM] = {};\n",
        "\t\t// >>> THE WHOLE HANDLE, NOT JUST ITS INDEX. <<<\n"
        "\t\t//\n"
        "\t\t// Same defect, same repair as SEffectContainer::Dict below. This was\n"
        "\t\t// DWORD[] and held __AttachEffect(...).index alone, so __SetAffect had to\n"
        "\t\t// rebuild the handle as {index, 0} to detach. CActorInstance::DettachEffect\n"
        "\t\t// (ActorInstanceAttach.cpp) compares with operator== -- index AND generation\n"
        "\t\t// -- and bgfx's GetEmptyIndex hands out generation 1 and up\n"
        "\t\t// (BgfxEffectManager.h:582, \"0 is never handed out\"). The comparison\n"
        "\t\t// therefore ALWAYS failed: the affect effect was never erased from\n"
        "\t\t// m_AttachingEffectList and DestroyEffectInstance was never reached.\n"
        "\t\t// Visible as the stun symbol hanging over a character that can already walk\n"
        "\t\t// again -- SetSleep(false) does run, only the effect never goes away.\n"
        "\t\t//\n"
        "\t\t// The eter manager's generation is 0, so {index, 0} compared equal there and\n"
        "\t\t// the truncation was invisible; this is a bgfx-only regression.\n"
        "\t\t//\n"
        "\t\t// Handle{} (index == kInvalid) is the empty state, NOT 0: bgfx slot indices\n"
        "\t\t// start at 0 and are recycled, while the original CEffectManager never\n"
        "\t\t// handed out 0 and never reused an index (EffectManager.cpp:390).\n"
        "\t\tEngineLib::EffectHandle\tm_adwCRCAffectEffect[AFFECT_NUM];\n",
    ),
    # ---- InstanceBaseEffect.cpp : __ClearAffects ------------------------
    (
        "/opt/m2wasm/src/GameLib/src/InstanceBaseEffect.cpp",
        "\t\t\t__DetachEffect(EngineLib::EffectHandle{m_adwCRCAffectEffect[iAffect], 0});\n"
        "\t\t\tm_adwCRCAffectEffect[iAffect]=0;\n",
        "\t\t\t// The handle AS IT WAS HANDED OUT, generation included -- see the note on\n"
        "\t\t\t// m_adwCRCAffectEffect in InstanceBase.h.\n"
        "\t\t\t__DetachEffect(m_adwCRCAffectEffect[iAffect]);\n"
        "\t\t\tm_adwCRCAffectEffect[iAffect]=EngineLib::EffectHandle{};\n",
    ),
    # ---- InstanceBaseEffect.cpp : __SetAffect ---------------------------
    (
        "/opt/m2wasm/src/GameLib/src/InstanceBaseEffect.cpp",
        "\t\tif (!m_adwCRCAffectEffect[eAffect])\n"
        "\t\t{\n"
        "\t\t\tm_adwCRCAffectEffect[eAffect]=__AttachEffect(EFFECT_AFFECT+eAffect).index;\n"
        "\t\t}\n"
        "\t}\n"
        "\telse\n"
        "\t{\n"
        "\t\tif (m_adwCRCAffectEffect[eAffect])\n"
        "\t\t{\n"
        "\t\t\t__DetachEffect(EngineLib::EffectHandle{m_adwCRCAffectEffect[eAffect], 0});\n"
        "\t\t\tm_adwCRCAffectEffect[eAffect]=0;\n"
        "\t\t}\n"
        "\t}\n",
        "\t\t// valid(), not `!= 0`: 0 is a legitimate bgfx slot index, and the original's\n"
        "\t\t// \"0 means none\" invariant (CEffectManager::GetEmptyIndex started at 1 and\n"
        "\t\t// never recycled) does not hold on this backend.\n"
        "\t\tif (!m_adwCRCAffectEffect[eAffect].valid())\n"
        "\t\t{\n"
        "\t\t\tm_adwCRCAffectEffect[eAffect]=__AttachEffect(EFFECT_AFFECT+eAffect);\n"
        "\t\t}\n"
        "\t}\n"
        "\telse\n"
        "\t{\n"
        "\t\tif (m_adwCRCAffectEffect[eAffect].valid())\n"
        "\t\t{\n"
        "\t\t\t// THE STUN FIX. Rebuilding this as {index, 0} made the detach a silent\n"
        "\t\t\t// no-op on bgfx and left the stun symbol over the character forever.\n"
        "\t\t\t__DetachEffect(m_adwCRCAffectEffect[eAffect]);\n"
        "\t\t\tm_adwCRCAffectEffect[eAffect]=EngineLib::EffectHandle{};\n"
        "\t\t}\n"
        "\t}\n",
    ),
    # ---- InstanceBaseMovement.cpp : gyeonggong / kwaesok ----------------
    (
        "/opt/m2wasm/src/GameLib/src/InstanceBaseMovement.cpp",
        "\t\tm_adwCRCAffectEffect[AFFECT_GYEONGGONG] = __EffectContainer_AttachEffect(EFFECT_AFFECT_GYEONGGONG).index;\n",
        "\t\tm_adwCRCAffectEffect[AFFECT_GYEONGGONG] = __EffectContainer_AttachEffect(EFFECT_AFFECT_GYEONGGONG);\n",
    ),
    (
        "/opt/m2wasm/src/GameLib/src/InstanceBaseMovement.cpp",
        "\t\tm_adwCRCAffectEffect[AFFECT_KWAESOK] = __EffectContainer_AttachEffect(EFFECT_AFFECT_KWAESOK).index;\n",
        "\t\tm_adwCRCAffectEffect[AFFECT_KWAESOK] = __EffectContainer_AttachEffect(EFFECT_AFFECT_KWAESOK);\n",
    ),
    # ---- InstanceBase.cpp : __Initialize --------------------------------
    (
        "/opt/m2wasm/src/GameLib/src/InstanceBase.cpp",
        "\tmemset(m_adwCRCAffectEffect, 0, sizeof(m_adwCRCAffectEffect));\n",
        "\t// NOT memset 0 -- the empty state of an EffectHandle is kInvalid, and 0 is a\n"
        "\t// perfectly good bgfx slot index. See the note in InstanceBase.h.\n"
        "\tfor (auto& rkAffectEffect : m_adwCRCAffectEffect)\n"
        "\t\trkAffectEffect = EngineLib::EffectHandle{};\n",
    ),
]


def main():
    changed = 0
    already = 0
    failed = []

    for path, old, new in EDITS:
        if not os.path.isfile(path):
            failed.append("%s: missing" % path)
            continue
        with io.open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
            text = f.read()

        if new in text:
            already += 1
            print("[ok  ] already patched: %s" % path)
            continue

        cnt = text.count(old)
        if cnt != 1:
            failed.append("%s: anchor found %d times (expected 1):\n%s" % (path, cnt, old))
            continue

        text = text.replace(old, new, 1)
        with io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(text)
        changed += 1
        print("[edit] patched: %s" % path)

    print("\nchanged=%d already=%d failed=%d" % (changed, already, len(failed)))
    for f in failed:
        print("FAIL " + f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
