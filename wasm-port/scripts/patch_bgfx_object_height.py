#!/usr/bin/env python3
# Idempotent patch: give the bgfx backend the PER-OBJECT HEIGHT DATA it never read.
#
# WHY (not what): BgfxScene::GetTerrainHeight answered the heightfield alone, while
# the oracle behind the same IScene virtual (EterScene::GetTerrainHeight ->
# CMapManager::GetHeight -> CMapOutdoor::GetHeight, MapOutdoor.cpp:805-829) answers
# max(object height, terrain height). That single omission is why a character walks
# UNDER a bridge, and it is also why the object half of GetPickingPointWithRay
# (FGetPickingPoint, MapOutdoor.cpp:54-83) had nothing to intersect.
#
# Re-runnable: every edit checks for its own marker first and skips if present.

import io
import os

ROOT = "/opt/m2wasm/src/EngineLib/src/bgfx/core"
HDR = os.path.join(ROOT, "BgfxScene.h")
SRC = os.path.join(ROOT, "BgfxSceneTerrain.cpp")


def read(p):
    with io.open(p, "r", encoding="utf-8", newline="") as f:
        return f.read()


def write(p, s):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


class Patcher(object):
    def __init__(self, path):
        self.path = path
        self.text = read(path)
        self.orig = self.text
        self.applied = []
        self.skipped = []

    def replace(self, name, marker, old, new):
        """Replace the single occurrence of `old` unless `marker` is already there."""
        if marker in self.text:
            self.skipped.append(name)
            return
        n = self.text.count(old)
        if n != 1:
            raise SystemExit(
                "patch_bgfx_object_height: anchor for '%s' matched %d times "
                "(expected 1) in %s" % (name, n, self.path)
            )
        self.text = self.text.replace(old, new, 1)
        self.applied.append(name)

    def replace_span(self, name, marker, comment_start, func_start, new):
        """Replace a whole comment+function block, located positionally.

        Retyping a 30-line comment as a literal anchor is how a patch script
        breaks on an em dash; the block is located by two short unique strings
        and the function's own closing brace instead.
        """
        if marker in self.text:
            self.skipped.append(name)
            return
        if self.text.count(comment_start) != 1 or self.text.count(func_start) != 1:
            raise SystemExit(
                "patch_bgfx_object_height: could not locate the block for '%s'" % name
            )
        cs = self.text.index(comment_start)
        fs = self.text.index(func_start, cs)
        # The function's closing brace: the first brace at column 0 after the
        # signature. Every inner brace in this file is indented.
        fe = self.text.index("\n}\n", fs) + len("\n}\n")
        self.text = self.text[:cs] + new + self.text[fe:]
        self.applied.append(name)

    def commit(self):
        if self.text != self.orig:
            write(self.path, self.text)


# =============================================================================
# BgfxScene.h
# =============================================================================

h = Patcher(HDR)

h.replace(
    "header: includes + forward declarations",
    "class CAttributeInstance;",
    "#include <utility>\n#include <vector>\n\nnamespace EngineLib {\n",
    """#include <unordered_map>
#include <utility>
#include <vector>

// The .mdatr that ships beside every building .gr2: CAttributeData is the parsed
// resource (its COLLISION and HEIGHT meshes) and CAttributeInstance is ONE
// PLACEMENT of it, with the height triangles baked into world space. Both are
// global-namespace Eter types (eter/render/AttributeData.h, .../AttributeInstance.h)
// and both are in EngineLib's UNCONDITIONAL source list -- they are arithmetic over
// a parsed file, with no device and no D3D anywhere in them.
//
// Forward-declared rather than included because only shared_ptr members appear
// here, and shared_ptr -- unlike unique_ptr -- needs no complete type at the point
// its owner is destroyed. That is what keeps this header free of eter/ includes.
class CAttributeData;
class CAttributeInstance;

namespace EngineLib {
""",
)

h.replace(
    "header: AreaObject::height",
    "std::shared_ptr<CAttributeInstance> height;",
    "        IRenderable* renderable = nullptr;\n    };",
    """        IRenderable* renderable = nullptr;

        // -- THE WALKABLE SURFACE, i.e. WHY A CHARACTER CAN STAND ON A BRIDGE ----
        //
        // CArea::TObjectInstance::pAttributeInstance, filled by
        // CArea::__LoadAttribute (Area.cpp:719-770) as the LAST thing
        // __SetObjectInstance_SetBuilding does (:661). Null for an object with no
        // .mdatr, for one whose .mdatr carries collision but NO height mesh, and
        // for every tree -- the oracle never calls __LoadAttribute for a tree.
        //
        // >>> shared_ptr, AND NOT THE PARALLEL-VECTOR TREATMENT m_areaRenderables
        // >>> GETS. <<< The argument recorded at that member is that a unique_ptr
        // here would make this struct NON-COPYABLE, and SeedAreaObject, the
        // draw-list sort and the tests all copy an AreaObject. A shared_ptr copies,
        // so that argument does not apply -- and keeping a placement's height data
        // ON the placement means EraseAreaObjectsIf's single compaction carries it
        // along, instead of a THIRD vector that has to be kept index-parallel by
        // hand at every one of those sites.
        std::shared_ptr<CAttributeInstance> height;
    };""",
)

