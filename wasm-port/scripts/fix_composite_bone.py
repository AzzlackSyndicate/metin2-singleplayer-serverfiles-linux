#!/usr/bin/env python3
"""
Idempotent patch #2 for character picking.

THE MISS IN PATCH #1
--------------------
Patch #1 removed the LOD-controller guards, so CreateCollisionInstancePiece now
builds the defending sphere for a bone-attached race. It does not move.

CActorInstance::UpdatePointInstance needs BOTH bone accessors — the composite
supplies the 3x3, the world pose the translation (the original's own split,
ActorInstanceCollisionDetection.cpp:64-70 vs orig :66-70):

    if (!GetModel().GetBoneMatrix(...))          return;   // [M5] arm exists -> OK
    if (!GetModel().GetCompositeBoneMatrix(...)) return;   // NO [M5] arm     -> ALWAYS false

BgfxModel::GetBoneMatrix (BgfxModelMotion.cpp:759-762) tries __BoneWorldPose and
then falls through to SkinWorldPoseBone. BgfxModel::GetCompositeBoneMatrix
(:792-807) reads __BoneWorldPose ALONE. __BoneWorldPose reads m_skeletons, and
that file's own comment (:741-743) states the fact: "NOTHING IN PRODUCTION WRITES
IT — SetSkeletonBoneCount has no caller outside the tests, so after a full .gr2
load of a 66-bone skeleton its size is still 0."

So in production the composite getter returns false for every model, the second
`return` fires every frame, and the sphere never leaves the world origin. The ray
never hits it, and CPythonCharacterManager::__UpdatePickedActorList only admits a
living actor whose defending sphere is hit. Rendered, hostile, unclickable.

[M5] already owns the twin: BgfxModel::SkinCompositeBone (BgfxModelSkin.cpp:1293)
reads GrannyGetWorldPoseComposite4x4Array — which is exactly what the original's
CGrannyModelInstance::GetCompositeBoneMatrixPointer returns
(ModelInstanceModel.cpp:272-276). Adding the fall-through restores parity with the
original AND keeps the pair in one index space: SkinFindBone / SkinWorldPoseBone /
SkinCompositeBone are all [M5], and FindBoneIndex and GetBoneMatrix already fall
through to it.

WHAT THIS PATCH DOES
  A  BgfxModelMotion.cpp   — the [M5] fall-through, symmetric with GetBoneMatrix.
  B  ActorInstanceCollisionDetection.cpp — one [pickdiag] line per race so a single
     in-world run answers "did both accessors answer, and where did the sphere land".
  C  model_checks_m5.inc   — a headless assertion that fails without A and passes
     with it, next to the checks that already pin FindBoneIndex/GetBoneMatrix.
"""

import re
import sys

MARK_A = "PORT-FIX(pick2)"
MARK_B = "[pickdiag]"
MARK_C = "PORT-FIX(pick2) headless"

MOTION = "/opt/m2wasm/src/EngineLib/src/bgfx/models/BgfxModelMotion.cpp"
COLL = "/opt/m2wasm/src/GameLib/src/ActorInstanceCollisionDetection.cpp"
CHECKS = "/opt/m2wasm/tools/bgfxbackend/model_checks_m5.inc"

# ── A ────────────────────────────────────────────────────────────────────────
A_OLD = re.compile(
    r"    float worldPose\[16\];\n"
    r"    if \(!__BoneWorldPose\(modelIndex, static_cast<size_t>\(boneIndex\), worldPose\)\)\n"
    r"        return false;\n"
)

A_NEW = """    float worldPose[16];
    if (!__BoneWorldPose(modelIndex, static_cast<size_t>(boneIndex), worldPose))
    {
        // ── """ + MARK_A + """: THE [M5] ARM, WHICH GetBoneMatrix HAD AND THIS DID NOT. ──
        //
        // __BoneWorldPose reads m_skeletons — [M2]'s store — and the note at the top of
        // GetBoneMatrix already says what that means in production: nothing writes it, so
        // it is EMPTY after a full .gr2 load and this returned false for every real model.
        //
        // GetBoneMatrix was given SkinWorldPoseBone for exactly that reason. Leaving the
        // composite without its twin did not merely halve the fix, because
        // CActorInstance::UpdatePointInstance needs BOTH — composite for the 3x3, world
        // pose for the translation — and bails on the first false. Every bone-attached
        // collision sphere therefore stayed at the WORLD ORIGIN, and picking (which tests
        // the mouse ray against exactly those spheres) could not see the actor: every race
        // whose .msm carries `isAttaching 1` rendered, attacked, and could not be clicked.
        //
        // SkinCompositeBone is GrannyGetWorldPoseComposite4x4Array, which is precisely
        // what the original's GetCompositeBoneMatrixPointer returns
        // (ModelInstanceModel.cpp:272-276) — so this is parity, not a substitute. And the
        // index space matches: SkinFindBone, SkinWorldPoseBone and SkinCompositeBone are
        // one space, and FindBoneIndex and GetBoneMatrix already fall through to it.
        return SkinCompositeBone(boneIndex, outMatrix4x4);
    }
"""

