#!/usr/bin/env python3
"""Skills could not be dragged onto a quick slot: the cursor deattached itself.

WHAT THE OPERATOR SEES: picking a skill up in the skill window and dropping it on
quick slot 1, 2, 3... does nothing at all. Items drag fine.

WHAT IT ACTUALLY IS -- and it is NOT a py2 -> py3 artefact, nothing raises, the
client's PYEXC line stays silent. The path is:

    uicharacter.SelectSkill
      -> mouseController.AttachObject(self, SLOT_TYPE_SKILL, slot, skillIndex)

and inside AttachObject the skill branch read

    self.AttachedIconHandle = skill.GetIconInstanceNew(self.AttachedItemIndex, skillGrade)

which is followed, a dozen lines below, by

    if not self.AttachedIconHandle:
        self.DeattachObject()
        return

skillGetIconInstanceNew builds its instance through NewEterIconInstance, which
opens with `image->GetEterImage()' -- and GetEterImage() is nullptr for every
icon on a non-Eter backend, by design (BgfxEngine has no CGraphicImage). So the
handle came back 0, AttachObject deattached the cursor before it had carried
anything, and every later step was a no-op on an empty cursor: OnDrop reaches
uitaskbar.SelectEmptyQuickSlot, which does nothing unless isAttached().

So the skill was never picked up. The drop was never the problem, which is why
there was nothing to see and nothing in the log.

This is the SAME failure the item branch immediately above it was already fixed
for, and the fix is the same substitution: ask for the icon's FILE NAME, which is
backend-neutral, and load it with grpImage.Generate, which goes through
IEngine::CreateTexture -- implemented by both backends. skill.GetIconName gives
the registered path (RegisterSkill prefixes it with the image path). The cost is
the per-grade icon variant, since only the base name is reachable from script;
the quick slot itself still draws the graded icon, because CSlotWindow renders it
through ITexture and never touches CGraphicImageInstance.

EMOTIONS ARE THE SAME BUG and are fixed here too: their branch called
grpImage.GenerateFromHandle, i.e. Engine::CreateTextureFromHandle, which bgfx
refuses on purpose (the void* is an eter CGraphicImage*) and answers nullptr for.
So emoticons could not be put on a key either. emotion.ICON_DICT is the table the
icons were registered from, so script already knows the file name.

The quick-slot-to-quick-slot branch carries copies of both and gets both fixes,
otherwise dragging a skill from key 1 to key 2 stays broken.

DeattachObject has to follow: an icon must be freed through the registry it was
minted from. skill.DeleteIconInstance looks in PyLib::ImageInstanceHandles, while
grpImage.Generate mints into grpImage's own texture map -- and BOTH counters
start at 1, so calling the wrong one does not merely leak, it can free an
unrelated image that happens to share the number. SLOT_TYPE_QUICK_SLOT is added
to that branch because every one of its sub-paths now mints from grpImage as
well; it previously freed nothing at all.

WHAT WAS RULED OUT: uitaskbar.AddQuickSlot / SelectEmptyQuickSlot /
SelectItemQuickSlot are correct and py3-clean; so is uicharacter.SelectSkill and
its slot-index arithmetic (__GetSkillGradeFromSlot already wraps its division in
int()). No function in either file shadows a builtin the way FindMember did.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")

REL = "bin/pack/root/mousemodule.py"
MARKER = "skill.GetIconName(self.AttachedItemIndex)"

EDITS = [
    # Picking a skill up out of the skill window.
    (
        """			elif Type == player.SLOT_TYPE_SKILL:
				skillGrade = player.GetSkillGrade(SlotNumber)
				self.AttachedIconHandle = skill.GetIconInstanceNew(self.AttachedItemIndex, skillGrade)
