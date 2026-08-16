#!/usr/bin/env python3
"""Double click equips without pixel-hunting, and never leaves the character
walking forever.

TWO REPORTS, ONE MECHANISM, so one script:

  * "You literally have to click on the exact same pixel twice so that it
    actually equips it."
  * "Sometimes when you click somewhere it gets stuck and infinitely walks.
    It happens when you double click, it behaves like the mouse button is
    being held. Only gets fixed when you click somewhere again."

THE STUCK WALK, first, because it is provable end to end. On Win32 the OS
REPLACES the second button-down of a double click with WM_LBUTTONDBLCLK, and
on X11 this port polls, so X11Input synthesises the double INSIDE the second
press: down(1), up(1), down(2)+DBLCLK, up(2). Both platforms therefore need
CPythonApplication::OnMouseLeftButtonDoubleClick to be preceded by a down —
and on X11 the physical up(2) still follows and clears whatever the down
armed. The DOM is DIFFERENT: `dblclick` fires AFTER the complete second pair —
down(1), up(1), down(2), up(2), dblclick — so the RunMouseLeftButtonDown that
CPythonApplication::OnMouseLeftButtonDoubleClick issued for the other
platforms' benefit was a THIRD press with NO CLEARING PARTNER:

    OnMouseLeftButtonDoubleClick            PythonApplicationEvent.cpp
      -> RunMouseLeftButtonDown             an unpaired press
        -> game.py OnMouseLeftButtonDown
          -> player.SetMouseState(MBT_LEFT, MBS_PRESS)
            -> NEW_SetMouseSmartState       m_isSmtMov = true   (and never false)
              -> NEW_RefreshMouseWalkingDirection, every frame, forever

m_isSmtMov stays latched until the next click's MBS_CLICK — which is exactly
"behaves like the button is held, fixed when you click somewhere again". The
fix is one #ifndef: on Emscripten the browser has already delivered the second
down and up itself, so the app layer must not invent another. Win32 and X11
keep their line untouched.

THE PIXEL-HUNTING. Whether a `dblclick` fires at all is the BROWSER'S click
counter — ~500 ms and a slop of about 4 CSS pixels, the OS default double-
click rectangle, applied at whatever scale the canvas happens to be displayed.
The engine cannot widen that from C++; what it can do is what X11Input already
does on the other non-Windows platform: COUNT ITS OWN double clicks from the
mousedown stream, with a tolerance the game chooses. So CanvasInput now
synthesises LeftDoubleClick on the second left-down within 500 ms and 10 CSS
pixels per axis — two and a half times the browser's box, chosen against the
UI's own notion of "same place" (CWindowManager::IsDragging calls less than 10
px of travel not-a-drag), and small enough that two deliberate clicks on
NEIGHBOURING 32-px inventory slots stay two clicks. The synthetic double is
delivered BETWEEN the second down and its up — the exact order Win32 and X11
deliver it — so the up that follows clears every press-state the double's
handlers arm, and the equip hit-test happens where the second click landed
(CWindowManager::RunMouseLeftButtonDoubleClick re-picks the slot under the
cursor, and slots are far bigger than the slop).

THE DOM dblclick LISTENER IS KEPT, as a fallback, for two real sources:

  * touch-controls.js taps: the touch layer dispatches its own synthetic
    `dblclick` with a deliberately WIDER tolerance (350 ms / 30 CSS px — a
    fingertip is not a mouse). Two taps inside 10 px synthesise here AND
    arrive as the touch layer's dblclick; two taps 10..30 px apart arrive
    ONLY as the touch layer's dblclick. Both must equip exactly once.
  * a user whose OS double-click time is set LONGER than 500 ms: the browser
    honours it, this synthesiser does not know it.

Which creates the double-delivery problem, solved by pairing rather than by
timers: a DOM dblclick always belongs to the click pair of the MOST RECENT
mousedown (the spec fires it with the second click, so no third down can slip
in between). If that down synthesised a double, the DOM event is its echo and
is swallowed; if it did not, the DOM event is delivered. One flag, no clock.

THE BLUR HALF-FIX for the same family: mouse listeners live on the CANVAS, so
a press whose release happens off-canvas (drag out, release; Alt-Tab with the
button held) never sees its mouseup, which is the same latch by another door.
OnBlurDom already synthesises KeyUp for every held key for exactly this
reason; it now releases the left and middle buttons too, at the last known
position. The RIGHT button is deliberately left alone: its release is
entangled with Pointer Lock, and the pointerlockchange handler already
synthesises exactly one RightUp when the lock dies (blur exits the lock);
a second synthesis here would double-release. mouseleave is also deliberately
NOT used: releasing on every canvas-edge graze would drop icon-drags in
windowed embeds, and blur covers the cases that actually strand state.

WHAT WAS RULED OUT: CSlotWindow::OnMouseLeftButtonDoubleClick -> OnUseSlot
re-picks the slot under the cursor with slot-sized tolerance (not pixel-sized),
uiinventory.UseItemSlot equips regardless of the icon-attach churn the extra
browser down/up pair causes, and game.py has no OnMouseLeftButtonDoubleClick
handler at all — so the world side of a double click was never the problem;
the delivery was.

Idempotent. M2WASM points at the client tree; a second run reports
`already patched'. THE ENGINE HAS TO BE REBUILT.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")

CANVAS_H = os.path.join(ROOT, "src/EngineLib/src/shared/platform/CanvasInput.h")
CANVAS_C = os.path.join(ROOT, "src/EngineLib/src/shared/platform/CanvasInput.cpp")
APP_EVENT = os.path.join(ROOT, "src/PyLib/src/bindings/app/PythonApplicationEvent.cpp")

MARK = "M2_DBLCLICK_SYNTH"

# --------------------------------------------------------------------------
# CanvasInput.h — the new state
# --------------------------------------------------------------------------

H_OLD = """    bool m_rightUpSynthesised = false;
    int  m_lockX = 0;
    int  m_lockY = 0;

    bool m_initialised = false;
