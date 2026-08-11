#!/usr/bin/env python3
"""
Damage numbers (and every other one-shot effect) that were born off-screen never die.

Three defects on the ageing/cleanup path, all of them in BgfxEffectManager.cpp:

 1. AGEING DEPENDED ON BEING DRAWN.  `slot.particleSystems` and the mesh/texture frame
    controllers were built by PrepareMeshInstance, which is called from Render() AFTER the
    frustum-cull `continue`.  The ageing fold needs a controller to exist before it will
    judge an instance.  So an instance culled on every frame of its life never got one and
    was immortal — the file already stated this as a KNOWN RESIDUAL.  A damage number is
    spawned at the mob's head at the moment of the hit, which is exactly when the camera
    may be pointed elsewhere, so the digits of a fight at the edge of the view became
    permanent; swinging the camera back over that spot later is what makes them "flash up"
    long after combat ended.  CEffectInstance::OnUpdate ages in the UPDATE, with no
    reference to visibility, which is why the original does not have this failure.

 2. DYING ONLY MARKED, IT DID NOT FREE.  The rule set `alive = false` and left
    `used = true`, on the reasoning that "the game destroys it when it next asks".
    CreateEffect's callers discard the handle — CInstanceBase::ProcessDamage does, for
    every digit of every hit — so nobody ever asks, and the slot was burnt for the life of
    the process.  CEffectManager::Update erases the instance and returns it to the pool the
    moment isAlive() goes false (EffectManager.cpp:66-78); this is that erase.

 3. AcquireSlot HANDED ON THE PREVIOUS TENANT'S SIMULATION.  It cleared used/alive/crc/
    geometry/draw but NOT particleSystems, meshFrame, textureFrame or localTime — and
    PrepareMeshInstance rebuilds those only when the COUNT differs from the new def's.
    Almost every one-shot has exactly one particle group, so a reused slot would have
    inherited a finished system pointing at the old def's script: no emission, and instant
    death by the ageing fold.  Latent while nothing was ever freed; a guaranteed
    "effects stopped appearing" regression the moment (2) makes reuse real.

Idempotent: `RetireInstance` is unique file-wide in both files.
"""

import io, sys

CPP = "/opt/m2wasm/src/EngineLib/src/bgfx/core/BgfxEffectManager.cpp"
HDR = "/opt/m2wasm/src/EngineLib/src/bgfx/core/BgfxEffectManager.h"
MARKER = "RetireInstance"


def read(p):
    with io.open(p, "r", encoding="utf-8") as f:
        return f.read()


def write(p, s):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


def sub_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        sys.exit("ABORT: anchor for '%s' matched %d times, expected exactly 1" % (what, n))
    return text.replace(old, new, 1)


# ─────────────────────────────────────────────────────────────────── header ──
hdr = read(HDR)
if MARKER in hdr:
    print("header: already patched")
else:
    hdr = sub_once(
        hdr,
        "    void PrepareMeshInstance(Slot& slot, EffectDef& def);\n",
        "    void PrepareMeshInstance(Slot& slot, EffectDef& def);\n"
        "    // Return a dead instance's slot to the pool: CEffectManager::Update's\n"
        "    // `erase` + `CEffectInstance::Delete`, which this backend had replaced with a\n"
        "    // tombstone that was never collected. Bumps the generation, so every handle\n"
        "    // onto the slot goes stale before it can be handed out again.\n"
        "    void RetireInstance(Slot& slot);\n",
        "RetireInstance decl",
    )
    write(HDR, hdr)
    print("header: patched")

# ────────────────────────────────────────────────────────────────────── cpp ──
cpp = read(CPP)
if MARKER in cpp:
    print("cpp: already patched")
    sys.exit(0)

