#!/usr/bin/env python3
"""Idempotent patch: make the bgfx backend honour the material IMAGE override.

CGrannyMaterialPalette::SetMaterialImagePointer / SetMaterialData replace a
material's texture. The bgfx port STORED that override in
M1State::MaterialRecord::image and never read it back: SubmitMeshNodeList binds
tg.texture, the texture ResolveTextures took from the .gr2 itself. So every
ShapeData entry whose TargetSkin differs from its SourceSkin rendered with the
SourceSkin — the base texture of that .gr2 — which is the LOWEST armour tier
sharing the mesh. Hence "armour always one tier too low".

This wires the stored override into the draw:
  1. MaterialRecord gains a resolved bgfx texture handle + a keep-alive ref.
  2. The two setters resolve the ImageHandle through the model's texture cache.
  3. TriGroupOverrideTexture() answers the per-tri-group lookup, keyed exactly
     like TriGroupSpecularPower (normalised path equality == the original's
     CGrannyMaterial::IsIn).
  4. SubmitMeshNodeList prefers the override over the .gr2's own texture.

Run repeatedly; each edit is guarded by its own marker.
"""
import io
import sys

ROOT = "/opt/m2wasm/src/EngineLib/src/bgfx/models"
PARTS = ROOT + "/BgfxModelParts.cpp"
HEADER = ROOT + "/BgfxModel.h"
RENDER = ROOT + "/BgfxModelRender.cpp"

changed = []


def read(p):
    with io.open(p, "r", encoding="utf-8", newline="") as f:
        return f.read()


def write(p, s):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


def sub_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        raise SystemExit("ANCHOR %r matched %d times (expected 1) — %s" % (what, n, what))
    return text.replace(old, new)


# ─────────────────────────────────────────────────────────────────────────────
# 1. BgfxModelParts.cpp
# ─────────────────────────────────────────────────────────────────────────────
src = read(PARTS)

