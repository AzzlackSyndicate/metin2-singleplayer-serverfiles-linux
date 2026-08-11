#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_damage_digit_textures.py -- idempotent.

BUG
---
Every damage number reads 000 (or 00). The DIGIT COUNT is right, every digit is
a zero.

CInstanceBase::AddDamageEffect (InstanceBaseEffect.cpp:181-221) builds a number
one digit at a time. Per digit it sets the effect's texture list to that digit's
.dds and immediately creates an instance at the digit's screen position:

    textures.push_back(".../damagevalue/" + strDamageType + "<n>.dds");
    rkEftMgr.SetEffectTextures(ms_adwCRCAffectEffect[rdwCRCEft], textures);
    ...
    rkEftMgr.CreateEffect(ms_adwCRCAffectEffect[rdwCRCEft], ...);
    textures.clear();

BgfxEffectManager::SetEffectTextures wrote the list into `EffectDef::textures`
-- and NOTHING in the render path reads that member. The particle renderer
binds `def.particleTextures[system]` (RenderParticleSystem), which is resolved
once from `def.particles[s].particle.textureFiles` and then latched behind
`def.texturesResolved`. So the override was a complete no-op and every digit
drew whatever the .mse authored, which is the zero. The digit COUNT came out
right because the instances are real and correctly positioned; only their
texture never changed.

WHY THE OBVIOUS FIX IS THE SAME BUG WEARING A HAT
-------------------------------------------------
"Write into def.particles[*].particle.textureFiles and clear texturesResolved"
makes the override reach the renderer -- and makes every simultaneously live
number show the SAME digit, because the resolved handles hang off the
DEFINITION and a definition is shared by every instance created from it. With
three digits created in one loop and then re-resolved, all three would settle on
the last digit written. Different picture, same class of defect, and it would
look "nearly fixed" (777 for 123), which is worse than obviously broken.

>>> THE ORACLE DOES NOT HELP HERE, AND THAT IS WORTH STATING. <<<
CEffectManager::SetEffectTextures (EffectManager.cpp:294-308) calls
CParticleSystemData::ChangeTexture -> CParticleProperty::SetTexture on the
SHARED data, and CParticleSystemInstance::OnRender binds
`m_pParticleProperty->m_ImageVector[...]` from that same shared property. So the
original has the identical aliasing, and copying it faithfully would reproduce
the aliasing rather than the intended picture. This is a place where the
original's structure is not a specification: the damage-number caller is written
as if the texture were captured per instance, so that is what this implements.

FIX
---
The override is captured PER INSTANCE, at CreateEffectInstance, into the slot:

  * `EffectDef::textureOverride` holds the most recent SetEffectTextures list --
    pending, not applied. One entry per particle system, matching the original's
    `GetParticlePointer(i)` indexing.
  * `Slot::particleTextureOverride` holds the handles that instance was born
    with. Render prefers it over the def's list when it has an entry.

`def.texturesResolved` is deliberately NOT reset: the def's own lazy resolve is
about the SCRIPT's textures and is still correct and still shared. The override
is a different lifetime and now has its own storage, so the two no longer fight.

