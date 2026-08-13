#!/usr/bin/env python3
"""Two attachments the port creates and never takes away, and both were the port's.

Three visual defects were reported together -- a weapon that keeps rendering in
the hand after it is unequipped, a metin stone's aura that lands on the player,
and hit flashes that behave oddly. The first two turned out to share a shape and
both were settled by reading the ORIGINAL, which is in the same tree. The third
did not: see the last section, which is a diagnosis and not a change.


### THE WEAPON THAT SURVIVES UNEQUIPPING

Unequipping runs CInstanceBase::SetWeapon(0) -> CActorInstance::AttachWeapon(0,
PART_MAIN, PART_WEAPON) (ActorInstanceAttach.cpp:145). Item 0 has no CItemData,
so it takes the clear arm, which does exactly two things per hand:

    GetModel().RegisterModelThing(part, EngineLib::GraphicThingHandle{});
    GetModel().SetModelInstance(part, part, 0);

Both write into [M1]'s slot tables. On Eter that IS the removal -- the model
thing is the geometry. On bgfx it is not: RegisterModelThing resolves to nothing
there, which is the whole reason the port added the by-path twin
LoadModelPartFromPath a few lines below (ActorInstanceAttach.cpp:204-223), and
that route puts the geometry into m_m3Parts. m_m3Parts is what BgfxModel::Render,
BlendRender and Deform walk (BgfxModelRender.cpp:312, :751, :855, :941, :1036,
:1122); nothing in the render path consults modelInstances at all. So the clear
arm clears a table the renderer never reads, and the sword stays in m_m3Parts --
still carrying its AttachModelInstance record, which is why it stays in the HAND
rather than falling to the origin.

The port gave m_m3Parts exactly two removal routes and neither can serve here:
ClearParts() drops every part (it is SetRace's Clear) and ErasePreviousPart()
deliberately keeps the newest occupant of a slot, because it exists to make a
re-equip replace rather than append. There was no "empty this slot". This adds
one, as IModel::ClearModelPart -- default false, exactly like LoadModelPartFromPath
and BuildBoundsFromGeometry beside it, so Eter is untouched and keeps the handle
route that already works there -- and calls it from the clear arm next to each of
the two RegisterModelThing calls it mirrors.

RULED OUT on the way: that the clear arm was not being reached (it is -- SetWeapon
passes eWeapon straight through and __IsChangableWeapon forces 0), and that
SetModelInstance's inst.set flag gates drawing (it does not; the render loop never
looks at it).


### THE METIN'S AURA ON THE PLAYER

CEffectManager::SelectEffectInstance, EffectManager.cpp:280-292, in this tree:

    m_pSelectedEffectInstance = nullptr;                  <- FIRST, unconditionally
    if (m_kEftInstMap.end() == itor) return FALSE;
    m_pSelectedEffectInstance = itor->second;

A failed Select leaves NOTHING selected, so every no-argument call after it --
SetEffectInstanceGlobalMatrix, ShowEffect, HideEffect -- is a no-op. The port
dropped that line and documented the opposite ("leaves the previous selection
alone"), which is not what the file it cites does.

It matters because the callers act with no argument and do not test the return:

    CActorInstance::UpdateAttachingInstances   SelectEffectInstance, then
      SetEffectInstanceGlobalMatrix unconditionally (ActorInstanceAttach.cpp:604-635)
    CActorInstance::ShowAllAttachingEffect / HideAllAttachingEffect
      the same shape with ShowEffect / HideEffect, and no liveness gate at all

So on a failed Select, actor A's world matrix is written onto whatever effect was
selected earlier in the same frame -- actor B's. That is an aura moving to the
wrong character, which is the report.

WHY IT IS REACHABLE, and this is the port-specific half. The original's
GetEmptyIndex (EffectManager.cpp:390-402) is a monotonically increasing counter
that only wraps past 2.1 billion, so an index is never handed out twice and a
stale index can only ever MISS the map. The port pools slots and replaces that
invariant with a generation counter -- and then, one line before the Select,
UpdateAttachingInstances asks IsAliveEffect(handle.index), the raw-index arm,
which answers about whichever tenant holds the slot NOW. That gate passes exactly
the stale handles the generation-checked Select then rejects. Slot re-tenanting
became brisk when BgfxEffectManager::Update started retiring finished one-shots
(the fold at BgfxEffectManager.cpp:1470-1520), so the window is widest around
effect-heavy actors and in combat -- a metin stone's aura is one attaching effect
that loops forever (__StoneSmoke_Create -> AttachSmokeEffect -> AttachEffectByID),
while combat creates and retires an instance per hit and per damage digit.

RULED OUT on the way: that the aura is a second, separate stone effect (it is not;
__StoneSmoke_Create's handle is the stone's only effect, and the .msm's four
"smoke" entries are the metinstone_loop particle scripts).

NOT CHANGED, and named so it is not rediscovered: the raw-index liveness gate at
ActorInstanceAttach.cpp:595 still lets a stale attaching entry survive in the list.
With this fix that entry is an inert no-op forever rather than a wrong-owner write,
which is the original's own behaviour for a stale index. Swapping it for the
handle-based IsEffectInstanceAlive is NOT safe as it stands: EterEffectManager
resolves that one through EffectRegistry() while its GetEmptyIndex hands out a bare
CEffectManager index, so the two live in different handle spaces and the Eter
backend would start reaping every attaching effect on its first frame.

THE MEASUREMENT that confirms the aura half from a running client: set
METIN2_BGFX_FX_NAMES=1 and read BgfxEffectManager's live-effects roll, which prints
each effect name with an instance count and a position. Stand near a metin stone
and move; before this change metinstone_loop's @(x,y,z) follows the player, after
it stays on the stone.


### THE ODD HIT FLASHES -- NOT CHANGED, BECAUSE THE CAUSE IS NOT YET KNOWN

The hit flash is CActorInstance::__HitEffect, ActorInstanceBattle.cpp:674-686:
a free-standing one-shot, `CreateEffect(m_dwBattleHitEffectID, x, y, z, 0, 0,
fHeight)`. It never enters an attaching list, so the wrong-owner mechanism above
does not reach it -- these two items are NOT the same bug, which is the first
thing this looked for and did not find.

Also ruled out, so nobody spends the time again:

  * The rotation argument is not mistranslated. The original builds
    D3DXMatrixRotationYawPitchRoll(0, 0, toRad(z)), which is a pure Z rotation;
    Bgfx3D::MakeObjectWorld builds bx::mtxRotateZ(-toRad(z)) and negates
    deliberately, with the sign pinned by a smoke test (Bgfx3D.cpp:991-1004).
    They agree.
  * The "attach a second effect to the victim" arm is dead code on both sides:
    SetBattleAttachEffect exists but is called from nowhere in the tree, so
    m_dwBattleAttachEffectID is always 0 and only the free-standing flash fires.

TWO SUSPECTS REMAIN, both in BgfxEffectManager and both cheap to distinguish:

  1. A recycled slot inherits the previous tenant's world BASIS. AcquireSlot
     (~:866-887) and RetireInstance (~:1093-1147) reset every other field of Slot
     and neither resets `world`, whose only default is the identity a fresh slot is
     constructed with. The six-argument CreateEffect rewrites the whole matrix and
     is therefore immune -- which is why this is listed and not applied here -- but
     the three-argument one only writes the translation (SetPosition), so
     BgfxSceneTerrain.cpp:1125 and PythonItem.cpp:341 draw with whatever rotation
     the slot last held. One line in AcquireSlot, once someone has seen it.
  2. The ageing fold's own stated residual: controllers are built in Render, AFTER
     the frustum cull, and the fold refuses to age an instance that has none. A
     one-shot spawned off-screen therefore never dies -- already measured for the
     damage digits, "sticking in the world and flashing back up whenever the camera
     later swings over that spot".

THE MEASUREMENT that separates them: the same METIN2_BGFX_FX_NAMES=1 roll, read
through a fight. A hit-effect count that climbs and never falls is (2); a correct
count with a wrong @(x,y,z) or a visibly wrong orientation is a transform question
and (1) is where to start.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")

# (path, marker that only exists AFTER patching, old text, new text)
EDITS = [
    # ── item 2: a failed Select must not leave the previous effect selected ──
    (
        "src/EngineLib/src/bgfx/core/BgfxEffectManager.cpp",
        "A FAILED SELECT CLEARS THE SELECTION",
        """bool BgfxEffectManager::SelectEffectInstance(EffectHandle handle)
{
    // The stateful indexed API: select, then act with no argument. Returns FALSE in the
    // original when the index is not in the instance map (EffectManager.cpp), so a stale or
    // unused slot answers false here and leaves the previous selection alone — clearing it
    // would make a failed Select silently retarget the next ShowEffect.
    if (Resolve(handle.index, handle.generation) == nullptr)
        return false;
""",
        """bool BgfxEffectManager::SelectEffectInstance(EffectHandle handle)
{
    // >>> A FAILED SELECT CLEARS THE SELECTION, AND THE NOTE THAT STOOD HERE ASSERTED
    // >>> THE OPPOSITE ABOUT AN ORIGINAL THAT IS IN THIS TREE. <<<
    //
    // CEffectManager::SelectEffectInstance (EffectManager.cpp:280-292) writes
    // `m_pSelectedEffectInstance = nullptr` BEFORE it looks the index up, so a miss leaves
    // nothing selected and every no-argument call that follows is a no-op. Keeping the
    // previous selection does not make a failed Select harmless; it makes it RETARGET, and
    // the callers act with no argument straight afterwards without testing the return:
    // UpdateAttachingInstances follows its Select with SetEffectInstanceGlobalMatrix
    // unconditionally, and ShowAllAttachingEffect / HideAllAttachingEffect do the same with
    // ShowEffect / HideEffect and no liveness gate at all. So actor A's world matrix landed
    // on the effect actor B selected earlier in the same frame — an aura on the wrong
    // character, which is how it was reported.
    //
    // AND IT IS REACHABLE ONLY BECAUSE THIS BACKEND POOLS SLOTS. The original's
    // GetEmptyIndex (EffectManager.cpp:390-402) is a monotonic counter, so a stale index
    // can only ever miss the map; the generation counter here is what replaces that
    // invariant. One line before the Select, UpdateAttachingInstances asks
    // IsAliveEffect(handle.index) — the raw-index arm, which answers about whichever tenant
    // holds the slot now — and that gate passes exactly the handles this Select rejects.
    m_selected = 0xFFFFFFFFu;
    if (Resolve(handle.index, handle.generation) == nullptr)
        return false;
""",
    ),

    # ── item 1: the backend seam that can empty one part slot ──
    (
        "src/EngineLib/include/EngineLib/backend/IModel.h",
        "virtual bool ClearModelPart",
        """    virtual bool LoadModelPartFromPath(uint32_t /*partIndex*/, std::string_view /*path*/)
    {
        return false;
    }
""",
        """    virtual bool LoadModelPartFromPath(uint32_t /*partIndex*/, std::string_view /*path*/)
    {
        return false;
    }

    // ── AND THE WAY BACK OUT OF A PART SLOT ─────────────────────────────────
    //
    // LoadModelPartFromPath's inverse, and it exists for the same reason: the pair of
    // calls that empties a slot on Eter — RegisterModelThing with a null handle, then
    // SetModelInstance — writes a table a backend without an Eter resource layer does
    // not draw from. Unequipping a weapon ran exactly that pair and the sword went on
    // rendering in the hand, because the geometry the by-path route loaded was never
    // asked to leave.
    //
    // DEFAULT false, like the two above, and for the same reason: on Eter the handle
    // route IS the removal, so there is nothing here to do and false is the truthful
    // answer rather than a gap. A backend that has nothing in that slot also answers
    // false; "did anything go" is the only thing the return means.
    virtual bool ClearModelPart(uint32_t /*partIndex*/) { return false; }
""",
    ),
    (
        "src/EngineLib/src/bgfx/models/BgfxModel.h",
        "bool ClearModelPart(uint32_t partIndex) override;",
        """    bool LoadModelPartFromPath(uint32_t partIndex, std::string_view path) override;
""",
        """    bool LoadModelPartFromPath(uint32_t partIndex, std::string_view path) override;
    // Its inverse, and the one thing ClearParts and ErasePreviousPart between them could
    // not express — see IModel.h. Defined in BgfxModelParts.cpp beside ErasePreviousPart.
    bool ClearModelPart(uint32_t partIndex) override;
""",
    ),
    (
        "src/EngineLib/src/bgfx/models/BgfxModelParts.cpp",
        "bool BgfxModel::ClearModelPart(uint32_t partIndex)",
        """void BgfxModel::ErasePreviousPart(uint32_t partIndex)
{
    if (m_m3Parts.size() < 2)
        return;
    const size_t keep = m_m3Parts.size() - 1;
    for (size_t i = keep; i-- > 0; )
        if (m_m3Parts[i].partIndex == partIndex)
            m_m3Parts.erase(m_m3Parts.begin() + static_cast<ptrdiff_t>(i));
}
""",
        """void BgfxModel::ErasePreviousPart(uint32_t partIndex)
{
    if (m_m3Parts.size() < 2)
        return;
    const size_t keep = m_m3Parts.size() - 1;
    for (size_t i = keep; i-- > 0; )
        if (m_m3Parts[i].partIndex == partIndex)
            m_m3Parts.erase(m_m3Parts.begin() + static_cast<ptrdiff_t>(i));
}

// EMPTIES the slot — EVERY occupant, where ErasePreviousPart above deliberately keeps the
// newest. The two are not variants of one function: ErasePreviousPart serves a load and
// must leave the caller wearing something, this serves an UNEQUIP and must leave it
// wearing nothing. Nothing could express the second before, which is why a weapon went on
// rendering in the hand after it was taken off; the clear arm of
// CActorInstance::AttachWeapon reached only [M1]'s slot tables, and Render walks m_m3Parts.
//
// THE ATTACHMENT RECORD GOES WITH THE GEOMETRY, because that record is what holds a part
// to a bone: leaving it would keep the hand's transform alive for a slot with nothing in
// it, and re-equipping would push a second, identical record beside the first every time.
// AttachModelInstance is what wrote them, and `src` is the index it wrote them under.
bool BgfxModel::ClearModelPart(uint32_t partIndex)
{
    bool removed = false;

    for (size_t i = m_m3Parts.size(); i-- > 0; )
        if (m_m3Parts[i].partIndex == partIndex)
        {
            m_m3Parts.erase(m_m3Parts.begin() + static_cast<ptrdiff_t>(i));
            removed = true;
        }

    if (m_m1)
    {
        M1State& st = *m_m1;
        for (size_t i = st.attachments.size(); i-- > 0; )
            if (!st.attachments[i].external && st.attachments[i].src == partIndex)
                st.attachments.erase(st.attachments.begin() + static_cast<ptrdiff_t>(i));
        st.dirty = true;
    }

    if (!removed)
        return false;

    // The same gate AddPart clears, for the mirror-image reason: the composed matrices are
    // held per part and indexed alongside the list, so anything drawn between this and the
    // next Deform would read them against a list that has moved underneath.
    m_m3Updated = false;

    // AND THE LATCH FOLLOWS THE BODY. Part 0 is the base geometry, so leaving
    // m_baseGeometryLoaded true after removing it would tell PythonCharacterManager::Deform
    // the body is present when it is gone — the exact lie that cost two reverts, recorded
    // in ActorInstanceAttach.cpp's note. Not reachable from the weapon path, and correct
    // rather than dead: the invariant belongs to the member, not to today's callers.
    if (partIndex == 0)
        m_baseGeometryLoaded = false;

    return true;
}
""",
    ),

    # ── item 1: and the unequip path asks for it ──
    (
        "src/GameLib/src/ActorInstanceAttach.cpp",
        "THE BY-PATH TWIN OF THE TWO LINES ABOVE",
        """	CItemData * pItemData = nullptr;
	if (!CItemManager::Instance().GetItemDataPointer(dwItemIndex, &pItemData))
	{
		GetModel().RegisterModelThing(dwPartIndex, EngineLib::GraphicThingHandle{});
		GetModel().SetModelInstance(dwPartIndex, dwPartIndex, 0);

		GetModel().RegisterModelThing(CRaceData::PART_WEAPON_LEFT, EngineLib::GraphicThingHandle{});
		GetModel().SetModelInstance(CRaceData::PART_WEAPON_LEFT, CRaceData::PART_WEAPON_LEFT, 0);

		RefreshActorInstance();
		return;
	}
""",
        """	CItemData * pItemData = nullptr;
	if (!CItemManager::Instance().GetItemDataPointer(dwItemIndex, &pItemData))
	{
		// >>> THE UNEQUIP ARM: item 0 has no CItemData, so this is where taking a weapon
		// >>> OFF lands. <<<
		GetModel().RegisterModelThing(dwPartIndex, EngineLib::GraphicThingHandle{});
		GetModel().SetModelInstance(dwPartIndex, dwPartIndex, 0);

		GetModel().RegisterModelThing(CRaceData::PART_WEAPON_LEFT, EngineLib::GraphicThingHandle{});
		GetModel().SetModelInstance(CRaceData::PART_WEAPON_LEFT, CRaceData::PART_WEAPON_LEFT, 0);

		// THE BY-PATH TWIN OF THE TWO LINES ABOVE, and the exact counterpart of the
		// LoadModelPartFromPath pair further down this file. The four calls above empty
		// [M1]'s model-thing and model-instance tables, which IS the removal on Eter
		// because there the handle is the geometry. On a backend where that handle
		// resolves to nothing the geometry came in by PATH instead, and nothing here
		// asked it to leave: the unequipped weapon stayed in the render list, still
		// holding its bone attachment, and went on being carried in the hand.
		//
		// Default-false on IModel, so this is a no-op on Eter.
		GetModel().ClearModelPart(dwPartIndex);
		GetModel().ClearModelPart(CRaceData::PART_WEAPON_LEFT);

		RefreshActorInstance();
		return;
	}
""",
    ),
]


def main():
    changed = 0
    for rel, marker, old, new in EDITS:
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            sys.exit("not found: %s (set M2WASM to the client tree)" % path)

        s = io.open(path, encoding="utf-8", errors="surrogateescape").read()
        if marker in s:
            print("already patched: %s" % rel)
            continue
        if s.count(old) != 1:
            sys.exit("anchor not found exactly once in %s (found %d)"
                     % (rel, s.count(old)))

        io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(
            s.replace(old, new, 1))
        print("patched: %s" % rel)
        changed += 1

    if changed:
        print("\n%d file(s) changed." % changed)


if __name__ == "__main__":
    main()