h.replace(
    "header: GetTerrainHeight / GetTerrainHeightOnly",
    "float GetTerrainHeightOnly(float wx, float wy);",
    "    float GetTerrainHeight(float wx, float wy) override;                // [G3]",
    """    // >>> THIS VIRTUAL IS CMapOutdoor::GetHeight, NOT ::GetTerrainHeight, AND THE
    // >>> NAME IS THE TRAP. <<<
    //
    // The IScene spelling says "terrain"; the contract behind it never did.
    // EterScene::GetTerrainHeight (EterScene.cpp:541-546) forwards to
    // CMapManager::GetHeight -> CMapOutdoor::GetHeight (MapOutdoor.cpp:805-829),
    // which is max(OBJECT height, terrain height). This is the query the character's
    // per-frame height update runs through -- CInstanceBase::__GetBackgroundHeight
    // (InstanceBase.cpp:440-443), called from CInstanceBase::Update while moving
    // (:1941) -- so answering the terrain alone here is precisely "the player walks
    // through the bridge instead of over it".
    float GetTerrainHeight(float wx, float wy) override;                // [G3]
    // CMapOutdoor::GetTerrainHeight (MapOutdoor.cpp:1054-1077) -- THE HEIGHTFIELD
    // ALONE, no object pass. Public because the ray march needs it: the oracle's
    // __PickTerrainHeight samples the terrain at every step of the walk, and folding
    // the object scan into that would be both wrong (the oracle marches TERRAIN and
    // tests objects separately) and O(objects) per step of a several-hundred-step
    // march.
    float GetTerrainHeightOnly(float wx, float wy);                     // [G3]""",
)

h.replace(
    "header: object-height helpers",
    "std::shared_ptr<CAttributeInstance> AcquireHeightInstance(",
    "    void LoadAreaObjects(int slot, const std::string& base);",
    """    void LoadAreaObjects(int slot, const std::string& base);
    // -- THE OBJECT-HEIGHT PIPELINE (MapOutdoor.cpp:30-83's three collaborators) --
    //
    // The .mdatr beside a building's .gr2, parsed ONCE PER DISTINCT MODEL and shared
    // by every placement of it. CResourceManager does that sharing in the oracle
    // (Area.cpp:723 is a GetResourcePointer); m_areaAttributes does it here, because
    // THIS BACKEND HAS NO CResourceManager -- BgfxEngine::InitResourceSystem is a
    // documented no-op (BgfxEngine.cpp:1506-1522).
    std::shared_ptr<CAttributeData> AcquireAttributeData(const std::string& gr2Path);
    // ONE placement's height triangles in WORLD space: CAttributeInstance's
    // SetObjectPointer + RefreshObject, i.e. CArea::__LoadAttribute followed by the
    // RefreshObject at Area.cpp:435. Null when the model carries no height mesh.
    std::shared_ptr<CAttributeInstance> AcquireHeightInstance(
        const std::string& gr2Path, float x, float y, float z,
        float yaw, float pitch, float roll);
    // FGetObjectHeight (MapOutdoor.cpp:30-52) over the placed objects -- the "is
    // there a floor above the terrain at (fx, fy)" half of GetHeight. fy is MAP
    // space (positive); CAttributeInstance::GetHeight flips it to world space
    // itself (AttributeInstance.cpp:84).
    bool GetAreaObjectHeight(float fx, float fy, float& outHeight);
    // FGetPickingPoint (MapOutdoor.cpp:54-83) over the placed objects -- the object
    // half of GetPickingPointWithRay, and the thing the once-per-process warn in
    // PickTerrainWithRay used to stand in for.
    bool PickAreaObjects(const float* rayOrig, const float* rayDir,
                         float& outX, float& outY, float& outZ);""",
)

h.replace(
    "header: AreaObjectHeightCount observation",
    "size_t   AreaObjectHeightCount()  const;",
    "    size_t   AreaRenderableCount()    const;",
    """    size_t   AreaRenderableCount()    const;
    // Objects that got a WALKABLE SURFACE, i.e. a .mdatr with a height mesh in it.
    // Strictly smaller than AreaObjectCount and INDEPENDENT of AreaRenderableCount:
    // height is CPU-side data and is loaded on a scene with no device, which is what
    // lets the smoke assert that a bridge is walkable without a GPU. Zero here on a
    // map that places objects is the exact shape of the bug this exists to catch —
    // "the character walks through the bridge".
    size_t   AreaObjectHeightCount()  const;""",
)

h.replace(
    "header: m_areaAttributes",
    "m_areaAttributes;",
    "    std::unique_ptr<BgfxAreaModelStore> m_areaStore;",
    """    std::unique_ptr<BgfxAreaModelStore> m_areaStore;
    // The parsed .mdatr per DISTINCT model path, lower-cased key -- the height data
    // that every placement of that model shares. The AreaObject::height instances
    // POINT INTO these, so this is emptied in ReleaseTerrainData AFTER
    // EraseAreaObjectsIf has dropped the objects, never before.
    std::unordered_map<std::string, std::shared_ptr<CAttributeData>> m_areaAttributes;""",
)

h.commit()


# =============================================================================
# BgfxSceneTerrain.cpp
# =============================================================================

