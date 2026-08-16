#!/usr/bin/env python3
"""Non-ASCII letters can be typed: the code point reaches the edit buffer whole.

WHAT WAS REPORTED: "Turkish characters should be allowed, ä ü ö." A player
could not type their own name.

WHAT WAS ACTUALLY WRONG -- three separate truncations of the same value, on the
path a typed character takes from the browser into the game's edit buffer:

    browser keydown, e.key = "ş"
      -> CanvasInput.cpp CodePointForKey     decodes UTF-8 -> U+015F  (correct)
      -> queue -> CPythonApplication::OnChar(0x15F)                   (correct)
      -> CPythonIME::Instance().WMChar(nullptr, WM_CHAR, 0x15F, 0)
         1) CPythonIME::OnWM_CHAR:  c = wParam & 0xff                 <-- cut
         2) CIME::WMChar:           c = wParam & 0xff                 <-- cut
            ImeToWide(ms_uInputCodePage, &c, 1, ...)                  <-- wrong
                                                                          question

The engine's text path is a legacy ANSI codepage (SetDefaultCodePage: 1254 for
Turkish, 1252 for German, 1251 for Russian), and CIME::WMChar was written for
Windows, where WM_CHAR from an ANSI window really does deliver a CODEPAGE BYTE
-- there `wParam & 0xff` loses nothing. Off Windows nobody manufactures
codepage bytes: both non-Windows input bridges (CanvasInput's CodePointForKey,
X11Input's CodepointForKeysym) deliver UNICODE CODE POINTS, and the only other
callers (PythonIMEModule's PasteBackspace/PasteReturn) inject plain ASCII. So
the low-byte cut turned every code point >= 0x100 into a different character:

    ş U+015F -> 0x5F  "_"        ı U+0131 -> 0x31  "1"
    Ş U+015E -> 0x5E  "^"        İ U+0130 -> 0x30  "0"
    ğ U+011F -> 0x1F  (control)  Ğ U+011E -> 0x1E  (control)

ä ö ü ç (U+00E4/F6/FC/E7) only appeared to work by accident: their code points
coincide with their CP1252/CP1254 bytes, so cut-then-ImeToWide round-tripped
them. Under CP1251 the same accident types Cyrillic д for ä -- the byte path is
wrong for everything, it just happens to be invisibly wrong for Latin-1.

THE FIX, in the order the three cuts sit:

  * CPythonIME::OnWM_CHAR returns false for wParam >= 0x80 before truncating.
    It exists to catch the ASCII controls Return/Tab/Escape; after the cut, any
    code point whose LOW BYTE collided with one of them -- U+010D č ends in
    0x0D -- silently became a Return. A real control never has high bits set,
    on any platform, so the early-out is exact and safe on Windows too (a
    codepage byte >= 0x80 hit no case and returned false anyway).

  * CIME::WMChar, off Windows only (#ifndef _WIN32), inserts wParam >= 0x80
    directly as one UTF-16 unit into the wide edit buffer. That is NOT a
    codepage bypass -- it is the honest half of the existing design:
    StringCodec's pivot is UTF-16, m_wText already holds UTF-16 units, and the
    conversion to the CURRENT codepage keeps happening where it always
    happened, on OUTPUT, through ImeToNarrow(ms_uOutputCodePage, ...), which
    CheckInputLocale feeds from GetDefaultCodePage(). Nothing is hardcoded: a
    Turkish client narrows ş to CP1254 0xFE, a German one narrows ä to CP1252
    0xE4, and a character the codepage cannot express fails in ImeToNarrow
    exactly as it does on Windows. The event sink is still consulted first and
    the capture gate still holds, in the same order as the byte path. The
    Windows byte path is untouched, byte for byte.

    Supplementary-plane input (> 0xFFFF, i.e. emoji) is dropped rather than
    split into surrogates: no codepage in this client can carry it, and a lone
    surrogate would corrupt the UTF-16 pivot.

  * CanvasInput.cpp stops dropping AltGr. The char channel filtered out every
    Ctrl/Meta chord (correct for Ctrl+V), but Windows browsers report AltGr as
    ctrlKey AND altKey both true -- and AltGr chords PRODUCE text: € @ [ ] { }
    on a German layout, several marks on Turkish-Q. A real shortcut is Ctrl
    WITHOUT Alt, so the filter keeps catching paste and loses nothing.

WHY THE FIX IS SPLIT LIKE THIS: WMChar's case-8 backspace dispatch also reads
the truncated byte, so U+0108 Ĉ would have run backspace; intercepting at the
top of WMChar, before the switch, closes that too, and leaves the ASCII path
(including the '|' colour-tag doubling) untouched.

Idempotent. M2WASM points at the client tree; a second run reports
`already patched'. THE ENGINE HAS TO BE REBUILT.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")

IME_EDIT = os.path.join(ROOT, "src/EngineLib/src/eter/render/IMEEditBuffer.cpp")
PY_IME = os.path.join(ROOT, "src/PyLib/src/bindings/ime/PythonIME.cpp")
CANVAS = os.path.join(ROOT, "src/EngineLib/src/shared/platform/CanvasInput.cpp")

MARK = "A CODE POINT ABOVE 127 MUST NOT BE TRUNCATED"

# --------------------------------------------------------------------------
# src/EngineLib/src/eter/render/IMEEditBuffer.cpp — CIME::WMChar
# --------------------------------------------------------------------------

WMCHAR_OLD = """LRESULT CIME::WMChar(HWND /*hWnd*/, UINT /*uiMsg*/, WPARAM wParam, LPARAM lParam)
{
\tunsigned char c = static_cast<unsigned char>(wParam & 0xff);
"""

WMCHAR_NEW = """LRESULT CIME::WMChar(HWND /*hWnd*/, UINT /*uiMsg*/, WPARAM wParam, LPARAM lParam)
{
#ifndef _WIN32
\t// ── A CODE POINT ABOVE 127 MUST NOT BE TRUNCATED TO A CODEPAGE BYTE ──────────
\t//
\t// On Windows this function receives WM_CHAR from an ANSI window: wParam IS a
\t// byte of the active codepage, and the byte path below is correct. Off Windows
\t// nothing manufactures codepage bytes — CanvasInput's CodePointForKey and
\t// X11Input's CodepointForKeysym both hand CPythonApplication::OnChar a UNICODE
\t// CODE POINT, and PythonIMEModule injects plain ASCII (0x08/0x0D). Truncating
\t// a code point to its low byte typed the WRONG character for everything the
\t// current codepage does not share with Latin-1: Turkish ş U+015F became '_'
\t// (0x5F), ı U+0131 became '1', İ U+0130 became '0' — while ä U+00E4 only
\t// worked by the accident that CP1252/1254 agree with Latin-1 there (under
\t// CP1251 it would have typed Cyrillic д). The case-8 dispatch below reads the
\t// same truncated byte, so U+0108 Ĉ would have run BACKSPACE; intercepting
\t// before the switch closes that too.
\t//
\t// A value >= 0x80 is therefore inserted as what it already is: one UTF-16
\t// unit in the wide edit buffer (StringCodec's pivot — every codepage
\t// character this client can express is BMP). The conversion to the CURRENT
\t// codepage keeps happening where it always happened, on OUTPUT, through
\t// ImeToNarrow(ms_uOutputCodePage, ...), fed by CheckInputLocale from
\t// GetDefaultCodePage() — 1254 for Turkish, 1252 for German. Nothing is
\t// hardcoded here, and a character the codepage cannot express fails in
\t// ImeToNarrow exactly as it does on Windows.
\t//
\t// The event sink is still consulted first and the capture gate still holds,
\t// in the same order as the byte path below. Supplementary-plane input
\t// (> 0xFFFF) is dropped rather than split into surrogates: no codepage in
\t// this client can carry it, and a lone surrogate would corrupt the pivot.
\tif (wParam >= 0x80)
\t{
\t\tif (ms_pEvent)
\t\t{
\t\t\tif (ms_pEvent->OnWM_CHAR(wParam, lParam))
\t\t\t\treturn 0;
\t\t}
\t\tif (ms_bCaptureInput == false)
\t\t\treturn 0;
\t\tif (wParam <= 0xFFFF && (wParam < 0xD800 || wParam > 0xDFFF))
\t\t{
\t\t\tOnChar(static_cast<wchar_t>(wParam));
\t\t\tif (ms_pEvent)
\t\t\t\tms_pEvent->OnUpdate();
\t\t}
\t\treturn 0;
\t}
#endif

\tunsigned char c = static_cast<unsigned char>(wParam & 0xff);
"""

# --------------------------------------------------------------------------
# src/PyLib/src/bindings/ime/PythonIME.cpp — CPythonIME::OnWM_CHAR
# --------------------------------------------------------------------------

ONWMCHAR_OLD = """bool CPythonIME::OnWM_CHAR( WPARAM wParam, LPARAM lParam )
{
\tunsigned char c = static_cast<unsigned char>(wParam & 0xff);
"""

ONWMCHAR_NEW = """bool CPythonIME::OnWM_CHAR( WPARAM wParam, LPARAM lParam )
{
\t// Only the ASCII controls Return/Tab/Escape are named below. Off Windows
\t// wParam is a full Unicode code point (see CIME::WMChar), and truncating it
\t// FIRST meant any code point whose low byte collided with a control — č
\t// U+010D ends in 0x0D — silently became a Return. A real control never has
\t// the high bits set, on either platform (a Windows ANSI byte >= 0x80 hit no
\t// case and returned false anyway), so this early-out is exact.
\tif (wParam >= 0x80)
\t\treturn false;

\tunsigned char c = static_cast<unsigned char>(wParam & 0xff);
"""

# --------------------------------------------------------------------------
# src/EngineLib/src/shared/platform/CanvasInput.cpp — the AltGr chord
# --------------------------------------------------------------------------

ALTGR_OLD = """    // No char for a Ctrl/Meta chord: `key` is still "v" under Ctrl+V, and pushing it would
    // type a literal letter into the field the paste is aimed at. X11 gets this for free —
    // a control chord's keysym maps to a control CODE, which CodepointForKeysym filters.
    const unsigned cp = (e->ctrlKey || e->metaKey) ? 0 : CodePointForKey(e->key);
"""

ALTGR_NEW = """    // No char for a Ctrl/Meta chord: `key` is still "v" under Ctrl+V, and pushing it would
    // type a literal letter into the field the paste is aimed at. X11 gets this for free —
    // a control chord's keysym maps to a control CODE, which CodepointForKeysym filters.
    //
    // EXCEPT Ctrl+Alt together, which is AltGr: Windows browsers report the AltGr level-3
    // shift as ctrlKey AND altKey both true, and that chord PRODUCES text — € @ [ ] { } on
    // a German layout, several marks on Turkish-Q. Dropping it typed nothing for every
    // AltGr character. A real shortcut is Ctrl WITHOUT Alt (no browser or client binding
    // chords Ctrl+Alt), so the paste filter loses nothing.
    const bool isShortcutChord = (e->ctrlKey && !e->altKey) || e->metaKey;
    const unsigned cp = isShortcutChord ? 0 : CodePointForKey(e->key);
"""

EDITS = [
    (IME_EDIT, [(WMCHAR_OLD, WMCHAR_NEW)]),
    (PY_IME, [(ONWMCHAR_OLD, ONWMCHAR_NEW)]),
    (CANVAS, [(ALTGR_OLD, ALTGR_NEW)]),
]


def read(path):
    return io.open(path, encoding="utf-8", errors="surrogateescape", newline="").read()


def write(path, text):
    io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(text)


def main():
    for path, _ in EDITS:
        if not os.path.isfile(path):
            sys.exit("not found: %s (set M2WASM to the client tree)" % path)

    sources = dict((path, read(path)) for path, _ in EDITS)

    if MARK in sources[IME_EDIT]:
        print("already patched")
        return

    # Every anchor is checked BEFORE anything is written; a half-applied patch
    # is worse than none.
    for path, pairs in EDITS:
        for old, new in pairs:
            n = sources[path].count(old)
            if n != 1:
                sys.exit("anchor not found exactly once in %s (%d matches, "
                         "wanted 1):\n---\n%s\n---\nNothing was changed."
                         % (os.path.relpath(path, ROOT), n, old[:200]))

    for path, pairs in EDITS:
        text = sources[path]
        for old, new in pairs:
            text = text.replace(old, new, 1)
        write(path, text)

    print("patched: non-ASCII characters reach the edit buffer whole")
    print("   CIME::WMChar          inserts a code point >= 0x80 as the UTF-16")
    print("                         unit it is; the codepage conversion stays")
    print("                         on output (ImeToNarrow, current codepage)")
    print("   CPythonIME::OnWM_CHAR no longer mistakes U+xx0D/xx09/xx1B for")
    print("                         Return/Tab/Escape")
    print("   CanvasInput           AltGr (ctrl+alt) chords type their character")
    print("   THE ENGINE HAS TO BE REBUILT.")


if __name__ == "__main__":
    main()
