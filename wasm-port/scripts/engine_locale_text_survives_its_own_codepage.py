#!/usr/bin/env python3
"""An item tooltip in German raises an exception, because "s" means strict UTF-8.

WHAT WAS REPORTED, and what it turned out to be a symptom of: switching the
browser client to German and loading into the world produced

    "Ihr Datensatz ist beschädigt. Bitte Client neu installieren,
     Drücke jetzt die Taste ESC."

That message has exactly one show site in the whole corpus —
bin/pack/root/introloading.py:266, a bare `except:` around every world-load step
which calls errMsg.Show() and app.Exit() on ANY Python exception. It is not a
data check. It accuses the installation of being broken whenever anything at all
raises during loading, which is how a decoding fault came to be reported as a
corrupt download.

The immediate cause was found and fixed in the DATA
(locale_guild_building_list_is_utf8.py): guildbuildinglist.txt is stored in each
locale's own ANSI codepage, app.GetTextFileLine hands it to Py_BuildValue("s"),
and on Python 3 "s" is PyUnicode_FromString — a STRICT UTF-8 decode. German's
`R\xfcstungsschmiede` is not valid UTF-8, so it returned NULL with a
UnicodeDecodeError set, and introloading turned that into the reinstall message.

═══════════════════════════════════════════════════════════════════════════════
>>> BUT THE TRAP IS NOT IN THAT FILE. IT IS IN THE BINDING, AND IT IS STILL SET.
═══════════════════════════════════════════════════════════════════════════════

Converting one text file fixed the world load and nothing else. Every OTHER
locale asset is still handed to the same strict decode, and the two that matter
most are read on every mouse-over rather than once at startup:

    item.GetItemName / GetItemDescription / GetItemSummary
        PythonItemModule.cpp — the strings come out of item_proto, which is
        stored per locale, in that locale's codepage.
    nonplayer.GetMonsterName
        PythonNonPlayerModule.cpp — same, out of mob_proto.

So a German player who got past the loading screen would have hit this again the
first time they pointed at an item called `Rüstung` or a monster called `Wildhund
(Bär)` — one raise per tooltip, at a point where this client treats a callback
exception as fatal (see fix_callback_exception_is_fatal.py in this directory).
Shipping fifteen languages with that live would have traded a broken loading
screen for a client that dies when you look at your own armour.

app.GetTextFileLine is fixed here too, because the data fix cured the ONE file in
the corpus that goes through it today and not the reader itself — the next locale
text file added would walk into the identical bug, and the next reader of it
would get the same unreadable accusation instead of a decoding error.

── WHY A HELPER, AND WHY IT DECODES TWICE ─────────────────────────────────────

Two of these bindings already do half of it. appLoadLocaleAddr
(PythonApplicationModule.cpp:391) and wndMgrGetHyperlink
(PythonWindowManagerModule.cpp:710) both try UTF-8 and fall back to UTF-8 with
"replace", with a comment saying that a UnicodeDecodeError from inside a decrypt
path is a worse failure than visible garbage. That is right about the failure
mode and wrong about the remedy for THESE strings, because here we know
something they did not: what the bytes actually are.

    utf-8 strict          the file may genuinely be UTF-8. French's
                          guildbuildinglist.txt already is, and every locale's
                          is after the data fix. This arm is the common case and
                          it must be tried first.
    the locale's codepage the honest answer for an asset that was authored in
                          it. 1250 for cz/hu/pl/ro, 1251 ru, 1252 for the
                          western fifteen, 1253 gr, 1254 tr — the same table the
                          page writes into locale.cfg, and the engine already
                          holds the number: LocaleService_GetCodePage().
                          THIS ARM IS WHY THE TEXT COMES OUT RIGHT rather than
                          merely non-fatal: `Rüstung` decoded as cp1252 is
                          `Rüstung`, decoded as utf-8/replace it is `R?stung`.
    utf-8 replace         never reached in practice; it is there so that a
                          codec the frozen stdlib does not carry, or a byte
                          sequence in no encoding at all, still returns a string
                          instead of NULL. A tooltip with a question mark in it
                          is a cosmetic bug. A tooltip that raises is a dead
                          client.

The helper is a header rather than three copies because the three call sites are
in three different modules and a fourth will be added by whoever adds the next
locale-backed string. It is header-only and inline, so nothing in the build
system has to learn about it.

── WHAT THIS DOES NOT DO ──────────────────────────────────────────────────────

It does not re-encode any asset and it does not touch the loading screen's bare
`except:`. That except is a separate fault worth its own change — it reports
every possible failure as a corrupt installation, which cost this diagnosis most
of its length — but widening it is a behavioural decision about what a player
should be told, not a decoding fix, and the two do not belong in one patch.

Idempotent. Point it at the client tree with M2WASM; a second run reports
`already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")

HEADER_PATH = os.path.join(ROOT, "src/PyLib/include/PyLib/LocaleText.h")

HEADER = """#pragma once