if "TriGroupOverrideTexture" not in src:
    # 1a. the record carries the RESOLVED handle, not only the ImageHandle.
    old = """        uint32_t      part = 0;
        std::string   imageName;
        ImageHandle   image{};
        bool          hasSpecular      = false;"""
    new = """        uint32_t      part = 0;
        std::string   imageName;
        ImageHandle   image{};
        // THE OVERRIDE, RESOLVED. `image` alone was write-only: nothing between
        // SetMaterialImagePointer and Bgfx3D::Draw ever turned it into something a
        // draw could bind, so the skin swap the .msm asks for never happened and
        // every armour rendered with its .gr2's own texture — the base tier of the
        // mesh family. Resolved at RECORD time rather than per frame because the
        // cache lookup is a string hash and equipment changes are rare; the ref is
        // the keep-alive, exactly as ResolveTextures holds one per tri-group.
        uint16_t        texture = UINT16_MAX;
        BgfxResourceRef textureRef;
        bool          hasSpecular      = false;"""
    src = sub_once(src, old, new, "MaterialRecord fields")

    # 1b. the resolver, immediately before the first setter that needs it.
    old = """void BgfxModel::SetMaterialImagePointer(uint32_t partIndex, std::string_view imageName,
                                        ImageHandle pImage)
{"""
    new = """// ImageHandle -> a bgfx texture this backend can bind.
//
// The original stores a CGraphicImage* in the material record and the device binds
// straight off it (CGrannyMaterial::SetImagePointer). There is no CGraphicImage on
// this backend, so the equivalent is: read the NAME back out of the resource — which
// is all IImageResource carries — and acquire it through the same cache
// ResolveTextures uses, so the payload is de-duplicated against the .gr2's own
// textures instead of loaded twice.
//
// Failure is SILENT and leaves outHandle at UINT16_MAX, which the draw path reads as
// "no override" and falls back to the .gr2's texture. That is the pre-existing
// picture rather than a blank one, i.e. the same degrade this whole path had before.
static void ResolveOverrideTexture(BgfxResourceCache* cache, ImageHandle pImage,
                                   uint16_t& outHandle, BgfxResourceRef& outRef)
{
    outHandle = UINT16_MAX;
    outRef = BgfxResourceRef();

    if (cache == nullptr)
        return;

    const IImageResource* pRes = ImageRegistry().resolve(pImage);
    if (pRes == nullptr)
        return;

    const char* szName = pRes->GetFileName();
    if (szName == nullptr || *szName == '\\0')
        return;

    BgfxResourceRef ref = cache->Acquire(szName);
    if (!ref || !ref->IsImage())
        return;

    const BgfxTexture* tex = ref->AsTexture();
    if (tex == nullptr)
        return;

    outHandle = tex->Handle();
    outRef    = std::move(ref);
}

// The texture a tri-group must bind INSTEAD of its own, or UINT16_MAX for none.
//
// Keyed exactly like TriGroupSpecularPower: normalised-path equality against the
// material name, which is what CGrannyMaterial::IsIn does (StringPath on both sides,
// then ==). NAMED records only — SetMaterialData's empty-name arm is the specular
// broadcast and explicitly "touches no image", so a part-wide record must not be
// allowed to repaint every material of the part.
uint16_t BgfxModel::TriGroupOverrideTexture(uint32_t partIndex, const std::string& mtrlName) const
{
    const M1State* st = M1Peek();
    if (st == nullptr || mtrlName.empty())
        return UINT16_MAX;

    for (const M1State::MaterialRecord& r : st->materials)
        if (r.part == partIndex && r.imageName == mtrlName && r.texture != UINT16_MAX)
            return r.texture;

    return UINT16_MAX;
}

void BgfxModel::SetMaterialImagePointer(uint32_t partIndex, std::string_view imageName,
                                        ImageHandle pImage)
{"""
    src = sub_once(src, old, new, "resolver + lookup")

    # 1c. SetMaterialImagePointer — update arm and insert arm.
    old = """    for (M1State::MaterialRecord& r : st.materials)
    {
        if (r.part == partIndex && r.imageName == name)
        {
            r.image = pImage;
            return;
        }
    }
    M1State::MaterialRecord r;
    r.part      = partIndex;
    r.imageName = name;
    r.image     = pImage;
    st.materials.push_back(std::move(r));
}"""
    new = """    for (M1State::MaterialRecord& r : st.materials)
    {
        if (r.part == partIndex && r.imageName == name)
        {
            r.image = pImage;
            ResolveOverrideTexture(m_texCache, pImage, r.texture, r.textureRef);
            return;
        }
    }
    M1State::MaterialRecord r;
    r.part      = partIndex;
    r.imageName = name;
    r.image     = pImage;
    ResolveOverrideTexture(m_texCache, pImage, r.texture, r.textureRef);
    st.materials.push_back(std::move(r));
}"""
    src = sub_once(src, old, new, "SetMaterialImagePointer arms")

    # 1d. SetMaterialData — the NAMED arm only (the empty-name arm sets no image).
    old = """        if (r.part == partIndex && r.imageName == name)
        {
            r.image          = pImage;
            r.hasSpecular    = true;"""
    new = """        if (r.part == partIndex && r.imageName == name)
        {
            r.image          = pImage;
            ResolveOverrideTexture(m_texCache, pImage, r.texture, r.textureRef);
            r.hasSpecular    = true;"""
    src = sub_once(src, old, new, "SetMaterialData update arm")

    old = """    M1State::MaterialRecord r;
    r.part           = partIndex;
    r.imageName      = name;
    r.image          = pImage;
    r.hasSpecular    = true;"""
    new = """    M1State::MaterialRecord r;
    r.part           = partIndex;
    r.imageName      = name;
    r.image          = pImage;
    ResolveOverrideTexture(m_texCache, pImage, r.texture, r.textureRef);
    r.hasSpecular    = true;"""
    src = sub_once(src, old, new, "SetMaterialData insert arm")

    write(PARTS, src)
    changed.append(PARTS)

