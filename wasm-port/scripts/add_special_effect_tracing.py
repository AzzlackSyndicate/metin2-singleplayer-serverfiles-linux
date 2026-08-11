#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
add_special_effect_tracing.py -- idempotent.

INSTRUMENTATION ONLY. No behaviour changes: every line added is inside a
`METIN2_BGFX_FX_NAMES` guard, which is the env var that already gates the effect
census, so with it unset this costs one getenv answered from a static.

WHY, AND WHAT IT DECIDES
------------------------
Two reported symptoms, and neither has a static smoking gun. Three candidate
causes for the critical-hit one were checked against the tree and ELIMINATED
rather than assumed:

  * "the per-file cascade collapses a multi-system effect onto its first
    system's blend" -- critical.mse has six Group Particle blocks, but
    RenderParticleSystem reads `script.particle` PER SYSTEM
    (BgfxEffectManager.cpp:2237-2239), so the collapse the header warns about
    does not reach this path.
  * "the texture name with a space is truncated" -- critical.mse names
    `"D:\\Ymir Work\\pc\\shaman\\effect\\spread copy.dds"`, but the particle
    loader uses the REAL CTextFileLoader (BgfxEffectParticle.cpp:321) and
    CMemoryTextFileLoader::SplitLine2 (FileLoader.cpp:58-67) reads a quoted run
    as one token. The file is in pc.zip under exactly that name.
  * "MaxEmissionCount is ignored, so a 10-spark burst emits 50" -- the cap is
    enforced on the live total (BgfxEffectParticle.cpp:415-420).

So the question moves to runtime, and what has to be separated is:

  A. did the game ever ASK for the effect (does the SE packet arrive, is the
     type registered, does a live instance come back)?
  B. did the instance then get killed by the ageing fold before anything could
     be seen?

(A) is the whole question for the potion, whose effect draws nothing at all;
(B) is the shape that would make a short one-shot -- drugup_red.mse's alpha
curve peaks at t=0.133 and is back to zero by t=0.193 -- disappear between two
frames.