// LocaleText.h -- turning a locale asset's bytes into a Python string without
// raising, and without losing the letters.
//
// Py_BuildValue("s", ...) is PyUnicode_FromString, which is a STRICT UTF-8
// decode. Every string that comes out of a locale asset -- item_proto,
// mob_proto, the locale's own text files -- is stored in that locale's ANSI
// codepage, so the moment a name contains a letter outside ASCII the binding
// returns NULL with a UnicodeDecodeError set. Measured: eleven of the fifteen
// shipped locales carry such a letter, and in German it is the word for
// "armour".
//
// Three arms, in this order, and the middle one is the point:
//
//   utf-8 strict           the asset may genuinely be UTF-8 (French's
//                          guildbuildinglist.txt already was, and every
//                          locale's is now). Common case, tried first.
//   the locale's codepage  the honest answer for an asset authored in it.
//                          LocaleService_GetCodePage() is the number the
//                          client read out of locale.cfg at startup, so this
//                          cannot drift from the language actually loaded.
//                          Decoded with "replace" rather than strictly: a
//                          single bad byte in a 5,743-entry proto must not
//                          cost the whole tooltip.
//   utf-8 replace          unreachable in practice. It exists so that a codec
//                          the frozen stdlib does not carry still yields a
//                          string rather than NULL. A question mark in a
//                          tooltip is cosmetic; a raise is fatal, because this
//                          client treats a callback exception as fatal.
//
// Never returns NULL and never leaves an exception set.

#include <Python.h>
#include <cstring>

#include "PyLib/LocaleService.h"

inline const char* PyLocaleCodecName(unsigned codePage)
{
    switch (codePage)
    {
        case 1250: return "cp1250";   // cz hu pl ro
        case 1251: return "cp1251";   // ru
        case 1252: return "cp1252";   // en de fr es it nl pt dk
        case 1253: return "cp1253";   // gr
        case 1254: return "cp1254";   // tr
        default:   return nullptr;    // an unknown page: skip the arm, do not guess
    }
}

