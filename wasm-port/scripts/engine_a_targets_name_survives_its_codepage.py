#!/usr/bin/env python3
"""The target bar showed a health bar and no name, in Turkish.

REPORTED with a screenshot: a metin stone, its floating name drawn correctly
over the world, and the target bar at the top of the screen showing the health
gauge and the close button with the name missing entirely.

THE TWO NAMES COME FROM DIFFERENT PLACES, and that is the whole diagnosis. The
floating one is drawn by the engine, in C++, straight from the bytes in
mob_proto -- no Python anywhere. The one in the target bar goes through
chr.GetNameByVID, and that binding ended in

    return Py_BuildValue("s", pInstance->GetNameString());

which is PyUnicode_FromString, which is a STRICT UTF-8 decode. The Turkish
mob_proto is cp1254 and holds 26,696 non-ASCII bytes -- measured, not assumed --
so the first Turkish name with an s-cedilla in it returns NULL with a
UnicodeDecodeError set. This is the trap already written down in
docs/agents/CODEBASE-MAP.md; this is simply a site that the earlier pass missed.

WHY THE BAR APPEARS AT ALL, WITH A GAUGE AND NO TEXT. uitarget.SetEnemyVID
calls chr.GetNameByVID first and SetTargetName last, and game.py does:

    if vid != self.targetBoard.GetTargetVID():
        self.targetBoard.ResetTargetBoard()
        self.targetBoard.SetEnemyVID(vid)      <- raises here
    self.targetBoard.SetHP(hpPercentage)
    self.targetBoard.Show()

So the FIRST update for a new target dies inside SetEnemyVID and never reaches
Show. The next update finds vid already equal, skips the whole branch, and runs
SetHP and Show -- which is a bar, drawn, with the name never having been set.
The symptom is not "the name is empty"; it is "the name was never assigned
because the line before it threw", and it looks like a rendering bug.

TWO SITES, NOT ONE. GetNameByVID is the one in the report; the line fifteen
above it is the same expression in the same file for the main character's name,
and it fails the same way for a player whose own name is not ASCII. Fixing one
and leaving the other is how this trap has already survived two passes.

PyLocaleText is the existing helper and is unchanged by this patch: UTF-8
first, then the locale's own codepage, then lossy. Nothing new is invented here.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
SRC = os.path.join(ROOT, "src/PyLib/src/bindings/chr/CharacterBindings.cpp")
HEADER = '#include "PyLib/LocaleText.h"'

SITES = [
    ('	return Py_BuildValue("s", pCharacterInstance->GetNameString());',
     '	// PyLocaleText, not Py_BuildValue("s", ...): the latter is a strict UTF-8\n'
     '	// decode and every non-ASCII name in a cp1250/1251/1254 locale raises\n'
     '	// UnicodeDecodeError, which aborts the Python caller before it can use\n'
     '	// the name for anything.\n'
     '	return PyLocaleText(pCharacterInstance->GetNameString());'),
    ('	return Py_BuildValue("s", pInstance->GetNameString());',
     '	// The target bar\'s name, and the site the Turkish report was about: the\n'
     '	// exception raised here killed uitarget.SetEnemyVID before it reached\n'
     '	// SetTargetName, so the bar drew a health gauge with no text at all.\n'
     '	return PyLocaleText(pInstance->GetNameString());'),
]


def main():
    src = io.open(SRC, encoding="utf-8").read()
    if "PyLocaleText(pInstance->GetNameString())" in src:
        print("already patched: %s" % SRC)
        return 0

    for old, _ in SITES:
        if src.count(old) != 1:
            sys.stderr.write("a name site was found %d times, expected 1:\n  %s\n"
                             "Nothing changed.\n" % (src.count(old), old.strip()))
            return 1

    out = src
    for old, new in SITES:
        out = out.replace(old, new, 1)

    if HEADER not in out:
        # After the last existing include, so the file's own headers still come
        # first and nothing is inserted above an include guard.
        idx = out.rfind("#include")
        end = out.index("\n", idx) + 1
        out = out[:end] + HEADER + "\n" + out[end:]

    io.open(SRC, "w", encoding="utf-8", newline="\n").write(out)
    print("patched: %s (2 name sites)" % SRC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