c = Patcher(SRC)

c.replace(
    "source: attribute includes",
    "eter/render/AttributeInstance.h",
    '#include "shared/world/EnvironmentConfig.h"\n',
    """#include "shared/world/EnvironmentConfig.h"

// The placed objects' walkable surface. These live under eter/ by DIRECTORY only:
// CAttributeData is a binary parse of a .mdatr and CAttributeInstance is triangle
// arithmetic over it -- no device, no D3D, and both are in EngineLib's unconditional
// source list. The ONE Eter coupling they do carry is CResource's teardown path
// (Resource.cpp:31-36 reaches CResourceManager::Instance(), which does not exist on
// this backend); AcquireAttributeData below is where that is defused, deliberately
// and in exactly one place.
#include "eter/render/AttributeData.h"
#include "eter/render/AttributeInstance.h"
""",
)

c.replace(
    "source: MarchTerrain uses the terrain-only query",
    "scene.GetTerrainHeightOnly(cur[0], cur[1])",
    "        const float mapHeight = scene.GetTerrainHeight(cur[0], cur[1]);",
    """        // GetTerrainHeightOnly AND NOT GetTerrainHeight: __PickTerrainHeight marches
        // the HEIGHTFIELD (MapOutdoor.cpp:507-541 reaches CTerrain::GetHeight through
        // GetTerrainPointer), and the objects are tested separately by
        // FGetPickingPoint. Routing this through the object-aware query would both
        // diverge from the oracle and put an O(objects) scan inside a loop that runs
        // hundreds of times per pick.
        const float mapHeight = scene.GetTerrainHeightOnly(cur[0], cur[1]);""",
)

c.replace(
    "source: rename the terrain-only body",
    "float BgfxScene::GetTerrainHeightOnly(float wx, float wy)",
    "float BgfxScene::GetTerrainHeight(float wx, float wy)",
    "float BgfxScene::GetTerrainHeightOnly(float wx, float wy)",
)