inline PyObject* PyLocaleText(const char* s)
{
    if (!s)
        return PyUnicode_FromString("");

    const Py_ssize_t n = static_cast<Py_ssize_t>(std::strlen(s));

    PyObject* text = PyUnicode_DecodeUTF8(s, n, nullptr);
    if (text)
        return text;
    PyErr_Clear();

    if (const char* codec = PyLocaleCodecName(LocaleService_GetCodePage()))
    {
        text = PyUnicode_Decode(s, n, codec, "replace");
        if (text)
            return text;
        PyErr_Clear();
    }

    text = PyUnicode_DecodeUTF8(s, n, "replace");
    if (!text)
    {
        PyErr_Clear();
        text = PyUnicode_FromString("");
    }
    return text;
}
"""

# (file, include-anchor, [(old, new), ...])
EDITS = [
    (
        "src/PyLib/src/bindings/item/PythonItemModule.cpp",
        '#include "PyLib/PythonPtrHandle.h"',
        [
            ('\treturn Py_BuildValue("s", pItemData->GetName());',
             '\t// item_proto is stored in the locale\'s codepage -- see PyLocaleText.\n'
             '\treturn PyLocaleText(pItemData->GetName());'),
            ('\treturn Py_BuildValue("s", pItemData->GetDescription());',
             '\treturn PyLocaleText(pItemData->GetDescription());'),
            ('\treturn Py_BuildValue("s", pItemData->GetSummary());',
             '\treturn PyLocaleText(pItemData->GetSummary());'),
        ],
    ),
    (
        "src/PyLib/src/bindings/nonplayer/PythonNonPlayerModule.cpp",
        '#include "GameLib/PythonNonPlayer.h"',
        [
            ('\treturn Py_BuildValue("s", rkNonPlayer.GetMonsterName(iVNum));',
             '\t// mob_proto is stored in the locale\'s codepage -- see PyLocaleText.\n'
             '\treturn PyLocaleText(rkNonPlayer.GetMonsterName(iVNum));'),
        ],
    ),
    (
        "src/PyLib/src/bindings/app/PythonApplicationModule.cpp",
        '#include "platform/input/DirectInputKeyCodes.h"',
        [
            ('\treturn Py_BuildValue("s", it->second->GetLine(iLineIndex));',
             '\t// A locale text file is stored in the locale\'s codepage. This one line\n'
             '\t// is what turned a decoding fault into "your data is corrupted, please\n'
             '\t// reinstall": introloading.py wraps every load step in a bare except and\n'
             '\t// shows that message for any exception at all. See PyLocaleText.\n'
             '\treturn PyLocaleText(it->second->GetLine(iLineIndex));'),
        ],
    ),
]

INCLUDE_LINE = '#include "PyLib/LocaleText.h"'
MARKER = "PyLocaleText"


def main():
    if not os.path.isdir(ROOT):
        sys.stderr.write("not found: %s\n(set M2WASM to the client tree)\n" % ROOT)
        return 1

    # Everything is checked before anything is written: a half-applied set here
    # is a tree that does not build, and the next reader would have to work out
    # which of five edits landed.
    plan = []
    already = os.path.isfile(HEADER_PATH)
    for rel, inc_anchor, pairs in EDITS:
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            sys.stderr.write("not found: %s\n" % path)
            return 1
        src = io.open(path, encoding="utf-8").read()
        if MARKER in src:
            continue
        if src.count(inc_anchor) != 1:
            sys.stderr.write("include anchor not found exactly once in %s (%d)\n"
                             % (rel, src.count(inc_anchor)))
            return 1
        for old, _new in pairs:
            if src.count(old) != 1:
                sys.stderr.write("anchor not found exactly once in %s (%d):\n  %s\n"
                                 % (rel, src.count(old), old.strip()))
                return 1
        plan.append((path, rel, src, inc_anchor, pairs))

    if not plan and already:
        print("already patched: the header and all five call sites")
        return 0

    if not already:
        d = os.path.dirname(HEADER_PATH)
        if not os.path.isdir(d):
            sys.stderr.write("not found: %s\n" % d)
            return 1
        io.open(HEADER_PATH, "w", encoding="utf-8", newline="\n").write(HEADER)
        print("wrote:   src/PyLib/include/PyLib/LocaleText.h")

    for path, rel, src, inc_anchor, pairs in plan:
        src = src.replace(inc_anchor, inc_anchor + "\n" + INCLUDE_LINE, 1)
        for old, new in pairs:
            src = src.replace(old, new, 1)
        io.open(path, "w", encoding="utf-8", newline="\n").write(src)
        print("patched: %s (%d call site%s)" % (rel, len(pairs), "" if len(pairs) == 1 else "s"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