The existing [fx-particle] lines already report blend, colorOp, billboard,
texture handles and quad geometry per system, so nothing new is needed for
"drawn but wrong" once we know the effect reached the manager at all.
"""

import io
import os
import re
import sys

CPP_EFF = "/opt/m2wasm/src/GameLib/src/InstanceBaseEffect.cpp"
CPP_MGR = "/opt/m2wasm/src/EngineLib/src/bgfx/core/BgfxEffectManager.cpp"

EDITS = [
    # ── 1. include <cstdlib> for std::getenv ──────────────────────────────
    (
        CPP_EFF,
        "#include <cstdlib>   // std::getenv — the METIN2_BGFX_FX_NAMES trace guard",
        re.compile(r"#include <cstddef>\n"),
        "#include <cstddef>\n"
        "#include <cstdlib>   // std::getenv — the METIN2_BGFX_FX_NAMES trace guard\n",
        1,
    ),
    # ── 2. AttachSpecialEffect / CreateSpecialEffect ──────────────────────
    (
        CPP_EFF,
        "[fx-special] attach",
        re.compile(
            r"void CInstanceBase::AttachSpecialEffect\(DWORD effect\)\n"
            r"\{\n"
            r"\t__AttachEffect\(effect\);\n"
            r"\}\n"
        ),
        "// THE TRACE GUARD, shared with BgfxEffectManager's census so one env var turns the\n"
        "// whole effect story on. A static so the getenv runs once per process.\n"
        "static bool __FxTrace()\n"
        "{\n"
        "\tstatic const bool s_on = []\n"
        "\t{\n"
        "\t\tconst char* v = std::getenv(\"METIN2_BGFX_FX_NAMES\");\n"
        "\t\treturn v != nullptr && *v != '\\0' && *v != '0';\n"
        "\t}();\n"
        "\treturn s_on;\n"
        "}\n"
        "\n"
        "void CInstanceBase::AttachSpecialEffect(DWORD effect)\n"
        "{\n"
        "\t// >>> WHERE A SPECIAL EFFECT ENTERS THE CLIENT. <<<\n"
        "\t//\n"
        "\t// RecvSpecialEffect (PythonNetworkStreamPhaseGameItem.cpp:884-893) is the only\n"
        "\t// caller that matters here: the potion swirl (SE_HPUP_RED -> EFFECT_HPUP_RED) and\n"
        "\t// the critical burst (SE_CRITICAL -> EFFECT_CRITICAL) both land on this line.\n"
        "\t//\n"
        "\t// It separates the two failures that look identical from outside: a packet that\n"
        "\t// never arrived leaves NO line at all, while a packet that arrived and produced an\n"
        "\t// invalid handle, or a valid handle at crc 0, is an effect-system fault. crc 0\n"
        "\t// specifically means CInstanceBase::RegisterEffect failed for that slot and zeroed\n"
        "\t// it (InstanceBaseEffect.cpp, RegisterEffect's error arm).\n"
        "\tconst EngineLib::EffectHandle h = __AttachEffect(effect);\n"
        "\tif (__FxTrace())\n"
        "\t{\n"
        "\t\tSPDLOG_INFO(\"[fx-special] attach type {} crc {:#010x} bone '{}' -> handle \"\n"
        "\t\t            \"{}/{} valid={} (vid {})\",\n"
        "\t\t            effect,\n"
        "\t\t            (effect < EFFECT_NUM) ? ms_adwCRCAffectEffect[effect] : 0u,\n"
        "\t\t            (effect < EFFECT_NUM) ? ms_astAffectEffectAttachBone[effect] : std::string{},\n"
        "\t\t            h.index, h.generation, h.valid() ? 1 : 0, GetVirtualID());\n"
        "\t}\n"
        "}\n",
        1,
    ),
    (
        CPP_EFF,
        "[fx-special] create",
        re.compile(
            r"\tEngineLib::Engine::Instance\(\)\.GetEffectManager\(\)\.SelectEffectInstance\(dwEffectIndex\);\n"
            r"\tEngineLib::Engine::Instance\(\)\.GetEffectManager\(\)\.SetEffectInstanceGlobalMatrix\(&c_rmatGlobal\._11\);\n"
            r"\}\n"
        ),
        "\tEngineLib::Engine::Instance().GetEffectManager().SelectEffectInstance(dwEffectIndex);\n"
        "\tEngineLib::Engine::Instance().GetEffectManager().SetEffectInstanceGlobalMatrix(&c_rmatGlobal._11);\n"
        "\n"
        "\t// The unattached arm of the same entry point — the firecracker, the spintop and\n"
        "\t// the level-up plates take it (bAttachEffect = false). Position is logged because\n"
        "\t// an instance whose matrix never got pushed sits at the world origin, which at any\n"
        "\t// real player position is outside kEffectCullRadius and invisible for a reason\n"
        "\t// that looks nothing like \"the matrix was not set\".\n"
        "\tif (__FxTrace())\n"
        "\t{\n"
        "\t\tSPDLOG_INFO(\"[fx-special] create type {} crc {:#010x} -> handle {}/{} at \"\n"
        "\t\t            \"({:.0f},{:.0f},{:.0f}) (vid {})\",\n"
        "\t\t            iEffectIndex, dwEffectCRC, dwEffectIndex.index, dwEffectIndex.generation,\n"
        "\t\t            c_rmatGlobal._41, c_rmatGlobal._42, c_rmatGlobal._43, GetVirtualID());\n"
        "\t}\n"
        "}\n",
        1,
    ),
    # ── 3. the ageing fold, both arms ─────────────────────────────────────
    (
        CPP_MGR,
        "[fx-age] one-shot",
        re.compile(
            r"            if \(done && s\.geometry\.empty\(\)\)\n"
            r"                s\.alive = false;\n"
        ),
        "            if (done && s.geometry.empty())\n"
        "            {\n"
        "                // WHEN A ONE-SHOT ENDS, AND AT WHAT AGE. A short effect that is\n"
        "                // reported as \"never appeared\" and one that played correctly in\n"
        "                // under a fifth of a second are the same screenshot; the localTime\n"
        "                // at death is what tells them apart. drugup_red.mse's alpha curve,\n"
        "                // for instance, does not peak until t=0.133.\n"
        "                if (std::getenv(\"METIN2_BGFX_FX_NAMES\") != nullptr)\n"
        "                {\n"
        "                    const auto d = m_defs.find(s.crc);\n"
        "                    SPDLOG_INFO(\"[fx-age] one-shot '{}' (crc {:#010x}) ended at \"\n"
        "                                \"localTime {:.3f} after {} updates — {} mesh \"\n"
        "                                \"controllers, {} particle systems\",\n"
        "                                d != m_defs.end() ? d->second.name : std::string(\"<unregistered>\"),\n"
        "                                s.crc, s.localTime, s.updates,\n"
        "                                s.meshFrame.size(), s.particleSystems.size());\n"
        "                }\n"
        "                s.alive = false;\n"
        "            }\n",
        1,
    ),
    (
        CPP_MGR,
        "[fx-age] elementless",
        re.compile(
            r"            const auto def = m_defs\.find\(s\.crc\);\n"
            r"            if \(def == m_defs\.end\(\)\n"
            r"                \|\| \(def->second\.meshes\.empty\(\) && def->second\.particles\.empty\(\)\)\)\n"
            r"            \{\n"
            r"                s\.alive = false;\n"
            r"            \}\n"
        ),
        "            const auto def = m_defs.find(s.crc);\n"
        "            if (def == m_defs.end()\n"
        "                || (def->second.meshes.empty() && def->second.particles.empty()))\n"
        "            {\n"
        "                // The arm that reaps an instance whose DEF has nothing to draw —\n"
        "                // including the unregistered-crc case, which is what a key mismatch\n"
        "                // used to produce in bulk. Naming it means a future mismatch shows up\n"
        "                // as a named stream rather than as a flat `neither` column.\n"
        "                if (std::getenv(\"METIN2_BGFX_FX_NAMES\") != nullptr)\n"
        "                {\n"
        "                    SPDLOG_INFO(\"[fx-age] elementless instance dropped — crc \"\n"
        "                                \"{:#010x} ({}), {} updates\",\n"
        "                                s.crc,\n"
        "                                def != m_defs.end() ? def->second.name\n"
        "                                                    : std::string(\"NOT REGISTERED\"),\n"
        "                                s.updates);\n"
        "                }\n"
        "                s.alive = false;\n"
        "            }\n",
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
            print("[ok  ] already patched: %s" % marker)
            continue

        hits = pattern.findall(text)
        if len(hits) != count:
            failed.append("%s: pattern matched %d times (expected %d): %s"
                          % (os.path.basename(path), len(hits), count, pattern.pattern[:140]))
            continue

        text = pattern.sub(repl, text, count=count)
        with io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(text)
        changed += 1
        print("[edit] patched: %s (%s)" % (marker, os.path.basename(path)))

    print("\nchanged=%d already=%d failed=%d" % (changed, already, len(failed)))
    for f in failed:
        print("FAIL " + f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
