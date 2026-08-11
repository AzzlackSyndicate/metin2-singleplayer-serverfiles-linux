#!/usr/bin/env python3
"""
Idempotent patch #3 — REPAIR THE MEASUREMENT, then measure.

The first [pickdiag] round produced 50 lines that looked like findings and were
mostly artefacts of how I wrote the probe. Three defects, all mine:

 1. FIRST-FRAME ONLY. The probe logged once per race, at the first sphere update.
    The log shows 16 of those lines inside ONE millisecond (23:10:41.669) with the
    first `[Deform] Ogre fallback` arriving 4 ms LATER, so every one of them was
    taken before any Deform had run. [M5] answers both bone accessors only after it
    has sampled a pose — BgfxModelSkin.cpp:1303 and :1350 both bail on
    `!st->poseSampled`, and poseSampled is set inside the sampling path (:494,
    :1093). So `GetBoneMatrix=0 GetCompositeBoneMatrix=0` at that instant is the
    CONTRACT, not a defect, and it says nothing about the settled state. This is
    also why the headless check disagreed: it calls bp.Deform() first.

 2. HARDCODED ZEROS ON THE FAILURE PATH. The early returns passed literal
    `false, 0.0f, 0.0f, 0.0f` for the sphere count and position. Every
    `spheres=0 pos=(0.0, 0.0, 0.0)` line is therefore a PLACEHOLDER, not a
    measurement — there is no evidence in that log that those races have no
    spheres. The question "does CreateCollisionInstancePiece arrive with an empty
    SphereDataVector" cannot be answered from it at all.

 3. FLAGS REPORTED FOR A BRANCH THAT NEVER RAN. On an unattached sphere the
    accessors are not called, yet the line still printed `GetBoneMatrix=0`,
    which reads as failure. Those races (the 101-111 / 171-179 village NPCs, the
    14xxx guild races, race 0 = the player) are in fact the HEALTHY ones: their
    positions are real world coordinates.

THIS PATCH
  * TWO lines per race: `first` (call 1) and `steady` (call 120 for that race,
    i.e. well after the model, skeleton and pose exist). One run now shows the
    transient AND the state that actually governs picking.
  * Every field REAL on every path, including both early returns.
  * A `called=` field, so "the accessors were never reached" is distinguishable
    from "the accessors answered false".
"""

import re
import sys

COLL = "/opt/m2wasm/src/GameLib/src/ActorInstanceCollisionDetection.cpp"
MARK = "[pickdiag] TWO lines per race"

HELPER_OLD = re.compile(
    r"// \[pickdiag\] ONE line per race.*?\n\}\n",
    re.S,
)

HELPER_NEW = '''// ''' + MARK + ''': one at the FIRST sphere update and one at the 120th, so a single
// run shows the transient AND the settled state.
//
// THE FIRST VERSION OF THIS PROBE MEASURED A STATE THAT DOES NOT LAST, and reported it
// as if it did. Its 16 opening lines all landed in one millisecond, 4 ms BEFORE the first
// [Deform] — and at that point [M5] has not sampled a pose, so SkinWorldPoseBone and
// SkinCompositeBone are CONTRACTUALLY false (BgfxModelSkin.cpp:1303, :1350; poseSampled
// is set at :494 and :1093, inside the sampling path). "Both accessors say 0" one frame
// after the actor appears is the contract, not a defect. The headless check disagreed
// for exactly this reason: it calls Deform() first.
//
// AND EVERY FIELD IS NOW REAL. The earlier version passed literal zeros for the sphere
// count and position on the failure paths, which made "spheres=0 pos=(0.0, 0.0, 0.0)"
// look like a measurement of an empty sphere vector when it was a placeholder.
//
// `called=0` means the branch that calls the accessors was never entered — an unattached
// sphere never calls them — which is a different thing from them answering false.
//
// The counter counts SPHERE UPDATES for that race, not frames: several actors and several
// point instances all feed it. 120 is simply "long past settling", not a frame number.
static void __PickDiag(DWORD dwRace, DWORD dwModelIndex, DWORD dwBoneIndex,
                       bool isAttached, bool called, bool gotRaw, bool gotComp,
                       size_t sphereCount, float sx, float sy, float sz)
{
	struct SPickDiagRec { DWORD race; int calls; };

	static SPickDiagRec s_akRec[96];
	static int          s_iRecCount = 0;

	SPickDiagRec* pkRec = nullptr;
	for (int i = 0; i < s_iRecCount; ++i)
		if (s_akRec[i].race == dwRace)
		{
			pkRec = &s_akRec[i];
			break;
		}

	if (!pkRec)
	{
		if (s_iRecCount >= 96)
			return;

		pkRec = &s_akRec[s_iRecCount++];
		pkRec->race  = dwRace;
		pkRec->calls = 0;
	}

	++pkRec->calls;

	const char* szTag = nullptr;
	if (1 == pkRec->calls)
		szTag = "first ";
	else if (120 == pkRec->calls)
		szTag = "steady";
	else
		return;

	SPDLOG_INFO("[pickdiag] {} race {} model {} bone {} attached={} called={} "
	            "GetBoneMatrix={} GetCompositeBoneMatrix={} spheres={} "
	            "pos=({:.1f}, {:.1f}, {:.1f})",
	            szTag, dwRace, dwModelIndex, dwBoneIndex,
	            isAttached ? 1 : 0, called ? 1 : 0, gotRaw ? 1 : 0, gotComp ? 1 : 0,
	            sphereCount, sx, sy, sz);
}
'''

