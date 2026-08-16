#!/usr/bin/env python3
"""Skills cannot be dragged, because skill.GetIconName() returns "" for all of them.

THE REPORT: "warum im Webclient die Fertigkeiten nicht mit Drag and Drop hin und
hergezogen werden koennen. Es funktioniert mit Items, aber immer noch nicht mit
Fertigkeiten. Im Desktop Client funktioniert es."

"immer noch nicht" is the important word. fix_skill_drag_icon_handle.py already
repaired the Python side of this and the repair is live in the shipped data --
it is correct, and it is inert, because the getter it calls is empty on this
build. This is the other half.

WHAT WAS MEASURED

The drag starts at uicharacter.py:614 (skill window) and uitaskbar.py:862
(quickslot to quickslot), both calling mouseController.AttachObject with
player.SLOT_TYPE_SKILL. mousemodule.py:232 then does

    self.AttachedIconHandle = grpImage.Generate(skill.GetIconName(...))

and PythonGraphicImageModule.cpp:37 answers an empty filename with handle 0:

    if (!*szFileName) return Py_BuildValue("i", 0);

mousemodule.py:264 tests that handle, calls DeattachObject and returns. Nothing
is attached to the cursor, so wndMgr.AttachIcon is never reached and BOTH drop
paths -- CSlotWindow::OnMouseLeftButtonUp (PythonSlotWindow.cpp:812) and
uitaskbar.py:851 -- correctly refuse a drop that was never picked up.

No exception is raised anywhere along that path. It is a clean `return', which
is why nothing appears in the browser console and why earlier passes over this
bug found nothing to look at.

WHY THE FIELD IS EMPTY

strIconFileName is assigned in exactly two places, PythonSkill.cpp:649 and :752,
both inside CPythonSkill::RegisterSkill -- which is reachable only through the
skill.RegisterSkill binding, and no script in this client calls it. The
registration that actually runs is RegisterSkillDesc + RegisterSkillTable, from
PythonApplication.cpp:1262/1268, and its icon block composes the path as a LOCAL
inside __RegisterGradeIconImage / __RegisterNormalIconImage, uses it for
ResourceGetImage and discards it. skill.LoadSkillData(), the only skill call the
scripts do make, is an empty stub.

So the field has been empty for every skill since this build existed.

WHY ITEMS WORK AND SKILLS DO NOT

The two drags are byte-identical through the whole C++ input stack -- same
CWindowManager::RunMouseLeftButtonDown/Move/Up, same m_lPickedX, same 10-pixel
IsDragging() threshold, same CSlotWindow. They diverge at one place only: the
Type == switch inside AttachObject. Items ask
item.GetIconImageFileName (PythonItemModule.cpp:121), which reads
IImageResource::GetFileName() -- a real string on bgfx. Skills ask for a field
nothing fills.

WHY THE DESKTOP CLIENT IS FINE

That is the original Windows D3D9/eter client. Before the port this branch went
through skill.GetIconInstanceNew -> GetEterImage(), which on bgfx is nullptr by
design (IImageResource.h:84, PythonSkill.cpp:2054). The earlier fix diagnosed
that correctly and then moved to a getter that is empty here.

WHAT THIS DOES

Reads the name off the image resource, which is the same mechanism the working
item path uses. GradeData[0] is not a fallback for an odd case: it is the normal
case for an ACTIVE skill, because __RegisterGradeIconImage (PythonSkill.cpp:270)
fills only GradeData[i].pImage and leaves rData.pImage null, while
__RegisterNormalIconImage (:285) fills both.

IImageResource::GetFileName is documented in its own header as "the path this
resource was acquired under, NUL-terminated and stable for the lifetime of the
resource. Callers pass it straight to IEngine::CreateTexture" -- the same string,
by the same route, that makes the item drag work.

WHAT IT COSTS AND WHAT IT DOES NOT

Engine only: one function, no data. The shipped Python side is already correct,
so the 1.75 GB content-addressed data archive is untouched and no chunk is
re-uploaded. The same line also repairs the native Linux desktop build, which
has the identical defect.

Cosmetic consequence, accepted: for a graded skill the cursor ghost shows the
grade-1 icon, because GradeData[0] is what is read. The slot underneath still
draws the graded one. PythonSkill.cpp:872, a debug dump of the skill table,
starts printing a real name instead of an empty one.

Idempotent. Point it at the client tree with M2WASM; a second run reports
`already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
SRC = os.path.join(ROOT, "src/PyLib/src/bindings/player/PythonSkill.cpp")

# The tail of skillGetIconName. Anchored on the whole tail rather than on the
# single return, because `return Py_BuildValue("s", "");' alone appears in other
# getters in this file and matching it there would corrupt one of them.
OLD = '''	CPythonSkill::SSkillData * c_pSkillData = nullptr;
	if (!CPythonSkill::Instance().GetSkillData(iSkillIndex, &c_pSkillData))
		return Py_BuildValue("s", "");

	return Py_BuildValue("s", c_pSkillData->strIconFileName.c_str());
}
'''

NEW = '''	CPythonSkill::SSkillData * c_pSkillData = nullptr;
	if (!CPythonSkill::Instance().GetSkillData(iSkillIndex, &c_pSkillData))
		return Py_BuildValue("s", "");

	// strIconFileName is empty for every skill on this build, so this used to
	// return "" -- and grpImage.Generate("") is handle 0, which aborts the skill
	// drag before anything reaches the cursor. See the head of
	// fix_skill_icon_name_is_always_empty.py for the whole chain.
	//
	// The field is only ever written by CPythonSkill::RegisterSkill, which no
	// script calls. What runs is RegisterSkillDesc, and its icon block builds the
	// path as a local inside __RegisterGradeIconImage / __RegisterNormalIconImage
	// and throws it away. So the name is read off the image resource instead,
	// which is exactly what the working item path does.
	//
	// GradeData[0] is the normal case, not a fallback: __RegisterGradeIconImage
	// fills only GradeData[i].pImage and leaves rData.pImage null for every
	// ACTIVE skill, while __RegisterNormalIconImage fills both.
	EngineLib::IImageResource * pIconImage = c_pSkillData->pImage;

	if (!pIconImage)
		pIconImage = c_pSkillData->GradeData[0].pImage;

	if (pIconImage && pIconImage->GetFileName() && *pIconImage->GetFileName())
		return Py_BuildValue("s", pIconImage->GetFileName());

	return Py_BuildValue("s", c_pSkillData->strIconFileName.c_str());
}
'''

MARKER = "pIconImage = c_pSkillData->GradeData[0].pImage;"


def main():
    if not os.path.isfile(SRC):
        sys.exit("not found: %s (set M2WASM to the client tree)" % SRC)

    text = io.open(SRC, encoding="utf-8", newline="").read()

    if MARKER in text:
        print("   already patched: src/PyLib/src/bindings/player/PythonSkill.cpp")
        return

    # The struct fields this depends on. Checked here rather than discovered by
    # the compiler, so a rename upstream stops the patch with a sentence instead
    # of producing a build error three minutes into a link.
    header = os.path.join(ROOT, "src/PyLib/src/bindings/player/PythonSkill.h")
    if not os.path.isfile(header):
        sys.exit("not found: %s" % header)
    hdr = io.open(header, encoding="utf-8").read()
    for field in ("TGradeData GradeData[SKILL_EFFECT_COUNT];",
                  "EngineLib::IImageResource * pImage = nullptr;"):
        if field not in hdr:
            sys.exit("PythonSkill.h no longer declares `%s'.\n"
                     "The icon now lives somewhere else and this patch would "
                     "read a field that is gone. Nothing was changed." % field)

    count = text.count(OLD)
    if count != 1:
        sys.exit("skillGetIconName does not look the way this patch expects "
                 "(%d matches, wanted exactly 1). PythonSkill.cpp was not "
                 "changed." % count)

    io.open(SRC, "w", encoding="utf-8", newline="").write(text.replace(OLD, NEW))
    print("   patched: src/PyLib/src/bindings/player/PythonSkill.cpp")
    print("   skill.GetIconName() now answers with the image resource's own path,")
    print("   so dragging a skill to a quick slot attaches an icon and completes.")
    print("   THE ENGINE HAS TO BE REBUILT for this to reach anyone. The data")
    print("   archive is untouched -- the Python half of this fix already ships.")


if __name__ == "__main__":
    main()