# ── B ────────────────────────────────────────────────────────────────────────
B_OLD = re.compile(
    r"void CActorInstance::UpdatePointInstance\(TCollisionPointInstance & pPointInstance\)\n"
    r"\{\n.*?\n\}\n",
    re.S,
)

B_NEW = '''// ''' + MARK_B + ''' ONE line per race, once per process. This exists to make a single
// in-world run answer the question that two rounds of reasoning could not: do the two
// bone-matrix accessors answer for a bone-attached collision sphere, and where does the
// sphere actually end up? A sphere at (0, 0, 0) is the "unclickable NPC" signature.
// Delete once picking is confirmed in the wild.
static void __PickDiagOnce(DWORD dwRace, DWORD dwModelIndex, DWORD dwBoneIndex,
                           bool isAttached, bool gotRaw, bool gotComp,
                           bool hasSphere, float sx, float sy, float sz)
{
	static DWORD s_adwSeen[96];
	static int   s_iSeen = 0;

	for (int i = 0; i < s_iSeen; ++i)
		if (s_adwSeen[i] == dwRace)
			return;

	if (s_iSeen >= 96)
		return;

	s_adwSeen[s_iSeen++] = dwRace;

	SPDLOG_INFO("[pickdiag] race {} model {} bone {} attached={} GetBoneMatrix={} "
	            "GetCompositeBoneMatrix={} spheres={} pos=({:.1f}, {:.1f}, {:.1f})",
	            dwRace, dwModelIndex, dwBoneIndex, isAttached ? 1 : 0,
	            gotRaw ? 1 : 0, gotComp ? 1 : 0, hasSphere ? 1 : 0, sx, sy, sz);
}

void CActorInstance::UpdatePointInstance(TCollisionPointInstance & pPointInstance)
{
	D3DXMATRIX matBone;

	bool bGotRaw  = false;
	bool bGotComp = false;

	if (pPointInstance.isAttached)
	{
		if (pPointInstance.dwModelIndex>=GetLODControllerCount())
		{
			__PickDiagOnce(GetRace(), pPointInstance.dwModelIndex, pPointInstance.dwBoneIndex,
			               true, false, false, false, 0.0f, 0.0f, 0.0f);
			return;
		}

		// PORT-FIX(pick): LOD-controller guard removed: BgfxModel::GetLODControllerPointer
		// always answers an invalid handle (no CGrannyLODController on this backend), so
		// that guard returned for EVERY bone-attached collision sphere and left it at the
		// world origin. The original bails on a null MODEL INSTANCE, not on the controller
		// handle; the two accessors below answering false is that same bail.

		float pRawBoneMatrix[16];
		bGotRaw = GetModel().GetBoneMatrix(pPointInstance.dwModelIndex,
		                                   pPointInstance.dwBoneIndex, pRawBoneMatrix);

		float compositeMatrix[16];
		bGotComp = bGotRaw && GetModel().GetCompositeBoneMatrix(pPointInstance.dwModelIndex,
		                                                        pPointInstance.dwBoneIndex,
		                                                        compositeMatrix);

		if (!bGotRaw || !bGotComp)
		{
			__PickDiagOnce(GetRace(), pPointInstance.dwModelIndex, pPointInstance.dwBoneIndex,
			               true, bGotRaw, bGotComp, false, 0.0f, 0.0f, 0.0f);
			return;
		}

		D3DXMATRIX rawBoneMat;
		memcpy(&rawBoneMat, pRawBoneMatrix, sizeof(rawBoneMat));

		memcpy(&matBone, compositeMatrix, sizeof(matBone));
		matBone._41 = rawBoneMat._41;
		matBone._42 = rawBoneMat._42;
		matBone._43 = rawBoneMat._43;
		matBone *= m_worldMatrix;
	}
	else
	{
		matBone = m_worldMatrix;
	}

	// Update Collsion Sphere
	CSphereCollisionInstanceVector::const_iterator sit = pPointInstance.c_pCollisionData->SphereDataVector.begin();
	CDynamicSphereInstanceVector::iterator dit=pPointInstance.SphereInstanceVector.begin();
	for (;sit!=pPointInstance.c_pCollisionData->SphereDataVector.end();++sit,++dit)
	{
		const TSphereData & c = sit->GetAttribute();//c_pCollisionData->SphereDataVector[j].GetAttribute();

		D3DXMATRIX matPoint;
		D3DXMatrixTranslation(&matPoint, c.v3Position.x, c.v3Position.y, c.v3Position.z);
		matPoint = matPoint * matBone;

		dit->v3LastPosition = dit->v3Position;
		dit->v3Position.x = matPoint._41;
		dit->v3Position.y = matPoint._42;
		dit->v3Position.z = matPoint._43;
	}

	{
		const bool hasSphere = !pPointInstance.SphereInstanceVector.empty();
		const float sx = hasSphere ? pPointInstance.SphereInstanceVector.front().v3Position.x : 0.0f;
		const float sy = hasSphere ? pPointInstance.SphereInstanceVector.front().v3Position.y : 0.0f;
		const float sz = hasSphere ? pPointInstance.SphereInstanceVector.front().v3Position.z : 0.0f;
		__PickDiagOnce(GetRace(), pPointInstance.dwModelIndex, pPointInstance.dwBoneIndex,
		               pPointInstance.isAttached != FALSE, bGotRaw, bGotComp,
		               hasSphere, sx, sy, sz);
	}
}
'''