FUNC_OLD = re.compile(
    r"void CActorInstance::UpdatePointInstance\(TCollisionPointInstance & pPointInstance\)\n"
    r"\{\n.*?\n\}\n",
    re.S,
)

FUNC_NEW = '''void CActorInstance::UpdatePointInstance(TCollisionPointInstance & pPointInstance)
{
	D3DXMATRIX matBone;

	bool bCalled  = false;
	bool bGotRaw  = false;
	bool bGotComp = false;

	// Reports the REAL sphere state, whichever path we leave by. On the early returns
	// that is the sphere's CURRENT resting place, which is exactly the thing worth
	// knowing: a sphere still sitting at (0, 0, 0) after 120 updates is an actor the
	// mouse ray can never hit.
	auto kPickDiagReport = [&](bool called, bool gotRaw, bool gotComp)
	{
		const size_t nSpheres = pPointInstance.SphereInstanceVector.size();
		const float sx = nSpheres ? pPointInstance.SphereInstanceVector.front().v3Position.x : 0.0f;
		const float sy = nSpheres ? pPointInstance.SphereInstanceVector.front().v3Position.y : 0.0f;
		const float sz = nSpheres ? pPointInstance.SphereInstanceVector.front().v3Position.z : 0.0f;

		__PickDiag(GetRace(), pPointInstance.dwModelIndex, pPointInstance.dwBoneIndex,
		           pPointInstance.isAttached != FALSE, called, gotRaw, gotComp,
		           nSpheres, sx, sy, sz);
	};

	if (pPointInstance.isAttached)
	{
		if (pPointInstance.dwModelIndex>=GetLODControllerCount())
		{
			kPickDiagReport(false, false, false);
			return;
		}

		// PORT-FIX(pick): LOD-controller guard removed: BgfxModel::GetLODControllerPointer
		// always answers an invalid handle (no CGrannyLODController on this backend), so
		// that guard returned for EVERY bone-attached collision sphere and left it at the
		// world origin. The original bails on a null MODEL INSTANCE, not on the controller
		// handle; the two accessors below answering false is that same bail.

		bCalled = true;

		float pRawBoneMatrix[16];
		bGotRaw = GetModel().GetBoneMatrix(pPointInstance.dwModelIndex,
		                                   pPointInstance.dwBoneIndex, pRawBoneMatrix);

		float compositeMatrix[16];
		bGotComp = bGotRaw && GetModel().GetCompositeBoneMatrix(pPointInstance.dwModelIndex,
		                                                        pPointInstance.dwBoneIndex,
		                                                        compositeMatrix);

		if (!bGotRaw || !bGotComp)
		{
			kPickDiagReport(bCalled, bGotRaw, bGotComp);
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

	kPickDiagReport(bCalled, bGotRaw, bGotComp);
}
'''


def main():
    with open(COLL, "r", encoding="utf-8", errors="surrogateescape") as fh:
        src = fh.read()

    if MARK in src:
        print("already patched, nothing to do")
        return 0

    out, n = HELPER_OLD.subn(lambda _m: HELPER_NEW, src)
    if n != 1:
        print(f"ERROR: helper anchor matched {n} times (expected 1)", file=sys.stderr)
        return 1

    out, n = FUNC_OLD.subn(lambda _m: FUNC_NEW, out)
    if n != 1:
        print(f"ERROR: function anchor matched {n} times (expected 1)", file=sys.stderr)
        return 1

    if "__PickDiagOnce" in out:
        print("ERROR: old helper still referenced", file=sys.stderr)
        return 1

    with open(COLL, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        fh.write(out)
    print("written:", COLL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