c.replace(
    "source: GetHeight = max(object, terrain) + the two object passes",
    "bool BgfxScene::GetAreaObjectHeight(float fx, float fy, float& outHeight)",
    """    return h1 + (static_cast<float>(xdist) * xslope +
                 static_cast<float>(ydist) * yslope);
}
""",
    """    return h1 + (static_cast<float>(xdist) * xslope +
                 static_cast<float>(ydist) * yslope);
}

// -- FGetObjectHeight (MapOutdoor.cpp:30-52), and the walk that feeds it ---------
//
// >>> THE ORACLE NARROWS THE CANDIDATES WITH A SPHERE-PACK POINT TEST AND THIS
// >>> WALKS THE LIST. THAT IS AN ACCELERATOR, NOT PART OF THE ANSWER. <<<
// CMapOutdoor::GetHeight calls PointTest2d(fx, -fy, terrainZ) before invoking the
// functor (MapOutdoor.cpp:816-820), but the functor's own callee re-tests every
// candidate against its height radius -- CAttributeInstance::IsInHeight
// (AttributeInstance.cpp:135-143) -- and answers false for anything out of range. So
// the only thing the index can change is HOW MANY objects are asked, and the only
// way a wrong filter changes the ANSWER is by rejecting an object that would have
// answered. Since that is exactly the bug being fixed here, the filter is left out
// on this side rather than approximated with a bounding sphere whose radius is
// derived differently: AreaObject::boundRadius comes from the .gr2's AABB,
// m_fHeightRadius from the .mdatr's own vertices measured from the MODEL ORIGIN, and
// for a long asset like a bridge the second can be the larger of the two.
//
// The scan is cheap because `height` is null for the overwhelming majority: every
// tree, every model with no .mdatr, every .mdatr with collision but no height mesh.
// There is also nothing to reuse -- this backend's area objects are not registered in
// any spatial index (ISpatialIndex deals in CGraphicObjectInstance*, which they are
// not).
bool BgfxScene::GetAreaObjectHeight(float fx, float fy, float& outHeight)
{
    // >>> SEEDED TO ZERO, AND THAT IS THE ORACLE'S VALUE RATHER THAN A HABIT. <<<
    // FGetObjectHeight::m_fReturnHeight starts at 0.0f (MapOutdoor.cpp:40) and
    // CAttributeInstance::GetHeight only ever MAXes into it (AttributeInstance.cpp:121),
    // so a floor below z=0 is unreportable there and is unreportable here.
    outHeight = 0.0f;

    bool found = false;
    for (const AreaObject& o : m_areaObjects)
    {
        if (!o.height)
            continue;

        // THE ACCUMULATOR IS SHARED ACROSS OBJECTS, exactly as the functor's single
        // m_fReturnHeight is: two stacked objects answer the HIGHER floor rather than
        // whichever one the loop happened to reach last.
        if (o.height->GetHeight(fx, fy, &outHeight))
            found = true;
    }
    return found;
}

// CMapOutdoor::GetHeight (MapOutdoor.cpp:805-829) -- the IScene virtual, in full.
float BgfxScene::GetTerrainHeight(float wx, float wy)
{
    const float terrainHeight = GetTerrainHeightOnly(wx, wy);

    // SetTerrainOnlyForHeight: dungeon maps opt out of the object pass entirely
    // (MapOutdoor.cpp:809). Their floors ARE the heightfield by construction.
    if (m_terrainOnlyForHeight)
        return terrainHeight;

    // -CHECK_HEIGHT, not 0 and not the terrain height: the oracle seeds fObjectHeight
    // at -25000 so the max below always falls back to the terrain when no object
    // answered, INCLUDING on a map whose terrain sits below zero
    // (MapOutdoor.cpp:813-814).
    constexpr float kCheckHeight = 25000.0f;
    float objectHeight = -kCheckHeight;

    float found = 0.0f;
    if (GetAreaObjectHeight(wx, wy, found))
        objectHeight = found;

    return std::max(objectHeight, terrainHeight);
}

// -- FGetPickingPoint (MapOutdoor.cpp:54-83), applied to this backend's objects ---
//
// >>> THE THREE STEPS ARE IsObjectHeight, Picking, GetObjectHeight, IN THAT ORDER,
// >>> AND THE ORDER IS THE ALGORITHM. <<<
//   * IsObjectHeight         -- "does this object have a height mesh at all"
//                               (GrpObjectInstance.cpp:470-476). Here: a non-null
//                               `height`, which AcquireHeightInstance only produces
//                               when the instance ended up with triangles.
//   * Picking(v, dir, x, y)  -- ray/triangle over those SAME triangles, answering the
//                               XY of the nearest hit (AttributeInstance.cpp:10-77).
//   * GetObjectHeight(x, -y) -- the Z of the surface at that XY. THE SIGN FLIP ON Y IS
//                               THE ORACLE'S (MapOutdoor.cpp:73) and it is NOT
//                               redundant with the flip inside GetHeight: Picking
//                               answers in WORLD space (y negative) and GetHeight
//                               expects MAP space, so the pair is exactly the round
//                               trip.
//
// >>> THE CANDIDATE FILTER IS KEPT HERE, UNLIKE IN GetAreaObjectHeight, BECAUSE HERE
// >>> IT CHANGES THE ANSWER. <<< The oracle's `ForInRange2d(v3dStart, ...)` is a 2D
// point test AT THE RAY START -- i.e. AT THE CAMERA -- so only objects whose footprint
// contains the camera are ever ray-tested (MapOutdoor.cpp:560-563; ForInRange2d is
// PointTest2d, ISpatialIndex.h:157-161). That reads like an oversight and it is load
// bearing: without it, clicking the ground BEHIND a house answers the house's roof,
// because the ray genuinely crosses the roof first and the nearer hit wins below.
// Dropping it would be a different function, not a better one.
bool BgfxScene::PickAreaObjects(const float* rayOrig, const float* rayDir,
                                float& outX, float& outY, float& outZ)
{
    const D3DXVECTOR3 start(rayOrig[0], rayOrig[1], rayOrig[2]);
    const D3DXVECTOR3 dir(rayDir[0], rayDir[1], rayDir[2]);

    bool picked = false;
    for (const AreaObject& o : m_areaObjects)
    {
        if (!o.height)
            continue;

        // The PointTest2d above, in this backend's own bound. A zero radius is an
        // object whose model produced no sphere (LoadAreaObjects records those with
        // no renderable); it can never contain a point, which is the same answer the
        // sphere pack gives for a sphere that was never registered.
        const float dx = o.boundCenter[0] - start.x;
        const float dy = o.boundCenter[1] - start.y;
        if (dx * dx + dy * dy > o.boundRadius * o.boundRadius)
            continue;

        float fx = 0.0f, fy = 0.0f, fz = 0.0f;
        if (!o.height->Picking(start, dir, fx, fy))
            continue;
        if (!o.height->GetHeight(fx, -fy, &fz))
            continue;

        // LAST WRITER WINS, and so does the functor's: FGetPickingPoint overwrites
        // m_v3PickingPoint on every hit and does no nearest-of-the-objects test. The
        // nearest test that DOES exist is object-vs-terrain, in the caller.
        outX = fx;
        outY = fy;
        outZ = fz;
        picked = true;
    }
    return picked;
}
""",
)