# ── C ────────────────────────────────────────────────────────────────────────
C_ANCHOR = (
    '                  "imodel=" + std::to_string(gotIModel ? 1 : 0) +\n'
    '                  " skin=" + std::to_string(gotSkin ? 1 : 0) +\n'
    '                  " max|delta| = " + std::to_string(maxDelta));\n'
)

C_ADD = '''
            // ── ''' + MARK_C + ''': AND THE COMPOSITE GETTER NEEDS THE SAME TWIN ARM.
            //    THIS IS WHERE IT WAS MISSING, AND UNCLICKABLE NPCs ARE WHAT IT COST. ──
            //
            // GetBoneMatrix got its [M5] fall-through. GetCompositeBoneMatrix did not: it
            // read __BoneWorldPose alone, i.e. [M2]'s m_skeletons, which production never
            // fills — so it answered FALSE for every real model.
            //
            // CActorInstance::UpdatePointInstance needs BOTH (composite for the 3x3, world
            // pose for the translation) and returns on the first false, so every
            // bone-attached collision sphere stayed at the WORLD ORIGIN. Picking tests the
            // mouse ray against exactly those spheres
            // (CPythonCharacterManager::__UpdatePickedActorList admits a living actor only
            // when IntersectDefendingSphere is true), so every race whose .msm carries
            // `isAttaching 1` — 56 of 75 shapes in npc.zip, 31 of 100 in monster.zip —
            // rendered and attacked while being impossible to hover, target or hit.
            //
            // Asserted by IDENTITY against [M5]'s own composite, not by "returns true":
            // an arm that answered some OTHER matrix would put the sphere on a random bone.
            float viaIModelComp[16] = {};
            float viaSkinComp[16]   = {};
            const bool gotIModelComp = bp.GetCompositeBoneMatrix(0u, viaIModel, viaIModelComp);
            const bool gotSkinComp   = bp.SkinCompositeBone(handBone, viaSkinComp);
            float compMaxDelta = 0.0f;
            for (int mi = 0; mi < 16; ++mi)
                compMaxDelta = std::max(compMaxDelta,
                                        std::fabs(viaIModelComp[mi] - viaSkinComp[mi]));
            Check(">>> [M5] IModel::GetCompositeBoneMatrix ANSWERS through [M5], with the "
                  "SAME matrix as SkinCompositeBone. Without that arm it is false for every "
                  "production model, every bone-attached collision sphere sits at the world "
                  "origin, and the NPC cannot be clicked <<<",
                  gotIModelComp && gotSkinComp && compMaxDelta == 0.0f,
                  "imodel=" + std::to_string(gotIModelComp ? 1 : 0) +
                  " skin=" + std::to_string(gotSkinComp ? 1 : 0) +
                  " max|delta| = " + std::to_string(compMaxDelta));
'''


def patch(path, mark, apply_fn):
    with open(path, "r", encoding="utf-8", errors="surrogateescape") as fh:
        src = fh.read()
    if mark in src:
        print(f"  {path}: already patched")
        return True
    out = apply_fn(src)
    if out is None:
        print(f"  ERROR {path}: anchor not found / not unique", file=sys.stderr)
        return False
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        fh.write(out)
    print(f"  {path}: patched")
    return True


def do_a(src):
    out, n = A_OLD.subn(A_NEW, src)
    return out if n == 1 else None


def do_b(src):
    out, n = B_OLD.subn(lambda _m: B_NEW, src)
    return out if n == 1 else None


def do_c(src):
    if src.count(C_ANCHOR) != 1:
        return None
    return src.replace(C_ANCHOR, C_ANCHOR + C_ADD)


def main():
    ok = True
    print("A: [M5] arm for GetCompositeBoneMatrix")
    ok &= patch(MOTION, MARK_A, do_a)
    print("B: [pickdiag] instrumentation in UpdatePointInstance")
    ok &= patch(COLL, MARK_B, do_b)
    print("C: headless assertion in model_checks_m5.inc")
    ok &= patch(CHECKS, MARK_C, do_c)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