The BgfxResourceRefs go into the same process-lifetime keep-alive the mesh and
particle textures already use -- the digit corpus is ten files per prefix, and
the cache dedupes by normalised path, so the second damage number onwards costs
a map lookup.
"""

import io
import os
import re
import sys

HDR = "/opt/m2wasm/src/EngineLib/src/bgfx/core/BgfxEffectManager.h"
CPP = "/opt/m2wasm/src/EngineLib/src/bgfx/core/BgfxEffectManager.cpp"

EDITS = [
    # ── 1. EffectDef::textureOverride ─────────────────────────────────────
    (
        HDR,
        "std::vector<std::string> textureOverride;",
        re.compile(
            r"        // Texture handles for `particles`, keyed \[system\]\[textureFrame\]\. Resolved by the\n"
            r"        // same lazy pass and under the same UINT16_MAX convention as meshTextures\.\n"
            r"        std::vector<std::vector<uint16_t>> particleTextures;\n"
        ),
        "        // Texture handles for `particles`, keyed [system][textureFrame]. Resolved by the\n"
        "        // same lazy pass and under the same UINT16_MAX convention as meshTextures.\n"
        "        std::vector<std::vector<uint16_t>> particleTextures;\n"
        "\n"
        "        // ── THE DAMAGE-NUMBER OVERRIDE, PENDING RATHER THAN APPLIED ─────────────────\n"
        "        //\n"
        "        // What the last SetEffectTextures(crc, …) asked for, one entry per particle\n"
        "        // system — the same indexing CEffectManager::SetEffectTextures uses when it\n"
        "        // walks `GetParticlePointer(i)` (EffectManager.cpp:303-307).\n"
        "        //\n"
        "        // IT IS NOT APPLIED TO THIS DEF, and that is the whole point. CInstanceBase::\n"
        "        // AddDamageEffect sets it and creates an instance once PER DIGIT, so a def-wide\n"
        "        // application would give every digit of the number the last digit's texture.\n"
        "        // CreateEffectInstance copies it into the slot instead; see\n"
        "        // Slot::particleTextureOverride.\n"
        "        std::vector<std::string> textureOverride;\n",
        1,
    ),
    # ── 2. Slot::particleTextureOverride ──────────────────────────────────
    (
        HDR,
        "particleTextureOverride",
        re.compile(
            r"        std::vector<EffectVertex> geometry;\n"
            r"        EffectDraw                draw;\n"
        ),
        "        std::vector<EffectVertex> geometry;\n"
        "        EffectDraw                draw;\n"
        "\n"
        "        // THE TEXTURES THIS INSTANCE WAS BORN WITH, keyed [system][textureFrame] like\n"
        "        // EffectDef::particleTextures, and empty for the overwhelming majority of\n"
        "        // effects — only the damage-number path sets an override at all.\n"
        "        //\n"
        "        // PER SLOT BECAUSE THE NUMBER 123 IS THREE INSTANCES OF ONE DEF, created in one\n"
        "        // loop with a different texture set before each. Anything hanging off the def\n"
        "        // is shared by all three and they would all show the same digit.\n"
        "        std::vector<std::vector<uint16_t>> particleTextureOverride;\n",
        1,
    ),
    # ── 3. the resolver declaration ───────────────────────────────────────
    (
        HDR,
        "void CaptureTextureOverride",
        re.compile(
            r"    // Set up `slot`'s mesh and texture controllers from `def`, and resolve `def`'s\n"
            r"    // textures if that has not happened yet\. Both are idempotent\.\n"
        ),
        "    // Resolve `def`'s pending textureOverride into `slot`, once, at birth. No-op when\n"
        "    // the def has no override, which is every effect except the damage numbers.\n"
        "    void CaptureTextureOverride(Slot& slot, const EffectDef& def);\n"
        "\n"
        "    // Set up `slot`'s mesh and texture controllers from `def`, and resolve `def`'s\n"
        "    // textures if that has not happened yet. Both are idempotent.\n",
        1,
    ),
    # ── 4. SetEffectTextures records the override ─────────────────────────
    (
        CPP,
        "it->second.textureOverride = textures;",
        re.compile(
            r"    auto it = m_defs\.find\(crc\);\n"
            r"    if \(it == m_defs\.end\(\)\)\n"
            r"        return;\n"
            r"    it->second\.textures = textures;\n"
        ),
        "    auto it = m_defs.find(crc);\n"
        "    if (it == m_defs.end())\n"
        "        return;\n"
        "    // `textures` stays for RegisteredTextures(), which is what the damage-number check\n"
        "    // in effect_checks.inc reads and what GetInfo reports.\n"
        "    it->second.textures = textures;\n"
        "\n"
        "    // >>> AND THE HALF THAT ACTUALLY REACHES A PIXEL. <<<\n"
        "    //\n"
        "    // Writing only the line above made this method a no-op: nothing in the render path\n"
        "    // reads `textures`. RenderParticleSystem binds `particleTextures[system]`, resolved\n"
        "    // once from the SCRIPT and latched behind `texturesResolved`, so every damage digit\n"
        "    // drew the zero the .mse ships with — reported as \"the numbers are always 000\".\n"
        "    //\n"
        "    // Recorded as PENDING and consumed by CreateEffectInstance, because the caller sets\n"
        "    // it once per digit and creates an instance immediately after each set.\n"
        "    it->second.textureOverride = textures;\n",
        1,
    ),
    # ── 5. CreateEffectInstance captures it ───────────────────────────────
    (
        CPP,
        "CaptureTextureOverride(*s, it->second);",
        re.compile(
            r"    auto it = m_defs\.find\(crc\);\n"
            r"    if \(it != m_defs\.end\(\)\)\n"
            r"    \{\n"
            r"        s->draw\.colorOp   = it->second\.colorOp;\n"
            r"        s->draw\.srcBlend  = it->second\.srcBlend;\n"
            r"        s->draw\.destBlend = it->second\.destBlend;\n"
            r"    \}\n"
        ),
        "    auto it = m_defs.find(crc);\n"
        "    if (it != m_defs.end())\n"
        "    {\n"
        "        s->draw.colorOp   = it->second.colorOp;\n"
        "        s->draw.srcBlend  = it->second.srcBlend;\n"
        "        s->draw.destBlend = it->second.destBlend;\n"
        "\n"
        "        // BIRTH IS WHERE THE OVERRIDE IS FROZEN. AddDamageEffect's loop is\n"
        "        // set-texture, create, set-texture, create …, so the def's pending list means\n"
        "        // THIS instance and nothing later.\n"
        "        CaptureTextureOverride(*s, it->second);\n"
        "    }\n",
        1,
    ),
    # ── 6. the resolver itself ────────────────────────────────────────────
    (
        CPP,
        "void BgfxEffectManager::CaptureTextureOverride",
        re.compile(
            r"void BgfxEffectManager::ResolveMeshTextures\(EffectDef& def\)\n"
        ),
        "void BgfxEffectManager::CaptureTextureOverride(Slot& slot, const EffectDef& def)\n"
        "{\n"
        "    slot.particleTextureOverride.clear();\n"
        "    if (def.textureOverride.empty())\n"
        "        return;   // every effect but the damage numbers\n"
        "\n"
        "    // No cache yet means no handles to take. Left EMPTY rather than filled with\n"
        "    // UINT16_MAX: empty means \"this instance has no override\" and Render falls back to\n"
        "    // the def, which is the better of the two pictures.\n"
        "    if (m_texCache == nullptr)\n"
        "        return;\n"
        "\n"
        "    if (!m_meshTextureRefs)\n"
        "        m_meshTextureRefs = std::make_shared<std::vector<BgfxResourceRef>>();\n"
        "    auto& keepAlive = *std::static_pointer_cast<std::vector<BgfxResourceRef>>(m_meshTextureRefs);\n"
        "\n"
        "    // One override entry per particle SYSTEM — CEffectManager::SetEffectTextures walks\n"
        "    // `GetParticlePointer(i)` over the same index, and the damage-number caller passes a\n"
        "    // one-element vector, i.e. it overrides system 0 only.\n"
        "    slot.particleTextureOverride.resize(def.textureOverride.size());\n"
        "    for (size_t i = 0; i < def.textureOverride.size(); ++i)\n"
        "    {\n"
        "        // A single frame, not an animation: the override REPLACES the list, matching\n"
        "        // CParticleProperty::SetTexture, which is the singular counterpart of\n"
        "        // InsertTexture (ParticleProperty.h:37-38).\n"
        "        uint16_t handle = UINT16_MAX;\n"
        "        BgfxResourceRef ref = m_texCache->Acquire(def.textureOverride[i]);\n"
        "        if (ref && ref->IsImage())\n"
        "        {\n"
        "            if (const BgfxTexture* tex = ref->AsTexture())\n"
        "            {\n"
        "                handle = tex->Handle();\n"
        "                // Same process-lifetime keep-alive the script textures use, and for the\n"
        "                // same dangling-handle reason. BOUNDED: ten digit files per damage\n"
        "                // prefix, and Acquire dedupes by normalised path, so this pushes a ref\n"
        "                // per instance but never a payload per instance.\n"
        "                keepAlive.push_back(std::move(ref));\n"
        "            }\n"
        "        }\n"
        "        slot.particleTextureOverride[i].assign(1, handle);\n"
        "    }\n"
        "}\n"
        "\n"
        "void BgfxEffectManager::ResolveMeshTextures(EffectDef& def)\n",
        1,
    ),
    # ── 7. Render prefers the instance's own list ─────────────────────────
    (
        CPP,
        "slot.particleTextureOverride[systemIndex]",
        re.compile(
            r"    const std::vector<uint16_t>\* textures =\n"
            r"        \(systemIndex < def\.particleTextures\.size\(\)\) \? &def\.particleTextures\[systemIndex\] : nullptr;\n"
        ),
        "    // THE INSTANCE'S OWN LIST WINS. Empty for everything except a damage digit, which\n"
        "    // is the one caller that overrides a texture per instance; see\n"
        "    // Slot::particleTextureOverride for why this cannot live on the def.\n"
        "    const std::vector<uint16_t>* textures =\n"
        "        (systemIndex < def.particleTextures.size()) ? &def.particleTextures[systemIndex] : nullptr;\n"
        "    if (systemIndex < slot.particleTextureOverride.size()\n"
        "        && !slot.particleTextureOverride[systemIndex].empty())\n"
        "    {\n"
        "        textures = &slot.particleTextureOverride[systemIndex];\n"
        "    }\n",
        1,
    ),
    # ── 8/9. the slot's override does not outlive the slot ────────────────
    (
        CPP,
        "m_slots[i].particleTextureOverride.clear();",
        re.compile(
            r"            m_slots\[i\]\.geometry\.clear\(\);\n"
            r"            m_slots\[i\]\.draw     = EffectDraw\{\};\n"
        ),
        "            m_slots[i].geometry.clear();\n"
        "            m_slots[i].draw     = EffectDraw{};\n"
        "            // Not the previous tenant's digit.\n"
        "            m_slots[i].particleTextureOverride.clear();\n",
        1,
    ),
    (
        CPP,
        "s->particleTextureOverride.clear();",
        re.compile(
            r"    s->geometry\.clear\(\);\n"
            r"    s->geometry\.shrink_to_fit\(\);\n"
            r"    s->draw = EffectDraw\{\};\n"
        ),
        "    s->geometry.clear();\n"
        "    s->geometry.shrink_to_fit();\n"
        "    s->draw = EffectDraw{};\n"
        "    s->particleTextureOverride.clear();\n"
        "    s->particleTextureOverride.shrink_to_fit();\n",
        1,
    ),
]


def main():
    changed = 0
    already = 0
    failed = []

    for path, marker, pattern, repl, count in EDITS:
        if not os.path.isfile(path):
            failed.append("%s: missing" % path)
            continue
        with io.open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
            text = f.read()

        if marker in text:
            already += 1
            print("[ok  ] already patched: %s" % marker[:56])
            continue

        hits = pattern.findall(text)
        if len(hits) != count:
            failed.append("%s: pattern matched %d times (expected %d): %s"
                          % (os.path.basename(path), len(hits), count, pattern.pattern[:130]))
            continue

        text = pattern.sub(repl, text, count=count)
        with io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(text)
        changed += 1
        print("[edit] patched: %s (%s)" % (marker[:56], os.path.basename(path)))

    print("\nchanged=%d already=%d failed=%d" % (changed, already, len(failed)))
    for f in failed:
        print("FAIL " + f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
