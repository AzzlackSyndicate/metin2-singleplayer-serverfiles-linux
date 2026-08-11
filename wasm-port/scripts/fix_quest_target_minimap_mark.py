#!/usr/bin/env python3
# fix_quest_target_minimap_mark.py
#
# WHAT THIS FIXES, and why it is the minimap and not the arrow.
#
# A quest target ("Questpfeil") has TWO displays and ONE piece of state: the
# TAtlasMarkInfo the CREATE packet pushes into CPythonMiniMap's waypoint vector,
# and the BgfxScene::TargetEffect record the SAME handler pushes into the scene.
# Both key on the same target id and the same m_dwChrVID.
#
#   * the head-of-NPC arrow -> BgfxScene::UpdateTargetEffects -> direction_land.mse
#   * the minimap mark      -> CPythonMiniMap::Render's "// Target" block
#
# In the ORIGINAL client the second one is PythonMiniMap.cpp:452-469 and draws
# __RenderTargetMark(m_fMiniMapX, m_fMiniMapY). In THIS tree __RenderTargetMark
# has exactly one caller — PythonMiniMapRenderD3D.cpp:325 — which is the D3D9
# fixed-function arm and cannot run on bgfx/Linux/wasm. __RenderNeutral, the arm
# that DOES run, never had that block ported: grep TYPE_TARGET over
# PythonMiniMap.cpp hits Update(), the two CreateTarget()s and
# __RenderAtlasNeutral, and nothing inside __RenderNeutral.
#
# So the minimap NEVER shows a quest target in this build. It is not "correctly
# silent" — it is blind, and it therefore cannot be used as the oracle that says
# the head arrow is wrong.
#
# The mark drawn here is the ATLAS-vs-MINIMAP coordinate distinction the neutral
# arm otherwise gets right: m_fMiniMapX/Y (Update():396-412) is an absolute screen
# position that already carries m_fScreenX/Y and the 55px radius clamp;
# m_fScreenX/Y is atlas-local and belongs to __RenderAtlasNeutral only.
#
# Idempotent: every edit has its own file-wide-unique marker and is skipped only
# when THAT marker is present. Anchors are asserted to occur exactly once.

import sys

H   = "/opt/m2wasm/src/PyLib/src/bindings/minimap/PythonMiniMap.h"
CPP = "/opt/m2wasm/src/PyLib/src/bindings/minimap/PythonMiniMap.cpp"

changed = []