c.replace_span(
    "source: PickTerrainWithRay gains the object pass",
    ">>> THE OBJECT HALF IS PRESENT. <<<",
    "// CMapOutdoor::GetPickingPointWithRay (MapOutdoor.cpp:543-604): the object pick",
    "bool BgfxScene::PickTerrainWithRay(",
    """// CMapOutdoor::GetPickingPointWithRay (MapOutdoor.cpp:543-604): the object pick
// and the terrain pick, nearest wins.
//
// >>> THE OBJECT HALF IS PRESENT. <<< It was not, and the block that stood here
// explained at length why -- ending on "the gap is the height data, and it is one
// asset load plus this function's five lines once that exists". THE ASSET LOAD NOW
// EXISTS: LoadAreaObjects reads the .mdatr beside each building's .gr2 into
// AreaObject::height. So the five lines are written, and the once-per-process warn
// that stood in for them is gone.
//
// For anyone diffing against an older log, that warn read: "picks over a building
// answer the terrain behind it, where the eter backend answers the building". It is
// answered for exactly the cases the oracle answers it for -- see the candidate
// filter documented at PickAreaObjects, which is a point test AT THE CAMERA and not
// along the ray, and which this reproduces rather than improves on.
bool BgfxScene::PickTerrainWithRay(
    const float* rayOrig, const float* rayDir, float rayRange,
    float& outX, float& outY, float& outZ)
{
    if (!rayOrig || !rayDir)
        throw std::logic_error("BgfxScene::PickTerrainWithRay: null ray");

    // THE OBJECT PASS RUNS FIRST, as the oracle's does, and it is gated by
    // m_bEnableTerrainOnlyForHeight exactly as MapOutdoor.cpp:558 gates it.
    bool  objectPick = false;
    float ox = 0.0f, oy = 0.0f, oz = 0.0f;
    if (IsMapReady() && !m_terrainOnlyForHeight)
        objectPick = PickAreaObjects(rayOrig, rayDir, ox, oy, oz);

    float tx = 0.0f, ty = 0.0f, tz = 0.0f;
    const bool terrainPick =
        PickTerrainWithRayOnlyTerrain(rayOrig, rayDir, rayRange, tx, ty, tz);

    if (objectPick && terrainPick)
    {
        // NEAREST TO THE RAY START WINS AND THE TIE GOES TO THE TERRAIN: the oracle's
        // test is `>=` on the OBJECT distance (MapOutdoor.cpp:583-586).
        const float odx = ox - rayOrig[0], ody = oy - rayOrig[1], odz = oz - rayOrig[2];
        const float tdx = tx - rayOrig[0], tdy = ty - rayOrig[1], tdz = tz - rayOrig[2];
        const float od = std::sqrt(odx * odx + ody * ody + odz * odz);
        const float td = std::sqrt(tdx * tdx + tdy * tdy + tdz * tdz);

        if (od >= td)
        {
            outX = tx; outY = ty; outZ = tz;
        }
        else
        {
            outX = ox; outY = oy; outZ = oz;
        }
        return true;
    }

    if (objectPick)
    {
        outX = ox; outY = oy; outZ = oz;
        return true;
    }

    if (terrainPick)
    {
        outX = tx; outY = ty; outZ = tz;
        return true;
    }

    return false;
}
""",
)

# An earlier revision of this script attached the height instance AFTER the
# `model == nullptr` guard, which silently withheld it from every device-less
# scene — including the one the smoke builds. Strip that placement if it is
# present so the insertion below is the only one, on a tree patched either way.
OLD_PLACEMENT = """        // -- THE WALKABLE SURFACE ------------------------------------------
        //
        // CArea::__SetObjectInstance_SetBuilding's LAST line (Area.cpp:661):
        // __LoadAttribute(pObjectInstance, Data.strAttributeDataFileName). That file
        // name is NOT a row of the property file -- prt::PropertyBuildingStringToData
        // COMPOSES it as NoExtension(buildingfile) + ".mdatr" (MapType.cpp:142),
        // which is what AcquireAttributeData reproduces.
        //
        // TREES ARE EXCLUDED BECAUSE THE ORACLE EXCLUDES THEM:
        // __SetObjectInstance_SetTree never calls __LoadAttribute, a .spt has no
        // .mdatr, and a tree has no walkable surface to stand on.
        //
        // Placed HERE -- after the model resolved -- for the same reason the oracle
        // places it after its CGraphicThing check: a building whose .gr2 refused to
        // load is recorded with no renderable, and nothing may stand on geometry
        // that is not there.
        if (!isTree)
            o.height = AcquireHeightInstance(gr2, o.x, o.y, o.z,
                                             o.yaw, o.pitch, o.roll);

"""
if OLD_PLACEMENT in c.text:
    c.text = c.text.replace(OLD_PLACEMENT, "", 1)
    c.applied.append("source: removed the earlier (post-model-guard) height attach")

c.replace(
    "source: load the .mdatr per placement",
    "o.height = AcquireHeightInstance(",
    "        if (model == nullptr)\n",
    """        // -- THE WALKABLE SURFACE ------------------------------------------
        //
        // CArea::__SetObjectInstance_SetBuilding's LAST line (Area.cpp:661):
        // __LoadAttribute(pObjectInstance, Data.strAttributeDataFileName). That file
        // name is NOT a row of the property file -- prt::PropertyBuildingStringToData
        // COMPOSES it as NoExtension(buildingfile) + ".mdatr" (MapType.cpp:142),
        // which is what AcquireAttributeData reproduces.
        //
        // TREES ARE EXCLUDED BECAUSE THE ORACLE EXCLUDES THEM:
        // __SetObjectInstance_SetTree never calls __LoadAttribute, a .spt has no
        // .mdatr, and a tree has no walkable surface to stand on.
        //
        // >>> BEFORE THE `model == nullptr` GUARD, AND THAT IS ONE DELIBERATE
        // >>> DIVERGENCE FROM Area.cpp:597-608. <<< The oracle returns early when the
        // .gr2 is empty and so never loads the attribute; here the model can also be
        // refused for a reason that has nothing to do with the DATA — a scene with no
        // Bgfx3D declines every .gr2 on purpose (BgfxAreaModelStore::Acquire). Height
        // is CPU-side geometry with no device in it, so tying it to the GPU load would
        // make a headless scene answer "no floor" for a bridge whose .mdatr is right
        // there, which is both wrong and untestable.
        if (!isTree)
            o.height = AcquireHeightInstance(gr2, o.x, o.y, o.z,
                                             o.yaw, o.pitch, o.roll);

        if (model == nullptr)
""",
)