"""

H_NEW = """    bool m_rightUpSynthesised = false;
    int  m_lockX = 0;
    int  m_lockY = 0;

    // ── M2_DBLCLICK_SYNTH state ─────────────────────────────────────────────
    // The last left-button press, for the engine's own double-click detection
    // (see OnMouseDownDom in the .cpp), and whether that press synthesised a
    // double — a DOM `dblclick` arriving while the flag is set is the echo of
    // the pair that was already served, and is swallowed. The position is kept
    // in CSS pixels deliberately: it is compared against the NEXT event's raw
    // coordinates, before any backing-store scaling.
    long long m_lastLeftDownMs = -1;
    int       m_lastLeftDownCssX = 0;
    int       m_lastLeftDownCssY = 0;
    bool      m_synthDblOnLastLeftDown = false;

    // M2_STUCK_BUTTON_BLUR — which buttons this class believes are held, so
    // blur can synthesise the release the browser will never deliver (the
    // mouse listeners are on the canvas; a release off-canvas is invisible).
    // The right button is NOT tracked here: its release is owned by the
    // Pointer Lock machinery above.
    bool m_leftDownSeen   = false;
    bool m_middleDownSeen = false;

    bool m_initialised = false;
"""

# --------------------------------------------------------------------------
# CanvasInput.cpp — includes
# --------------------------------------------------------------------------

INC_OLD = """#include <spdlog/spdlog.h>

#include <cstring>
"""

INC_NEW = """#include <spdlog/spdlog.h>

#include <chrono>    // steady_clock — the double-click synthesiser's clock
#include <cstdlib>   // std::abs(int) — its slop test
#include <cstring>
"""

# --------------------------------------------------------------------------
# CanvasInput.cpp — tuning constants + clock, next to kWheelDelta
# --------------------------------------------------------------------------

CONST_OLD = """constexpr int kWheelDelta = 120;

} // namespace
"""

CONST_NEW = """constexpr int kWheelDelta = 120;