def read(p):
    with open(p, "r", encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def write(p, t):
    with open(p, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(t)


def patch(path, marker, anchor, new_text, label):
    """Replace the single occurrence of `anchor` with `new_text`, unless `marker`
    is already in the file. `marker` must be unique to THIS edit."""
    text = read(path)
    if marker in text:
        print("  skip (already applied): %s" % label)
        return False
    n = text.count(anchor)
    if n != 1:
        sys.exit("ANCHOR NOT UNIQUE (%d hits) for %s in %s\n---\n%s\n---"
                 % (n, label, path, anchor))
    write(path, text.replace(anchor, new_text, 1))
    print("  applied: %s" % label)
    changed.append(label)
    return True


# ── 1. the member: two ITexture frames of targetmark, the neutral arm's copy of
#      m_TargetMarkGraphicImageInstances (Create():946-951) ────────────────────
patch(
    H,
    "M2FIX_QUESTARROW_TARGETMARK_MEMBER",
    """		std::unique_ptr<EngineLib::ITexture>         m_NeutralGuildAreaFlag;
""",
    """		std::unique_ptr<EngineLib::ITexture>         m_NeutralGuildAreaFlag;

		// M2FIX_QUESTARROW_TARGETMARK_MEMBER — the MINIMAP's blinking quest-target
		// sprite, which is NOT one of the atlas sprites above.
		//
		// Create():946-951 loads targetmark01/02.sub into
		// m_TargetMarkGraphicImageInstances for the D3D9 arm. __RenderTargetMark
		// (:1947) draws them, and its ONLY caller is PythonMiniMapRenderD3D.cpp:325 —
		// so on bgfx/Linux/wasm the minimap drew no quest target at all while the
		// head-of-NPC arrow (BgfxScene::UpdateTargetEffects) drew one from the SAME
		// TAtlasMarkInfo's m_dwChrVID. Two displays of one fact, disagreeing because
		// one of them was never ported. Loaded in __EnsureNeutralMarks.
		std::unique_ptr<EngineLib::IExpandedTexture> m_NeutralTargetMark[TARGET_MARK_IMAGE_COUNT];
""",
    "PythonMiniMap.h: m_NeutralTargetMark member",
)

# ── 2. load the two frames next to the other minimap marks ───────────────────
patch(
    CPP,
    "M2FIX_QUESTARROW_TARGETMARK_LOAD",
    """	m_NeutralCameraMark = rkEngine.CreateExpandedTexture("d:/ymir work/ui/minimap_camera.dds");
""",
    """	m_NeutralCameraMark = rkEngine.CreateExpandedTexture("d:/ymir work/ui/minimap_camera.dds");

	// M2FIX_QUESTARROW_TARGETMARK_LOAD — the quest-target frames, same two files and
	// the same RENDERING_MODE_SCREEN as Create():946-951 gives the D3D9 arm. They live
	// with the MINIMAP marks and not with the atlas ones (__EnsureNeutralAtlasMarks)
	// because __RenderNeutral is their only consumer; the atlas draws the waypoint
	// sprites instead, which is the original's split (Render():452-469 vs
	// RenderAtlas():1003-1023) and not an arbitrary one.
	{
		char szTargetMark[256];
		for (int k = 0; k < TARGET_MARK_IMAGE_COUNT; ++k)
		{
			snprintf(szTargetMark, sizeof(szTargetMark),
			         "d:/ymir work/ui/minimap/targetmark%02d.sub", k + 1);
			m_NeutralTargetMark[k] = rkEngine.CreateExpandedTexture(szTargetMark);
			if (m_NeutralTargetMark[k])
				m_NeutralTargetMark[k]->SetRenderingMode(EngineLib::IExpandedTexture::RENDERING_MODE_SCREEN);
			else
				SPDLOG_WARN("CPythonMiniMap — targetmark{:02d}.sub did not load; the minimap "
				            "will not show quest targets.", k + 1);
		}
	}
""",
    "PythonMiniMap.cpp: __EnsureNeutralMarks loads targetmark",
)

# ── 3. draw it, in the original's place: after the player arrow, before the
#      camera cone (orig Render():440-470) ──────────────────────────────────
patch(
    CPP,
    "M2FIX_QUESTARROW_TARGETMARK_RENDER",
    """	if (m_NeutralCameraMark)
	{
		m_NeutralCameraMark->SetRotation(EngineLib::Engine::Instance().GetCamera().GetRoll());""",
    """	// ── the quest-target mark ──────────────────────────────────────────────────────
	//
	// M2FIX_QUESTARROW_TARGETMARK_RENDER. This is the ORIGINAL client's
	// CPythonMiniMap::Render "// Target" block (PythonMiniMap.cpp:452-469), in the
	// original's position — after the player arrow, before the camera cone — and it had
	// NO neutral port. __RenderTargetMark's only caller in this tree is the D3D9 arm
	// (PythonMiniMapRenderD3D.cpp:325), so on this backend the minimap showed no quest
	// target at all. That is what made the minimap look like it "correctly" disagreed
	// with the arrow over the NPC's head: both read the same TAtlasMarkInfo, but only
	// one of them was drawing.
	//
	// >>> m_fMiniMapX/Y ARE NOT m_fScreenX/Y AND SUBSTITUTING ONE FOR THE OTHER IS THE
	// MISTAKE THIS BLOCK EXISTS TO AVOID. <<< Update():396-412 writes m_fMiniMapX/Y as an
	// ABSOLUTE screen position that already carries m_fScreenX/Y and the 55px rim clamp.
	// m_fScreenX/Y (__UpdateWayPoint:1922) is ATLAS-LOCAL and is what
	// __RenderAtlasNeutral draws the waypoint sprites at, offset by the atlas window's
	// own origin. Feeding the atlas pair to the minimap puts the mark up to a full
	// atlas-image width away from the circle.
	//
	// The `<= 0.0f` skips and the /80 two-frame blink are the reference's
	// (Render():462-464 and __RenderTargetMark:1949), not a rate chosen here.
	{
		const size_t uFrame =
			static_cast<size_t>((ELTimer_GetMSec() / 80) % TARGET_MARK_IMAGE_COUNT);
		EngineLib::IExpandedTexture* pTargetSprite = m_NeutralTargetMark[uFrame].get();
		if (pTargetSprite)
		{
			for (const TAtlasMarkInfo& rInfo : m_AtlasWayPointInfoVector)
			{
				if (TYPE_TARGET != rInfo.m_byType)
					continue;
				if (rInfo.m_fMiniMapX <= 0.0f)
					continue;
				if (rInfo.m_fMiniMapY <= 0.0f)
					continue;

				// __RenderTargetMark:1951-1952 centres the sprite on the point it is
				// given; ITexture::Render takes the top-left, so the halving is here
				// instead of inside a SetPosition call this arm does not make.
				pTargetSprite->Render(
					rInfo.m_fMiniMapX - static_cast<float>(pTargetSprite->GetWidth())  / 2.0f,
					rInfo.m_fMiniMapY - static_cast<float>(pTargetSprite->GetHeight()) / 2.0f);
			}
		}
	}

	if (m_NeutralCameraMark)
	{
		m_NeutralCameraMark->SetRotation(EngineLib::Engine::Instance().GetCamera().GetRoll());""",
    "PythonMiniMap.cpp: __RenderNeutral draws the target mark",
)

print("changed: %d edit(s)" % len(changed))