# ── 1. build the controllers in Update, so ageing does not depend on drawing ──
cpp = sub_once(
    cpp,
    """        ++s.updates;
        s.localTime += elapsed;

        for (EffectFrameController& fc : s.meshFrame)
""",
    """        ++s.updates;
        s.localTime += elapsed;

        // >>> THE CONTROLLERS ARE BUILT HERE, NOT ONLY WHERE THE INSTANCE IS DRAWN. <<<
        //
        // Render() calls PrepareMeshInstance too, but it calls it AFTER the frustum-cull
        // `continue`, and the ageing fold below refuses to judge an instance that has no
        // controller. An instance culled on every frame of its life therefore never got
        // one and could never die — stated as a KNOWN RESIDUAL where the fold is written,
        // and measured as the damage digits of an off-screen fight sticking in the world
        // and flashing back up whenever the camera later swings over that spot.
        //
        // FEffectUpdator runs over the element instances from CEffectInstance::OnUpdate
        // (EffectInstance.cpp:74-101) with no reference to visibility whatever, so ageing
        // in the update IS the oracle's shape; the cull is a drawing decision and only a
        // drawing decision. PrepareMeshInstance is idempotent, so Render's call stays.
        //
        // GUARDED ON THE CACHE, because PrepareMeshInstance resolves textures and
        // ResolveMeshTextures records a miss PERMANENTLY (it sets texturesResolved before
        // it checks m_texCache). Update() is reachable from Python — effect.Update() and
        // fly.Update() — which can run at a loading screen before the cache is attached,
        // and resolving there would leave every effect white for the rest of the process.
        // Render cannot run that early, which is why its call needs no such guard.
        if (m_texCache != nullptr)
        {
            const auto defIt = m_defs.find(s.crc);
            if (defIt != m_defs.end())
                PrepareMeshInstance(s, defIt->second);
        }

        for (EffectFrameController& fc : s.meshFrame)
""",
    "Update builds controllers",
)

# ── 2a. the one-shot arm frees instead of marking ──
cpp = sub_once(
    cpp,
    """                s.alive = false;
            }
        }
        // ── AN INSTANCE WITH NO ELEMENTS AT ALL, WHICH THE FOLD ABOVE CANNOT REACH ─────
""",
    """                RetireInstance(s);
            }
        }
        // ── AN INSTANCE WITH NO ELEMENTS AT ALL, WHICH THE FOLD ABOVE CANNOT REACH ─────
""",
    "one-shot arm retires",
)

# ── 2b. the elementless arm frees too ──
cpp = sub_once(
    cpp,
    """                s.alive = false;
            }
        }
    }
}

void BgfxEffectManager::Render()
""",
    """                RetireInstance(s);
            }
        }
    }
}

void BgfxEffectManager::Render()
""",
    "elementless arm retires",
)