// ── M2_DBLCLICK_SYNTH tuning ────────────────────────────────────────────────
//
// The engine counts its own double clicks (see OnMouseDownDom) instead of
// depending on the browser's click counter, whose ~4 CSS px box is what made
// equipping "click the exact same pixel twice". 500 ms is the Win32 default
// double-click time and what Chromium hardcodes; 10 CSS px per axis is 2.5x
// the browser's box, the same magnitude the UI itself calls "not a drag"
// (CWindowManager::IsDragging, < 10 px), and well under a 32-px inventory
// slot, so two deliberate clicks on neighbouring slots stay two clicks.
// CSS pixels, not backing-store: compared before any scaling, so a resolution
// change does not change what a double click feels like.
constexpr long long kDoubleClickMs     = 500;
constexpr int       kDoubleClickSlopPx = 10;

// steady_clock as in X11Input's synthesiser: monotonic, immune to wall-clock
// jumps, and the only consumer subtracts two of these.
long long NowMs()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

} // namespace
"""

# --------------------------------------------------------------------------
# CanvasInput.cpp — CanvasInputAccess accessors
# --------------------------------------------------------------------------

ACC_OLD = """    static bool& PointerLocked(CanvasInput* self) { return self->m_pointerLocked; }
    static bool& LockWanted(CanvasInput* self)    { return self->m_lockWanted; }
    static bool& RightUpSynthesised(CanvasInput* self) { return self->m_rightUpSynthesised; }
"""

ACC_NEW = """    static bool& PointerLocked(CanvasInput* self) { return self->m_pointerLocked; }
    static bool& LockWanted(CanvasInput* self)    { return self->m_lockWanted; }
    static bool& RightUpSynthesised(CanvasInput* self) { return self->m_rightUpSynthesised; }

    // M2_DBLCLICK_SYNTH + M2_STUCK_BUTTON_BLUR state, same access pattern.
    static long long& LastLeftDownMs(CanvasInput* self)   { return self->m_lastLeftDownMs; }
    static int&  LastLeftDownCssX(CanvasInput* self)      { return self->m_lastLeftDownCssX; }
    static int&  LastLeftDownCssY(CanvasInput* self)      { return self->m_lastLeftDownCssY; }
    static bool& SynthDblOnLastLeftDown(CanvasInput* self) { return self->m_synthDblOnLastLeftDown; }
    static bool& LeftDownSeen(CanvasInput* self)          { return self->m_leftDownSeen; }
    static bool& MiddleDownSeen(CanvasInput* self)        { return self->m_middleDownSeen; }
