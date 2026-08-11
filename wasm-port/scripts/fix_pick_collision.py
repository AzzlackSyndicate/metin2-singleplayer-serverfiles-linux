#!/usr/bin/env python3
"""
Idempotent patch: restore character picking / hit detection for every race whose
.msm attaches its collision spheres to a bone (isAttaching 1).

WHY
---
CActorInstance's collision code was ported with a guard that does not exist in the
original client:

    if (!GetModel().GetLODControllerPointer(idx).valid()) return/continue;

On the bgfx backend BgfxModel::GetLODControllerPointer ALWAYS answers an invalid
handle by design (BgfxModelParts.cpp:1875-1890 — "In range, and still an INVALID
handle: no CGrannyLODController exists on this backend"). The original never bails
there: m_LODControllerVector is pre-sized with non-null controllers, and when the
model instance behind one is missing it still falls through to
isAttached = TRUE / bone 0 and creates the sphere
(orig ActorInstanceCollisionDetection.cpp:224-241).

Consequence in the port:
  * CreateCollisionInstancePiece returns false for every ATTACHING collision piece,
    so RefreshActorInstance skips it and m_DefendingPointInstanceList stays EMPTY.
  * CPythonCharacterManager::__UpdatePickedActorList only admits a living instance
    when IntersectDefendingSphere() is true, so such an actor can never be hovered,
    targeted or attacked — while still rendering and still attacking the player.
  * The two UpdatePointInstance sites bail the same way, so even a sphere that did
    get created would never leave the world origin.

Which races are hit is decided purely by the .msm: isAttaching 1 -> broken,
isAttaching 0 -> fine. Measured over the shipped packs:
    monster.zip  31 of 100 msm attaching   (e.g. orc_scouter = race 632/652)
    monster2.zip 12 of  46
    npc.zip      56 of  75                 (e.g. blacksmith, oldster)
    npc2.zip      2 of   2
which is exactly the reported symptom: nearly every NPC, and only some monsters.

The faithful analogue of the original's "model instance is null" bail is the
GetBoneMatrix()/GetCompositeBoneMatrix() failure return that already follows each
site; those are kept. Only the LOD-controller-handle guard is removed.
"""

import re
import sys

PATH = "/opt/m2wasm/src/GameLib/src/ActorInstanceCollisionDetection.cpp"

MARK = "PORT-FIX(pick): LOD-controller guard removed"

# (regex, replacement) — each replacement carries the marker so a second run is a no-op.
PATCHES = [
    # 1) UpdatePointInstance — per-frame sphere placement
    (
        re.compile(
            r"[ \t]*if \(!GetModel\(\)\.GetLODControllerPointer\(pPointInstance\.dwModelIndex\)\.valid\(\)\)\r?\n"
            r"[ \t]*\{\r?\n"
            r"[ \t]*//SPDLOG_TRACE\(\"CActorInstance::UpdatePointInstance[^\n]*\r?\n"
            r"[ \t]*return;\r?\n"
            r"[ \t]*\}\r?\n"
        ),
        "\t\t// " + MARK + ": BgfxModel::GetLODControllerPointer always answers an\n"
        "\t\t// invalid handle (no CGrannyLODController on this backend), so this guard\n"
        "\t\t// returned for EVERY bone-attached collision sphere and left it at the world\n"
        "\t\t// origin. The original bails on a null MODEL INSTANCE, not on the controller\n"
        "\t\t// handle; GetBoneMatrix() below answering false is that same bail.\n",
    ),
    # 2) UpdateAdvancingPointInstance — movement/blocking spheres
    (
        re.compile(
            r"[ \t]*if \(!GetModel\(\)\.GetLODControllerPointer\(rInstance\.dwModelIndex\)\.valid\(\)\)\r?\n"
            r"[ \t]*\{\r?\n"
            r"[ \t]*SPDLOG_TRACE\(\"CActorInstance::UpdateAdvancingPointInstance[^\n]*\r?\n"
            r"[ \t]*continue;\r?\n"
            r"[ \t]*\}\r?\n"
        ),
        "\t\t\t// " + MARK + " (see UpdatePointInstance above).\n",
    ),
    # 3) CreateCollisionInstancePiece — the sphere is never even created
    (
        re.compile(
            r"[ \t]*if \(!GetModel\(\)\.GetLODControllerPointer\(dwAttachingModelIndex\)\.valid\(\)\) return false;\r?\n"
        ),
        "\t\t// " + MARK + ": returning false here dropped the piece entirely, so\n"
        "\t\t// m_DefendingPointInstanceList stayed empty and the actor became unpickable\n"
        "\t\t// and unattackable. The original has no such bail — a missing model instance\n"
        "\t\t// falls through to the else branch below (isAttached = TRUE, bone 0).\n",
    ),
]


def main() -> int:
    with open(PATH, "r", encoding="utf-8", errors="surrogateescape") as fh:
        src = fh.read()

    if MARK in src:
        print("already patched, nothing to do")
        return 0

    out = src
    for i, (rx, rep) in enumerate(PATCHES, 1):
        out, n = rx.subn(rep, out)
        if n != 1:
            print(f"ERROR: patch {i} matched {n} times (expected 1) — file not written",
                  file=sys.stderr)
            return 1
        print(f"patch {i}: ok")

    if re.search(r"!GetModel\(\)\.GetLODControllerPointer\(", out):
        print("ERROR: a GetLODControllerPointer guard survived — file not written",
              file=sys.stderr)
        return 1

    with open(PATH, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        fh.write(out)
    print("written:", PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
