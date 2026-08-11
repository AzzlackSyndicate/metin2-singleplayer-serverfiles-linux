#!/usr/bin/env python3
"""The ageing note still described the tombstone it no longer leaves. Idempotent."""
import io, sys

CPP = "/opt/m2wasm/src/EngineLib/src/bgfx/core/BgfxEffectManager.cpp"
MARKER = "and the slot goes back to the pool WITH it"

OLD = """        // particles left. `alive = false` and NOT a slot release, which is the same state
        // DeactiveEffectInstance leaves behind — the handle stays valid, and the game
        // destroys it when it next asks.
"""

NEW = """        // particles left, and the slot goes back to the pool WITH it — see RetireInstance.
        //
        // THE NOTE HERE USED TO SAY THE OPPOSITE: `alive = false` and deliberately not a
        // slot release, "the handle stays valid, and the game destroys it when it next
        // asks". The game never asks. CreateEffect returns a handle its callers are free to
        // drop, and the damage-number path drops it for every digit of every hit
        // (InstanceBaseEffect.cpp ProcessDamage), so a tombstone nobody collects is a slot
        // burnt for the life of the process. CEffectManager::Update erases the instance and
        // returns it to the pool the moment isAlive() goes false (EffectManager.cpp:66-78),
        // which is the behaviour this now matches.
"""


def main():
    with io.open(CPP, "r", encoding="utf-8") as f:
        text = f.read()
    if MARKER in text:
        print("already patched")
        return
    if text.count(OLD) != 1:
        sys.exit("ABORT: stale-comment anchor matched %d times, expected 1" % text.count(OLD))
    with io.open(CPP, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace(OLD, NEW, 1))
    print("comment corrected")


main()