"""

# --------------------------------------------------------------------------
# CanvasInput.cpp — OnMouseDownDom: the synthesiser
# --------------------------------------------------------------------------

DOWN_OLD = """    switch (e->button)
    {
    case kButtonLeft:
        CanvasInputAccess::Push(self, CanvasEventKind::LeftDown, x, y);
        break;
    case kButtonMiddle:
        CanvasInputAccess::Push(self, CanvasEventKind::MiddleDown, x, y);
        break;
"""

DOWN_NEW = """    switch (e->button)
    {
    case kButtonLeft:
    {
        // ── M2_DBLCLICK_SYNTH: THE ENGINE COUNTS ITS OWN DOUBLE CLICKS ──────
        //
        // The browser's `dblclick` needs the two clicks inside the OS double-
        // click rectangle — ~4 CSS px — which on this game's slot-sized targets
        // reads as "click the exact same pixel twice". X11Input already counts
        // its own doubles for the same reason (no dblclick concept there); this
        // is the same synthesiser with the DOM's event stream. Tolerances are
        // kCodeMap-style constants above.
        //
        // Delivered BETWEEN this press and its coming release — the order
        // Win32 (down, up, DBLCLK, up) and X11Input deliver — so the release
        // that follows clears every press-state the double's handlers arm.
        // The DOM's own late `dblclick` (it fires after the second UP) is
        // recognised as this pair's echo through SynthDblOnLastLeftDown and
        // swallowed in OnDblClickDom; see there for why the listener stays.
        //
        // Compared in CSS pixels (raw targetX/Y), before backing-store
        // scaling, so display scale does not change the feel. Under Pointer
        // Lock targetX freezes, but a left double click during a right-drag
        // rotation is not a gesture this client assigns meaning to.
        const long long nowMs = NowMs();
        const bool isDouble =
            CanvasInputAccess::LastLeftDownMs(self) >= 0 &&
            nowMs - CanvasInputAccess::LastLeftDownMs(self) <= kDoubleClickMs &&
            std::abs(e->targetX - CanvasInputAccess::LastLeftDownCssX(self)) <= kDoubleClickSlopPx &&
            std::abs(e->targetY - CanvasInputAccess::LastLeftDownCssY(self)) <= kDoubleClickSlopPx;

        CanvasInputAccess::LeftDownSeen(self) = true;
        CanvasInputAccess::Push(self, CanvasEventKind::LeftDown, x, y);

        if (isDouble)
        {
            CanvasInputAccess::Push(self, CanvasEventKind::LeftDoubleClick, x, y);
            CanvasInputAccess::SynthDblOnLastLeftDown(self) = true;
            // Armed for a fresh pair: a triple click is down + double + down,
            // not two doubles — the same reset Win32 and X11Input make.
            CanvasInputAccess::LastLeftDownMs(self) = -1;
        }
        else
        {
            CanvasInputAccess::SynthDblOnLastLeftDown(self) = false;
            CanvasInputAccess::LastLeftDownMs(self)   = nowMs;
            CanvasInputAccess::LastLeftDownCssX(self) = e->targetX;
            CanvasInputAccess::LastLeftDownCssY(self) = e->targetY;
        }
        break;
    }
    case kButtonMiddle:
        CanvasInputAccess::MiddleDownSeen(self) = true;
        CanvasInputAccess::Push(self, CanvasEventKind::MiddleDown, x, y);
        break;
"""

# --------------------------------------------------------------------------
# CanvasInput.cpp — OnMouseUpDom: the releases retire the held flags
# --------------------------------------------------------------------------

UP_OLD = """    switch (e->button)
    {
    case kButtonLeft:
        CanvasInputAccess::Push(self, CanvasEventKind::LeftUp, x, y);
        break;
    case kButtonMiddle:
        CanvasInputAccess::Push(self, CanvasEventKind::MiddleUp, x, y);
        break;
"""

UP_NEW = """    switch (e->button)
    {
    case kButtonLeft:
        CanvasInputAccess::LeftDownSeen(self) = false;
        CanvasInputAccess::Push(self, CanvasEventKind::LeftUp, x, y);
        break;
    case kButtonMiddle:
        CanvasInputAccess::MiddleDownSeen(self) = false;
        CanvasInputAccess::Push(self, CanvasEventKind::MiddleUp, x, y);
        break;
"""

# --------------------------------------------------------------------------
# CanvasInput.cpp — OnDblClickDom: fallback + echo swallow
# --------------------------------------------------------------------------

DBL_OLD = """// Real, unlike on X11 — the browser synthesises this from the USER'S OWN system
// double-click interval, the same source Win32's WM_LBUTTONDBLCLK uses. See CanvasInput.h.
EM_BOOL OnDblClickDom(int /*eventType*/, const EmscriptenMouseEvent* e, void* userData)
{
    auto* self = Self(userData);

    if (e->button != kButtonLeft)
        return EM_FALSE;

    int x = 0, y = 0;
    ScaleToBackingStore(CanvasWindow::CanvasSelector(), e->targetX, e->targetY, x, y);
    CanvasInputAccess::Push(self, CanvasEventKind::LeftDoubleClick, x, y);
    return EM_TRUE;
}
"""

DBL_NEW = """// The FALLBACK double-click source since M2_DBLCLICK_SYNTH counts its own in
// OnMouseDownDom. Still needed for two real senders this class cannot see from
// the mousedown stream alone:
//
//   * touch-controls.js dispatches a synthetic `dblclick` for two taps within
//     ITS tolerance (350 ms / 30 CSS px — a fingertip is not a mouse). Taps
//     10..30 px apart never trip the synthesiser and arrive only here.
//   * a user whose OS double-click time is set longer than kDoubleClickMs:
//     the browser honours the OS, this class cannot know it.
//
// M2_DBLCLICK_SYNTH_ECHO: a DOM dblclick always belongs to the click pair of
// the MOST RECENT mousedown — the spec fires it with the second click, so no
// third down can precede it. If that down already synthesised the double, this
// event is the same gesture arriving twice, and delivering both would run
// OnUseSlot twice (equip, then use whatever slid into the slot). Pairing by
// flag rather than by timer: it cannot go stale, because the very next
// left-down rewrites it either way.
EM_BOOL OnDblClickDom(int /*eventType*/, const EmscriptenMouseEvent* e, void* userData)
{
    auto* self = Self(userData);

    if (e->button != kButtonLeft)
        return EM_FALSE;

    if (CanvasInputAccess::SynthDblOnLastLeftDown(self))
    {
        CanvasInputAccess::SynthDblOnLastLeftDown(self) = false;
        return EM_TRUE;
    }

    int x = 0, y = 0;
    ScaleToBackingStore(CanvasWindow::CanvasSelector(), e->targetX, e->targetY, x, y);
    CanvasInputAccess::Push(self, CanvasEventKind::LeftDoubleClick, x, y);
    return EM_TRUE;
}
"""

# --------------------------------------------------------------------------
# CanvasInput.cpp — OnBlurDom: release held mouse buttons too
# --------------------------------------------------------------------------

BLUR_OLD = """    for (int dik = 0; dik < 256; ++dik)
    {
        if (CanvasInputAccess::Pressed(self, static_cast<std::uint8_t>(dik)))
        {
            CanvasInputAccess::Pressed(self, static_cast<std::uint8_t>(dik)) = false;
            CanvasInputAccess::Push(self, CanvasEventKind::KeyUp, dik, 0);
        }
    }

    return EM_FALSE;
}
"""

BLUR_NEW = """    for (int dik = 0; dik < 256; ++dik)
    {
        if (CanvasInputAccess::Pressed(self, static_cast<std::uint8_t>(dik)))
        {
            CanvasInputAccess::Pressed(self, static_cast<std::uint8_t>(dik)) = false;
            CanvasInputAccess::Push(self, CanvasEventKind::KeyUp, dik, 0);
        }
    }

    // ── M2_STUCK_BUTTON_BLUR: THE SAME SYNTHESIS FOR THE MOUSE ──────────────
    //
    // The mouse listeners are on the CANVAS, so a release that happens
    // off-canvas — drag out and let go, or Alt-Tab with the button held — is
    // never delivered, and the client keeps MBS_PRESS latched: the character
    // walks toward the pointer until the next click, exactly the keyboard's
    // walking-into-a-wall failure this handler already fixes for keys. The
    // release is synthesised at the last known position, which is where the
    // press-state believes the pointer is.
    //
    // The RIGHT button is deliberately left out: blur exits Pointer Lock, and
    // the pointerlockchange handler already synthesises exactly one RightUp
    // for that (M2_SYNTHETIC_RIGHTUP_SET); a second one here would run the
    // release path twice and open the character menu on the false "click".
    if (CanvasInputAccess::LeftDownSeen(self))
    {
        CanvasInputAccess::LeftDownSeen(self) = false;
        CanvasInputAccess::Push(self, CanvasEventKind::LeftUp,
                                CanvasInputAccess::MouseX(self),
                                CanvasInputAccess::MouseY(self));
    }
    if (CanvasInputAccess::MiddleDownSeen(self))
    {
        CanvasInputAccess::MiddleDownSeen(self) = false;
        CanvasInputAccess::Push(self, CanvasEventKind::MiddleUp,
                                CanvasInputAccess::MouseX(self),
                                CanvasInputAccess::MouseY(self));
    }

    return EM_FALSE;
}
"""

# --------------------------------------------------------------------------
# CanvasInput.cpp — Shutdown resets the new state with the old
# --------------------------------------------------------------------------

SHUT_OLD = """    m_pointerLocked      = false;   // M2_SYNTHETIC_RIGHTUP_RESET
    m_lockWanted         = false;
    m_rightUpSynthesised = false;
"""

SHUT_NEW = """    m_pointerLocked      = false;   // M2_SYNTHETIC_RIGHTUP_RESET
    m_lockWanted         = false;
    m_rightUpSynthesised = false;
    m_lastLeftDownMs          = -1;     // M2_DBLCLICK_SYNTH reset
    m_synthDblOnLastLeftDown  = false;
    m_leftDownSeen            = false;  // M2_STUCK_BUTTON_BLUR reset
    m_middleDownSeen          = false;
"""

# --------------------------------------------------------------------------
# PythonApplicationEvent.cpp — the unpaired press
# --------------------------------------------------------------------------

APP_OLD = """void CPythonApplication::OnMouseLeftButtonDoubleClick(int x, int y)
{
\tUI::CWindowManager& rkWndMgr=UI::CWindowManager::Instance();
\trkWndMgr.RunMouseLeftButtonDown(x, y);
\trkWndMgr.RunMouseLeftButtonDoubleClick(x, y);
}
"""

APP_NEW = """void CPythonApplication::OnMouseLeftButtonDoubleClick(int x, int y)
{
\tUI::CWindowManager& rkWndMgr=UI::CWindowManager::Instance();

\t// The synthesised down exists for the platforms whose event stream REPLACES
\t// the second physical down with the double: Win32's WM_LBUTTONDBLCLK, and
\t// X11Input's polling synthesiser — on both, the physical up(2) still follows
\t// and clears whatever this press arms. The browser is different: its second
\t// down and up are BOTH delivered before the double (CanvasInput pushes the
\t// double between them; the DOM's own dblclick even fires after the up), so
\t// this down was a THIRD press with no clearing partner. game.py's
\t// OnMouseLeftButtonDown ran player.SetMouseState(MBT_LEFT, MBS_PRESS),
\t// m_isSmtMov latched true, and NEW_RefreshMouseWalkingDirection walked the
\t// character toward the pointer forever — "it behaves like the mouse button
\t// is being held. Only gets fixed when you click somewhere again."
#ifndef __EMSCRIPTEN__
\trkWndMgr.RunMouseLeftButtonDown(x, y);
#endif
\trkWndMgr.RunMouseLeftButtonDoubleClick(x, y);
}
"""

EDITS = [
    (CANVAS_H, [(H_OLD, H_NEW)]),
    (CANVAS_C, [(INC_OLD, INC_NEW),
                (CONST_OLD, CONST_NEW),
                (ACC_OLD, ACC_NEW),
                (DOWN_OLD, DOWN_NEW),
                (UP_OLD, UP_NEW),
                (DBL_OLD, DBL_NEW),
                (BLUR_OLD, BLUR_NEW),
                (SHUT_OLD, SHUT_NEW)]),
    (APP_EVENT, [(APP_OLD, APP_NEW)]),
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

    if MARK in sources[CANVAS_H]:
        print("already patched")
        return

    # Every anchor checked BEFORE anything is written: a half-applied patch
    # across three files does not link.
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

    print("patched: double click is counted by the engine, and can no longer")
    print("         leave the character walking forever")
    print("   synth:  CanvasInput synthesises LeftDoubleClick on the second")
    print("           left-down within 500 ms / 10 CSS px — the browser's ~4 px")
    print("           box is what demanded the exact same pixel twice")
    print("   echo:   the DOM dblclick stays as fallback (touch layer's wider")
    print("           taps, long OS double-click times) and the synthesised")
    print("           pair's own echo is swallowed by flag, not by timer")
    print("   walk:   OnMouseLeftButtonDoubleClick no longer synthesises an")
    print("           unpaired RunMouseLeftButtonDown on Emscripten — that was")
    print("           the MBS_PRESS latch behind the infinite walk")
    print("   blur:   held left/middle buttons are released on blur, as held")
    print("           keys already were; the right button stays with the")
    print("           Pointer Lock machinery that already releases it")
    print("   THE ENGINE HAS TO BE REBUILT.")


if __name__ == "__main__":
    main()
