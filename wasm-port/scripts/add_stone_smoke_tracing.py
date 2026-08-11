#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
add_stone_smoke_tracing.py -- idempotent. INSTRUMENTATION ONLY.

The metin stone's glow. Everything about this path checks out statically and
matches the original line for line, so the answer is at runtime and this is what
makes it visible. Gated on METIN2_BGFX_FX_NAMES like the rest.

WHAT THE DATA SAYS, so the log can be read against it:

    metinstone_01.msm (monster.zip)
        SmokeBoneName  "ChamferCyl04"
        List SmokeFileName
        {
            0  "D:\\Ymir Work\\effect\\background\\metinstone_loop_1.mse"
            1..3  loop_2 / loop_3 / loop_4
        }
        Group AttachingData -- BOTH entries are AttachingDataType 1 (collision).

So the stone's ONLY visual effect is the SmokeFileName list, and
`metinstone_loop_1.mse` is three Group Particle blocks over youn_gi.dds and
gatein.dds -- the yellow aura. "StoneSmoke" is a misnomer inherited from the
field name: on a metin stone this IS the light, and there is no second effect
to look for. The damage tier picks which of the four
(__SetStoneSmokeFlagContainer: STONE_SMOKE8 -> 3, SMOKE5..7 -> 2, SMOKE2..4 ->
1, else 0).

THE CHAIN IS INTACT AND FAITHFUL -- checked against the oracle:
    SetAffectFlagContainer -> IsStone() -> __SetStoneSmokeFlagContainer
      -> __StoneSmoke_Destroy() + __StoneSmoke_Create(eSmoke)
      -> CActorInstance::AttachSmokeEffect
      -> AttachEffectByID(0, GetSmokeBone(), GetSmokeEffectID(eSmoke))
and the id comes from CRaceData's RegisterEffect2 out-parameter
(RaceDataFile.cpp:57-58), i.e. the manager's OWN key -- so this path was never
touched by the CRC-key defect and cannot have been fixed by repairing it.

Which leaves exactly three runtime questions, one line each in the log:
  * is __StoneSmoke_Create reached at all (is IsStone() true, does the affect
    packet arrive)?
  * is the smoke id non-zero, i.e. did CRaceData::RegisterEffect2 succeed?
  * does AttachEffectByID hand back a valid handle, and does the bone resolve?
"""

import io
import os
import re
import sys

CPP_EFF = "/opt/m2wasm/src/GameLib/src/InstanceBaseEffect.cpp"

MARKER = "[fx-stone] create"

PATTERN = re.compile(
    r"void CInstanceBase::__StoneSmoke_Create\(DWORD eSmoke\)\n"
    r"\{\n"
    r"\tm_kStoneSmoke\.m_dwEftID=m_GraphicThingInstance\.AttachSmokeEffect\(eSmoke\);\n"
    r"\}\n"
)

REPL = (
    "void CInstanceBase::__StoneSmoke_Create(DWORD eSmoke)\n"
    "{\n"
    "\tm_kStoneSmoke.m_dwEftID=m_GraphicThingInstance.AttachSmokeEffect(eSmoke);\n"
    "\n"
    "\t// >>> THE METIN STONE'S GLOW, AND IT IS NOT SMOKE. <<<\n"
    "\t//\n"
    "\t// The field is named for the .msm token (`List SmokeFileName`), but on a metin\n"
    "\t// stone the four entries are effect/background/metinstone_loop_1..4.mse — three\n"
    "\t// Group Particle blocks over youn_gi.dds, i.e. the yellow aura. The stone's\n"
    "\t// AttachingData holds nothing but collision spheres, so this is its ONLY effect\n"
    "\t// and there is no second one to go looking for.\n"
    "\t//\n"
    "\t// eSmoke is the damage tier (__SetStoneSmokeFlagContainer, just below), the id is\n"
    "\t// CRaceData::m_adwSmokeEffectID[eSmoke] filled by RegisterEffect2 — a zero there\n"
    "\t// means the .msm's effect failed to register, which is a different fault from a\n"
    "\t// valid id that draws nothing.\n"
    "\tif (__FxTrace())\n"
    "\t{\n"
    "\t\tSPDLOG_INFO(\"[fx-stone] create tier {} -> handle {}/{} valid={} (vid {})\",\n"
    "\t\t            eSmoke, m_kStoneSmoke.m_dwEftID.index,\n"
    "\t\t            m_kStoneSmoke.m_dwEftID.generation,\n"
    "\t\t            m_kStoneSmoke.m_dwEftID.valid() ? 1 : 0, GetVirtualID());\n"
    "\t}\n"
    "}\n"
)


def main():
    if not os.path.isfile(CPP_EFF):
        print("FAIL missing %s" % CPP_EFF)
        return 1
    with io.open(CPP_EFF, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        text = f.read()

    if MARKER in text:
        print("[ok  ] already patched")
        return 0

    n = len(PATTERN.findall(text))
    if n != 1:
        print("FAIL pattern matched %d times (expected 1)" % n)
        return 1

    with io.open(CPP_EFF, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(PATTERN.sub(REPL, text, count=1))
    print("[edit] traced __StoneSmoke_Create")
    return 0


if __name__ == "__main__":
    sys.exit(main())