c.replace(
    "source: AcquireAttributeData / AcquireHeightInstance",
    "std::shared_ptr<CAttributeData> BgfxScene::AcquireAttributeData",
    "void BgfxScene::LoadAreaObjects(int slot, const std::string& base)\n{",
    """// -- THE .mdatr, ONE PER DISTINCT MODEL ------------------------------------------
//
// CArea::__LoadAttribute's first two lines are a CResourceManager
// GetResourcePointer (Area.cpp:721-723), which both PARSES the file and SHARES the
// result across every placement of the model. Neither half exists on this backend,
// so both are here: PackGet + CAttributeData::OnLoad for the parse, m_areaAttributes
// for the sharing.
//
// >>> ONE PERMANENT REFERENCE, AND IT IS NOT BOOKKEEPING -- IT IS A NULL DEREFERENCE
// >>> WAITING. <<< CAttributeInstance holds its data through CRef, and CRef's last
// ReleaseRef runs CResource::OnSelfDestruct, which is
// `CResourceManager::Instance().ReserveDeletingResource(this)` (Resource.cpp:31-36).
// THAT SINGLETON IS NEVER CONSTRUCTED HERE -- BgfxEngine::InitResourceSystem is a
// documented no-op where EterEngine's builds CPythonResource -- so letting the count
// fall to zero dereferences a null CSingleton pointer at map unload.
// AddReferenceOnly pins it at one for as long as this map owns the entry (it bumps
// the count WITHOUT running OnConstruct, ReferenceObject.cpp:25-28), and the
// shared_ptr held in m_areaAttributes is what actually frees the object. CResource::
// Load() is likewise never called, so its own pack read never runs and OnLoad is
// reached directly with bytes this file fetched the way it fetches every other asset.
std::shared_ptr<CAttributeData> BgfxScene::AcquireAttributeData(const std::string& gr2Path)
{
    if (gr2Path.empty())
        return nullptr;

    // NoExtension(gr2) + ".mdatr". CFileNameHelper::NoExtension strips from the LAST
    // dot; the separator is looked up as well because a dot can live in a DIRECTORY
    // name and does -- bin/pack/property/n/obj/snow.m/ is a shipped example.
    std::string attrPath = gr2Path;
    for (char& ch : attrPath)
        if (ch == '\\\\')
            ch = '/';
    const size_t dot   = attrPath.find_last_of('.');
    const size_t slash = attrPath.find_last_of('/');
    if (dot != std::string::npos && (slash == std::string::npos || dot > slash))
        attrPath.erase(dot);
    attrPath += ".mdatr";

    const std::string key = LowerCopy(attrPath);

    const auto hit = m_areaAttributes.find(key);
    if (hit != m_areaAttributes.end())
        return hit->second;

    std::shared_ptr<CAttributeData> data(new CAttributeData(attrPath.c_str()));
    data->AddReferenceOnly();   // see the block above -- this must happen FIRST

    CEterPackManager::TPackDataPtr bytes;
    if (PackGet(attrPath, bytes) && bytes && !bytes->empty())
    {
        // THROUGH THE BASE, AND THE CAST IS NOT DECORATION. CResource::OnLoad is
        // PUBLIC and CAttributeData re-declares its override as PROTECTED
        // (AttributeData.h:62) -- access is checked against the STATIC type of the
        // object expression, so `data->OnLoad(...)` does not compile while this does,
        // and the virtual call still lands on CAttributeData's body.
        CResource* res = data.get();
        res->OnLoad(static_cast<int>(bytes->size()), bytes->data());
    }
    // No else arm. The oracle's absent-file path synthesises a DUMMY OBB
    // (Area.cpp:730-760) and that is COLLISION data, never height -- so a model with
    // no .mdatr is simply not walkable. The empty resource is still cached, so the
    // next placement of the same model does not re-miss the pack.

    m_areaAttributes.emplace(key, data);
    return data;
}

// CArea::__LoadAttribute's tail plus the RefreshObject at Area.cpp:435 -- the point
// at which the .mdatr's model-space triangles become world-space ones.
std::shared_ptr<CAttributeInstance> BgfxScene::AcquireHeightInstance(
    const std::string& gr2Path, float x, float y, float z,
    float yaw, float pitch, float roll)
{
    const std::shared_ptr<CAttributeData> data = AcquireAttributeData(gr2Path);
    if (!data || data->IsEmpty())
        return nullptr;

    // CGraphicObjectInstance::SetRotation(yaw,pitch,roll) followed by ::Transform
    // (GrpObjectInstance.cpp:207-215 and :144-151), which is what the oracle hands
    // RefreshObject as pThingInstance->GetTransform().
    //
    // >>> THE TRANSLATION IS ADDED INTO THE FOURTH ROW, NOT MULTIPLIED IN. <<< That
    // is Transform()'s literal `_41 += x`, and it agrees with a rotation*translation
    // only because there is no scale in this client (m_v3Scale is written by SetScale
    // and read by nothing). Writing the multiply instead would be a different
    // function the day one appears.
    //
    // Rebuilt from the Euler angles rather than from the renderable's quaternion
    // because RefreshObject takes a D3DXMATRIX and because this IS the oracle's
    // spelling -- the quaternion path (AreaObjectRotation) exists for the DRAW and is
    // pinned against this same D3DX call, so the two agree by construction.
    D3DXMATRIX world;
    D3DXMatrixRotationYawPitchRoll(&world,
                                   D3DXToRadian(yaw),
                                   D3DXToRadian(pitch),
                                   D3DXToRadian(roll));
    world._41 += x;
    world._42 += y;
    world._43 += z;

    auto inst = std::make_shared<CAttributeInstance>();
    inst->SetObjectPointer(data.get());   // Clear()s first -- SetObjectPointer's own line
    inst->RefreshObject(world);

    // IsEmpty() here is the INSTANCE's test ("no transformed height triangles") and
    // NOT the data's: a .mdatr carrying collision but no height mesh loads perfectly
    // well and can answer no height at all. The oracle frees the attribute instance
    // in exactly that case (Area.cpp:766-771); a null here is that free.
    if (inst->IsEmpty())
        return nullptr;

    return inst;
}

void BgfxScene::LoadAreaObjects(int slot, const std::string& base)
{""",
)