# ── 2c. the helper, and DestroyEffectInstance routed through it ──
cpp = sub_once(
    cpp,
    """void BgfxEffectManager::DestroyEffectInstance(EffectHandle handle)
{
    Slot* s = Resolve(handle.index, handle.generation);
    if (s == nullptr)
        return;   // CEffectManager::DestroyEffectInstance answers a bool nobody here can
                  // return; a stale index is a no-op there too (EffectManager.cpp map miss).

    if (m_selected == handle.index)
        m_selected = 0xFFFFFFFFu;

    // Bump on RELEASE so every outstanding handle onto this slot goes stale immediately,
    // before the slot can be handed to someone else. Generation 0 is never handed out — it
    // is Resolve's "index only, skip the check" sentinel — so wrap skips it.
    s->used    = false;
    s->alive   = false;
    s->visible = true;
    s->crc     = 0;
    s->updates = 0;
    s->soundFrame = 0;   // CEffectInstance::Clear -> __Initialize:283
    s->geometry.clear();
    s->geometry.shrink_to_fit();
    s->draw = EffectDraw{};
    s->particleTextureOverride.clear();
    s->particleTextureOverride.shrink_to_fit();
    ++s->generation;
    if (s->generation == 0)
        s->generation = 1;
}
""",
    """void BgfxEffectManager::RetireInstance(Slot& slot)
{
    // WHY FREEING IS SAFE HERE, since the note this replaces argued the opposite.
    //
    // Every handle the engine hands out is an EffectHandle — index AND generation — and the
    // bump at the bottom makes all of them stale the instant the slot is freed, so a later
    // DestroyEffectInstance / SetPosition / SelectEffectInstance on a retired instance
    // resolves to nullptr and is the no-op it already is for a stale handle. That is
    // STRICTLY safer than the oracle, whose m_kEftInstMap is keyed by a bare index and
    // whose __GetEmptyIndex hands the same integer straight back out (EffectManager.cpp).
    //
    // ONE CONSUMER STILL DROPS THE GENERATION and is worth naming rather than leaving to be
    // rediscovered: ActorInstanceAttach.cpp:595 asks IsAliveEffect(dwEffectIndex.index),
    // the raw-index arm. Against a re-tenanted slot that answers about the NEW occupant, so
    // an attachment entry can outlive its effect. It is bounded — the following
    // SelectEffectInstance IS generation-checked and fails — it is the oracle's own
    // behaviour, and it is unreachable for the effects that populate that list in practice,
    // which loop and so never reach this fold. It belongs to whoever owns that file.
    //
    // m_selected is an INDEX with no generation (Resolve's `0` sentinel), so it cannot be
    // left pointing at a freed slot the way a handle can.
    const size_t index = size_t(&slot - m_slots.data());
    if (m_selected == unsigned(index))
        m_selected = 0xFFFFFFFFu;

    slot.used    = false;
    slot.alive   = false;
    slot.visible = true;
    slot.crc     = 0;
    slot.updates = 0;
    slot.soundFrame = 0;   // CEffectInstance::Clear -> __Initialize:283
    slot.localTime  = 0.0f;
    slot.geometry.clear();
    slot.geometry.shrink_to_fit();
    slot.draw = EffectDraw{};
    slot.particleTextureOverride.clear();
    slot.particleTextureOverride.shrink_to_fit();
    // THE SIMULATION GOES WITH THE SLOT. PrepareMeshInstance rebuilds these only when the
    // COUNT disagrees with the new def's, and one particle group is the commonest shape in
    // the corpus — so a slot handed on with its systems intact would give the next tenant a
    // FINISHED system pointing into the previous def's script: it would emit nothing and
    // the fold above would kill it on its second update.
    slot.particleSystems.clear();
    slot.particleSystems.shrink_to_fit();
    slot.meshFrame.clear();
    slot.meshFrame.shrink_to_fit();
    slot.textureFrame.clear();
    slot.textureFrame.shrink_to_fit();

    // Bump on RELEASE so every outstanding handle onto this slot goes stale immediately,
    // before the slot can be handed to someone else. Generation 0 is never handed out — it
    // is Resolve's "index only, skip the check" sentinel — so wrap skips it.
    ++slot.generation;
    if (slot.generation == 0)
        slot.generation = 1;
}

void BgfxEffectManager::DestroyEffectInstance(EffectHandle handle)
{
    Slot* s = Resolve(handle.index, handle.generation);
    if (s == nullptr)
        return;   // CEffectManager::DestroyEffectInstance answers a bool nobody here can
                  // return; a stale index is a no-op there too (EffectManager.cpp map miss).

    RetireInstance(*s);
}
""",
    "RetireInstance + DestroyEffectInstance",
)

# ── 3. AcquireSlot must not hand on the previous tenant's simulation ──
cpp = sub_once(
    cpp,
    """            // Not the previous tenant's digit.
            m_slots[i].particleTextureOverride.clear();
            return unsigned(i);
""",
    """            // Not the previous tenant's digit.
            m_slots[i].particleTextureOverride.clear();
            // >>> NOR THE PREVIOUS TENANT'S SIMULATION. <<< RetireInstance already clears
            // these on the path that frees a slot; this is the invariant's real home, and
            // it covers any future path that frees one without going through it. See the
            // note there for what a handed-on particle system does to the next tenant.
            m_slots[i].particleSystems.clear();
            m_slots[i].meshFrame.clear();
            m_slots[i].textureFrame.clear();
            m_slots[i].localTime = 0.0f;
            return unsigned(i);
""",
    "AcquireSlot clears simulation",
)

write(CPP, cpp)
print("cpp: patched")