# ─────────────────────────────────────────────────────────────────────────────
# 2. BgfxModel.h — declare the lookup
# ─────────────────────────────────────────────────────────────────────────────
hdr = read(HEADER)
if "TriGroupOverrideTexture" not in hdr:
    old = """    float    TriGroupSpecularPower(uint32_t partIndex, const std::string& mtrlName) const;"""
    new = """    float    TriGroupSpecularPower(uint32_t partIndex, const std::string& mtrlName) const;
    // The SKIN SUBSTITUTION's read side, and the twin of the call above: the texture
    // SetMaterialImagePointer / SetMaterialData put over ONE material, or UINT16_MAX
    // when that material has none. This is CGrannyMaterialPalette::
    // SetMaterialImagePointer's whole observable effect — the palette swaps the image
    // on the matching material and the device binds it — and until it existed the
    // override was stored and never bound, so every ShapeData entry with
    // TargetSkin != SourceSkin drew the SourceSkin: the lowest armour tier sharing
    // that .gr2. Defined in BgfxModelParts.cpp, where M1State is complete.
    uint16_t TriGroupOverrideTexture(uint32_t partIndex, const std::string& mtrlName) const;"""
    hdr = sub_once(hdr, old, new, "header decl")
    write(HEADER, hdr)
    changed.append(HEADER)

# ─────────────────────────────────────────────────────────────────────────────
# 3. BgfxModelRender.cpp — bind the override
# ─────────────────────────────────────────────────────────────────────────────
rnd = read(RENDER)
if "skinSource" not in rnd:
    old = """                        const BgfxModel* specularSource, uint32_t partIndex)"""
    new = """                        const BgfxModel* specularSource, uint32_t partIndex,
                        const BgfxModel* skinSource)"""
    rnd = sub_once(rnd, old, new, "SubmitMeshNodeList signature")

    old = """            d.texture      = tg.texture;"""
    new = """            d.texture      = tg.texture;
            // >>> AND THE SKIN SWAP WINS OVER THE .gr2's OWN TEXTURE. <<<
            //
            // tg.texture is what ResolveTextures read out of the model file. For a
            // character that is only the DEFAULT: warrior_nahan.gr2 ships
            // warrior_nahan.dds, and shapes 4 and 5 are the same mesh with
            // warrior_giryung.dds / warrior_jaho.dds put over it by the ShapeData's
            // TargetSkin (root.zip/warrior_m.msm). CActorInstance::SetShape hands
            // those to SetMaterialData/SetMaterialImagePointer; without this lookup
            // the record was stored and never read, every such armour fell back to
            // its family's base skin, and the player wore the LOWEST tier that shares
            // the mesh — "armour always one tier too low", every class, every tier.
            //
            // UNLIKE specularSource this is passed on EVERY pass: a skin swap is not
            // a highlight, and a blend-list tri-group wearing the wrong texture is
            // the same defect as an opaque one.
            if (skinSource != nullptr)
            {
                const uint16_t swapped =
                    skinSource->TriGroupOverrideTexture(partIndex, tg.materialName);
                if (swapped != UINT16_MAX)
                    d.texture = swapped;
            }"""
    rnd = sub_once(rnd, old, new, "draw-time override")

    n_this = rnd.count("this, part.partIndex);")
    n_null = rnd.count("nullptr, part.partIndex);")
    if (n_this, n_null) != (1, 2):
        raise SystemExit("call sites: found %d 'this' and %d 'nullptr' (expected 1 and 2)"
                         % (n_this, n_null))
    rnd = rnd.replace("this, part.partIndex);", "this, part.partIndex, this);")
    rnd = rnd.replace("nullptr, part.partIndex);", "nullptr, part.partIndex, this);")

    write(RENDER, rnd)
    changed.append(RENDER)

if changed:
    print("patched:")
    for c in changed:
        print("  " + c)
else:
    print("already patched — nothing to do")