c.replace(
    "source: AreaObjectHeightCount body",
    "size_t BgfxScene::AreaObjectHeightCount() const",
    "size_t BgfxScene::AreaRenderableCount() const",
    """size_t BgfxScene::AreaObjectHeightCount() const
{
    size_t n = 0;
    for (const AreaObject& o : m_areaObjects)
        if (o.height)
            ++n;
    return n;
}

size_t BgfxScene::AreaRenderableCount() const""",
)

c.replace(
    "source: report the walkable count in the [area] summary",
    "{} walkable",
    """    if (placed > 0 || skipped > 0 || placedEffects > 0)
        SPDLOG_INFO("[area] {}: {} objects placed, {} ambient effects, {} skipped, "
                    "{} distinct models",
                    base, placed, placedEffects, skipped, m_areaStore->Count());""",
    """    // The walkable count is in the line because it is the ONE number that separates
    // "the bridge is drawn" from "the bridge can be stood on", and the second used to
    // be silently zero on every map.
    if (placed > 0 || skipped > 0 || placedEffects > 0)
        SPDLOG_INFO("[area] {}: {} objects placed, {} ambient effects, {} skipped, "
                    "{} distinct models, {} walkable ({} distinct .mdatr)",
                    base, placed, placedEffects, skipped, m_areaStore->Count(),
                    AreaObjectHeightCount(), m_areaAttributes.size());""",
)

c.replace(
    "source: GetPickingPoint's stale note about the once-warn",
    "which is the object pass this now actually runs",
    """    // PickTerrainWithRay and NOT PickTerrainWithRayOnlyTerrain: CMapOutdoor::GetPickingPoint
    // routes through GetPickingPointWithRay (MapOutdoor.cpp:543-604), which runs the OBJECT
    // pass before the terrain march. The once-warn about this backend's missing object half
    // lives in PickTerrainWithRay, so going through it is what makes that warning fire on
    // the path it describes instead of never firing at all.""",
    """    // PickTerrainWithRay and NOT PickTerrainWithRayOnlyTerrain: CMapOutdoor::GetPickingPoint
    // routes through GetPickingPointWithRay (MapOutdoor.cpp:543-604), which runs the OBJECT
    // pass before the terrain march -- which is the object pass this now actually runs. The
    // sentence that stood here said the reason was to make a once-per-process WARN fire on
    // the path it describes; that warn is gone because the half it described is written, and
    // the routing is now load-bearing for the ANSWER rather than for a diagnostic.""",
)

c.replace(
    "source: drop the parsed .mdatr with the map",
    "m_areaAttributes.clear();",
    "    EraseAreaObjectsIf([](const AreaObject&) { return true; });\n",
    """    EraseAreaObjectsIf([](const AreaObject&) { return true; });

    // AFTER the line above and never before: every AreaObject::height holds a CRef
    // into one of these, and dropping the data first would leave those instances
    // pointing at freed memory for the length of the erase.
    //
    // Cleared rather than kept -- unlike the PropertyResolver index, which survives a
    // warp on purpose -- because this is per-MAP asset bytes rather than a CRC table.
    // The next map's buildings are a different set, and CResourceManager's own policy
    // for these is a 30 s sweep, not permanent residency.
    m_areaAttributes.clear();
""",
)

c.commit()


# =============================================================================
# tools/bgfxbackend/scene_checks_g3.inc
#
# The smoke's G3 block asserted the OLD behaviour in two ways and one of its
# checks asked, in writing, to be changed rather than deleted on the day the
# object pick landed. Both are honoured here.
# =============================================================================

CHECKS = "/opt/m2wasm/tools/bgfxbackend/scene_checks_g3.inc"
ALL = [h, c]