""",
        """			elif Type == player.SLOT_TYPE_SKILL:
				# The same substitution as the item branch above, and the reason a
				# skill could not be dragged onto a quick slot at all:
				# GetIconInstanceNew builds its instance from GetEterImage(), which is
				# nullptr for every icon on a non-Eter backend, so the handle came back
				# 0 and the `if not self.AttachedIconHandle' below deattached the
				# cursor before the player ever reached slot 1. GetIconName is the path
				# the icon was registered under, and grpImage.Generate goes through
				# IEngine::CreateTexture, which both backends implement.
				#
				# It costs the per-grade icon variant -- only the base name is reachable
				# from script -- while the slot itself still draws the graded one.
				self.AttachedIconHandle = grpImage.Generate(skill.GetIconName(self.AttachedItemIndex))
""",
    ),
    # Picking an emoticon up out of the emotion page.
    (
        """			elif Type == player.SLOT_TYPE_EMOTION:
				image = player.GetEmotionIconImage(ItemIndex)
				self.AttachedIconHandle = grpImage.GenerateFromHandle(image)
""",
        """			elif Type == player.SLOT_TYPE_EMOTION:
				# GenerateFromHandle wraps a raw eter CGraphicImage*, which bgfx refuses
				# by design rather than invent a meaning for the pointer, so this handle
				# was 0 too and emoticons could not be put on a key either.
				# emotion.ICON_DICT is the table they were registered from, so the file
				# name is already known here.
				import emotion
				self.AttachedIconHandle = grpImage.Generate(emotion.ICON_DICT.get(ItemIndex, ""))
""",
    ),
    # Dragging a skill from one quick slot to another.
    (
        """				elif quickSlotType == player.SLOT_TYPE_SKILL:
					skillIndex = player.GetSkillIndex(position)
					skillGrade = player.GetSkillGrade(position)
					self.AttachedIconHandle = skill.GetIconInstanceNew(skillIndex, skillGrade)
""",
        """				elif quickSlotType == player.SLOT_TYPE_SKILL:
					# Same substitution as above -- quickslot to skill.
					skillIndex = player.GetSkillIndex(position)
					self.AttachedIconHandle = grpImage.Generate(skill.GetIconName(skillIndex))
""",
    ),
    # Dragging an emoticon from one quick slot to another.
    (
        """				elif quickSlotType == player.SLOT_TYPE_EMOTION:
					image = player.GetEmotionIconImage(position)
					self.AttachedIconHandle = grpImage.GenerateFromHandle(image)
""",
        """				elif quickSlotType == player.SLOT_TYPE_EMOTION:
					# Same substitution as above -- quickslot to emotion.
					import emotion
					self.AttachedIconHandle = grpImage.Generate(emotion.ICON_DICT.get(position, ""))
""",
    ),
    # And free the icon through the registry it is minted from.
    (
        """			elif self.AttachedType == player.SLOT_TYPE_SKILL:
				skill.DeleteIconInstance(self.AttachedIconHandle)
""",
        """			elif self.AttachedType == player.SLOT_TYPE_SKILL or\\
				self.AttachedType == player.SLOT_TYPE_QUICK_SLOT:

				# Every branch of AttachObject mints from grpImage now, so this is the
				# registry that owns the handle. skill.DeleteIconInstance looks in
				# ImageInstanceHandles, whose counter also starts at 1 -- it would not
				# free this icon and could free an unrelated one with the same number.
				# The quick slot type used to fall through here and free nothing.
				grpImage.Delete(self.AttachedIconHandle)
""",
    ),
]


def main():
    path = os.path.join(ROOT, REL)
    if not os.path.isfile(path):
        sys.exit("not found: %s (set M2WASM to the client tree)" % path)

    s = io.open(path, encoding="utf-8", errors="surrogateescape").read()
    if MARKER in s:
        print("already patched: %s" % REL)
        return

    for old, new in EDITS:
        if s.count(old) != 1:
            sys.exit("anchor not found exactly once in %s:\n%s" % (REL, old))
        s = s.replace(old, new, 1)

    io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)
    print("patched: %s" % REL)


if __name__ == "__main__":
    main()