if os.path.exists(CHECKS):
    g = Patcher(CHECKS)

    if "GetTerrainHeightOnly" in g.text:
        g.skipped.append("checks: heightfield assertions use GetTerrainHeightOnly")
    else:
        # Every one of these compares the query against an INDEPENDENT READ OF
        # height.raw, or asserts the y-fold, or asserts 0 off the world. All three
        # are properties of the HEIGHTFIELD, and none of them survives contact with
        # an object pass that (correctly) does not fold y and does answer over a
        # non-resident tile. They move to GetTerrainHeightOnly, which is the query
        # they were always describing.
        #
        # Through `g3` and not `q`: GetTerrainHeightOnly is not an IScene virtual
        # (the interface has exactly one height query and its contract is
        # CMapOutdoor::GetHeight), and `g3` is the concrete BgfxScene in both scopes
        # that contain these calls.
        g.text = g.text.replace("GetTerrainHeight", "GetTerrainHeightOnly")
        g.text = g.text.replace("q.GetTerrainHeightOnly(", "g3.GetTerrainHeightOnly(")
        g.applied.append("checks: heightfield assertions use GetTerrainHeightOnly")

    OLD_CHECK_START = "// >>> THIS PINS A KNOWN DIVERGENCE, NOT AN EQUIVALENCE. <<< It"
    OLD_CHECK_END = "g3.AreaObjectCount() > 0);"
    if "G3 the .mdatr beside each building's .gr2 is READ" in g.text:
        g.skipped.append("checks: the object pick is pinned as WRITTEN")
    elif g.text.count(OLD_CHECK_START) == 1 and g.text.count(OLD_CHECK_END) == 1:
        s = g.text.index(OLD_CHECK_START)
        e = g.text.index(OLD_CHECK_END, s) + len(OLD_CHECK_END)
        g.text = g.text[:s] + """// >>> THE OBJECT HALF IS WRITTEN NOW, AND THIS IS WHAT PINS IT. <<<
                    // The check that stood here asserted the OPPOSITE and asked, in
                    // writing, to be CHANGED rather than deleted on the day the object
                    // pick landed. So it is changed, and its reasoning is kept.
                    //
                    // PickTerrainWithRay is the object pass followed by the terrain
                    // march, nearest to the ray start winning and the tie going to the
                    // terrain (MapOutdoor.cpp:583-586). Over a column with no object
                    // footprint under the ray START it is the terrain answer verbatim:
                    // the oracle's candidate filter is a 2D point test AT THE CAMERA
                    // (ForInRange2d, MapOutdoor.cpp:560-563), which this reproduces
                    // rather than improves on. Where a footprint does cover it, the
                    // object surface is the nearer hit and sits at or above the ground.
                    Check("G3 PickTerrainWithRay = object pass THEN terrain march, "
                          "nearest to the ray start wins. The object half is WRITTEN "
                          "(AreaObject::height); it answers the terrain point unless an "
                          "object footprint covers the ray origin",
                          q.PickTerrainWithRay(org, dir, 20000.0f, mx, my, mz) &&
                              g3.AreaObjectCount() > 0 &&
                              ((mx == ox && my == oy && mz == oz) || mz >= oz));

                    // >>> THE ASSET LOAD ITSELF, MEASURED. <<< Zero walkable objects on
                    // a map that places 133 IS the bug this whole change exists to
                    // remove -- "the character walks through the bridge" -- and it is
                    // invisible to every other count in this file. Five of 001001's 133
                    // objects carry a height mesh (the rest have collision only, which
                    // is what a wall is), so this is a real measurement rather than a
                    // tautology.
                    Check("G3 the .mdatr beside each building's .gr2 is READ: this map's "
                          "objects carry a WALKABLE SURFACE",
                          g3.AreaObjectHeightCount() > 0 &&
                              g3.AreaObjectHeightCount() <= g3.AreaObjectCount(),
                          std::to_string(g3.AreaObjectHeightCount()) + " walkable of " +
                          std::to_string(g3.AreaObjectCount()) + " objects");

                    // ...AND WITHOUT A DEVICE. This fixture has no Bgfx3D, so not one
                    // object got a renderable (asserted above). Height is CPU-side
                    // geometry and must not have been tied to the GPU load -- tying it
                    // there is exactly the mistake an earlier revision of the loader
                    // made, and it is invisible on a machine that has a GPU.
                    Check("G3 ...and it loads with NO Bgfx3D: renderables 0, walkable > 0",
                          g3.AreaRenderableCount() == 0 && g3.AreaObjectHeightCount() > 0,
                          std::to_string(g3.AreaRenderableCount()) + " renderables / " +
                          std::to_string(g3.AreaObjectHeightCount()) + " walkable");

                    // CMapOutdoor::GetHeight is max(object, terrain), so the IScene
                    // query can never answer BELOW the heightfield at the same point.
                    Check("G3 GetTerrainHeightOnly is the floor of the IScene height "
                          "query, never the other way round",
                          q.GetTerrainHeight(6073.0f, -24352.0f) >=
                              g3.GetTerrainHeightOnly(6073.0f, -24352.0f));""" + g.text[e:]
        g.applied.append("checks: the object pick is pinned as WRITTEN")
    else:
        raise SystemExit("patch_bgfx_object_height: could not locate the old G3 pick check")

    g.commit()

    ALL.append(g)

for p in ALL:
    for name in p.applied:
        print("applied:  %s" % name)
    for name in p.skipped:
        print("skipped (already present):  %s" % name)

print("done")
