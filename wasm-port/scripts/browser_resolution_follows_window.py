#!/usr/bin/env python3
"""The browser client rendered 1024x768 into a canvas that fills the tab.

WHAT WAS ACTUALLY WRONG, established by reading the boot path end to end rather
than by looking for a resize handler that was missing -- it was not missing:

    tools/wasm/shell.html          canvas#canvas { width:100%; height:100% }
    CanvasWindow                   m_autoSize, a DOM resize listener, a debounce,
                                   and SetSize(0,0) meaning "follow the element"
    CPythonApplication::Prepare    reads a stored 0x0 as "ask the window how big
                                   it is" and creates the device at that size
    ChangeDisplayMode              0x0 is AUTO, persisted like any other mode
    uisystemoption.py              offers "Auto (window)" when app.PLATFORM=="web"
    game.py / interfacemodule.py   reflow the HUD when wndMgr's screen size moves

Every one of those was already in place. The ONE thing that never produced a 0x0
was CPythonSystem::SetDefaultConfig, which set 1024x768 on every platform -- so
the browser booted into the pinned-resolution branch and stayed there, and the
browser then stretched those 1024x768 pixels across the whole window. That is
the entire bug: a default, not a missing feature.

The three edits here are therefore small on purpose:

  1. SetDefaultConfig defaults to 0x0 under __EMSCRIPTEN__ -- AUTO. The desktop
     clients keep 1024x768; nothing outside the guard changes.

  2. A one-time migration. Browser settings live in localStorage (LoadConfig
     rehydrates metin2.cfg from UserStore), so a tab that ran this client before
     carries a stored WIDTH 1024 / HEIGHT 768 that no player ever chose, and a
     changed default would never reach it. SaveConfig now stamps the file with
     AUTO_RESOLUTION_KNOWN; LoadConfig reads a file without that stamp as
     pre-Auto and resets it to 0x0 once. A size picked AFTER this survives,
     which is why the migration keys off an explicit marker instead of guessing
     that a stored 1024x768 means "never chosen" -- that guess would have made
     that one entry in the resolution list unpickable forever.

  3. CanvasWindow::Create adopts the element's CSS size instead of the 1024x768
     literal main() passes on every platform. Without this the client is right
     one frame later anyway (AdjustSize(0,0) queues the real size and the first
     PumpMessages applies it), at the cost of creating the device at 1024x768 and
     tearing the swap chain down again immediately. The caller's numbers stay as
     the fallback for an element with no layout.

DEVICE PIXEL RATIO: one backing pixel per CSS pixel, devicePixelRatio ignored.
The long-form reason is a comment in CanvasWindow.cpp; the short form is that
CWindowManager's screen size IS the backing-store size and the interface scripts
lay themselves out against it in fixed pixel constants over fixed-size atlases,
so rendering at DPR 2 would not sharpen the HUD, it would halve it -- on top of
costing four times the fill rate in a WebGL context.

All three edits are C++, so this needs a relink of the wasm client. No data-only
path exists for it: the numbers live in the config defaults, not in the scripts.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")

# (path, marker that only exists AFTER patching, old text, new text)
EDITS = [
    # ── 1. the default that made the browser pin a desktop resolution ───────────
    (
        "src/PyLib/src/bindings/system/PythonSystem.cpp",
        "THE BROWSER HAS NO DISPLAY MODE TO INHERIT",
        """	m_Config.width				= 1024;
	m_Config.height				= 768;
	m_Config.bpp				= 32;
""",
        """#ifdef __EMSCRIPTEN__
	// >>> THE BROWSER HAS NO DISPLAY MODE TO INHERIT, SO IT BOOTS IN AUTO. <<<
	//
	// 0x0 is how AUTO is spelled through this config, and every consumer downstream
	// already understands it: CPythonApplication::Prepare reads a stored 0x0 as "ask the
	// window how big it is", AdjustSize(0,0) reaches CanvasWindow::SetSize's auto arm,
	// and the options dialog shows the pair as "Auto (window)". Only the DEFAULT was
	// still the desktop client's, and a page cannot honour that number honestly: the
	// canvas is styled to fill the viewport, so a 1024x768 backing store is not a
	// resolution anybody chose -- it is 1024x768 pixels stretched by the browser across
	// whatever size the tab actually is, soft on any window larger than that and
	// distorted on any window with a different shape.
	//
	// A player who wants cheaper pixels can still pin a size in the options dialog and it
	// persists as a real width and height, which is why this is a default and not a
	// hard-wire.
	m_Config.width				= 0;
	m_Config.height				= 0;
#else
	m_Config.width				= 1024;
	m_Config.height				= 768;
#endif
	m_Config.bpp				= 32;
""",
    ),
    # ── 2a. the flag the migration below reads ──────────────────────────────────
    (
        "src/PyLib/src/bindings/system/PythonSystem.cpp",
        "bool bAutoResolutionKnown = false;",
        """	char buf[256];
	char command[256];
	char value[256];
""",
        """	char buf[256];
	char command[256];
	char value[256];

#ifdef __EMSCRIPTEN__
	// Set by the AUTO_RESOLUTION_KNOWN line SaveConfig writes; its absence means this file
	// was written before the browser build had an Auto resolution at all, and the size in
	// it is the old default rather than a choice. Read at the end of the parse loop.
	bool bAutoResolutionKnown = false;
#endif
""",
    ),
    # ── 2b. read the marker, and migrate the tabs that predate it ───────────────
    (
        "src/PyLib/src/bindings/system/PythonSystem.cpp",
        "THE ONE-TIME MIGRATION OFF THE OLD FIXED SIZE",
        """		else if (!stricmp(command, "SHOW_SALESTEXT"))
			m_Config.bShowSalesText = atoi(value) == 1 ? true : false;
	}
""",
        """		else if (!stricmp(command, "SHOW_SALESTEXT"))
			m_Config.bShowSalesText = atoi(value) == 1 ? true : false;
#ifdef __EMSCRIPTEN__
		else if (!stricmp(command, "AUTO_RESOLUTION_KNOWN"))
			bAutoResolutionKnown = atoi(value) == 1 ? true : false;
#endif
	}

#ifdef __EMSCRIPTEN__
	// -- THE ONE-TIME MIGRATION OFF THE OLD FIXED SIZE --------------------------------
	//
	// A browser that ran this client before Auto existed has a metin2.cfg in localStorage
	// carrying WIDTH 1024 / HEIGHT 768 -- written by SaveConfig from the old default, not
	// picked by anybody -- and that file outlives the code that wrote it. Without this,
	// the new default would only ever reach a browser with no stored settings at all,
	// which is nobody who has played here before.
	//
	// It keys off an explicit marker rather than the value: reading a stored 1024x768 as
	// "never chosen" would have made that one entry in the resolution list unpickable
	// forever. Everything SaveConfig writes from now on carries the marker, so a size the
	// player pins after this survives every later boot.
	if (!bAutoResolutionKnown)
	{
		m_Config.width  = 0;
		m_Config.height = 0;
	}
#endif
""",
    ),
    # ── 2c. and stamp every file written from now on ────────────────────────────
    (
        # NOT the key name: edit 2b writes that string into the same file, so a marker
        # of "AUTO_RESOLUTION_KNOWN" would report this edit as already applied and the
        # marker would then be true of a file nothing ever stamps.
        "src/PyLib/src/bindings/system/PythonSystem.cpp",
        "Stamps the file as written by a client that HAS an Auto resolution",
        """	fprintf(fp.get(), "SHADOW_LEVEL			%d\\n", m_Config.iShadowLevel);
	fprintf(fp.get(), "\\n");
""",
        """	fprintf(fp.get(), "SHADOW_LEVEL			%d\\n", m_Config.iShadowLevel);
#ifdef __EMSCRIPTEN__
	// Stamps the file as written by a client that HAS an Auto resolution, which is how
	// LoadConfig tells a size the player pinned from one the old default left behind. Only
	// the browser needs it -- nothing migrates the desktop clients, whose configured mode
	// is exactly what they should keep -- and their parser ignores an unknown key anyway.
	fprintf(fp.get(), "AUTO_RESOLUTION_KNOWN	%d\\n", 1);
#endif
	fprintf(fp.get(), "\\n");
""",
    ),
    # ── 3. the boot size, and the device-pixel-ratio decision ───────────────────
    (
        "src/EngineLib/src/shared/platform/CanvasWindow.cpp",
        "ONE BACKING PIXEL PER CSS PIXEL",
        """// The client's fixed logical resolution, and what Create() is asked for. See OnDomResize:
// the CSS size of the element is the page's business, but the BACKING STORE is the
// framebuffer bgfx renders into, and that is what these track.
constexpr int kResizeSettleMs = 120;
""",
        """// ══════════════════════════════════════════════════════════════════════════════════
// ONE BACKING PIXEL PER CSS PIXEL — devicePixelRatio IS IGNORED, AND DELIBERATELY
// ══════════════════════════════════════════════════════════════════════════════════
//
// Every place in this file that turns the element's CSS size into a backing-store size
// — Create, the resize thunk, SetSize's auto arm — uses that number unmultiplied. On a
// HiDPI display the browser then upscales the frame, and the alternative is one
// multiplication away, so the omission is a decision and this is it.
//
// THE INTERFACE IS LAID OUT IN BACKING PIXELS. OnResize routes this size into
// CWindowManager as the screen size, and the ui scripts position themselves against that
// number in fixed constants — a taskbar 37 pixels tall, a chat window 600 wide — over
// atlases authored at exactly those sizes. Rendering at devicePixelRatio 2 would not draw
// the same interface more sharply, it would draw it HALF AS LARGE: every dialog, every
// glyph and every button shrinks to the physical size of the denser pixel grid, and the
// fixed-size art has no higher-resolution variant to gain anything back from.
//
// The other half of the argument is cost: at ratio 2 the same frame is FOUR TIMES the
// pixels, through a WebGL context, in wasm, for a renderer that is already fill-rate
// bound. Both point the same way, so there is no ratio to tune and no switch to expose —
// a player who wants a cheaper frame pins a resolution in the options dialog, which is
// what SetSize's non-auto arm exists for.

// How long the viewport must hold still before a pending resize is applied. See
// CanvasWindow.h for the drag that made a debounce necessary.
constexpr int kResizeSettleMs = 120;
""",
    ),
    (
        "src/EngineLib/src/shared/platform/CanvasWindow.cpp",
        "THE SIZE THIS WINDOW IS BORN WITH IS THE ELEMENT'S OWN",
        """    // emscripten_set_canvas_element_size sets exactly that one, and it is what the
    // width/height the client asked for must become — otherwise the client renders at
    // 1024x768 into whatever the stylesheet happened to imply and the result is resampled.
    const EMSCRIPTEN_RESULT rc = emscripten_set_canvas_element_size(kCanvas, w, h);
""",
        """    // emscripten_set_canvas_element_size sets exactly that one, and it is the size the
    // renderer will work at — whatever the stylesheet does to the element afterwards is
    // resampling, not resolution.
    //
    // >>> AND THE SIZE THIS WINDOW IS BORN WITH IS THE ELEMENT'S OWN, NOT THE CALLER'S. <<<
    //
    // m_autoSize starts true, so the requested w/h describe a mode this window is not in.
    // main() passes the same 1024x768 literal on every platform, and on a page that number
    // is not a display mode anyone chose — the stylesheet already decided how large the
    // canvas is, and the browser is about to stretch the frame to it either way. Adopting
    // the CSS size here is what auto mode means, one frame earlier than the first
    // PumpMessages would deliver it, and it saves creating the device at 1024x768 and
    // tearing the swap chain down again on the very next frame.
    //
    // The caller's numbers stay as the fallback for an element that has no layout yet —
    // display:none, or a shell that inserts the canvas after main() runs — rather than a
    // zero-sized backing store, which is not a framebuffer at all.
    double cssW = 0.0;
    double cssH = 0.0;
    if (emscripten_get_element_css_size(kCanvas, &cssW, &cssH) == EMSCRIPTEN_RESULT_SUCCESS
        && cssW >= 1.0 && cssH >= 1.0)
    {
        w = static_cast<int>(cssW);
        h = static_cast<int>(cssH);
    }

    const EMSCRIPTEN_RESULT rc = emscripten_set_canvas_element_size(kCanvas, w, h);
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
            print("already patched: %s (%s)" % (rel, marker[:40]))
            continue
        if s.count(old) != 1:
            sys.exit("anchor not found exactly once in %s (%s)" % (rel, marker[:40]))

        io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(
            s.replace(old, new, 1))
        print("patched: %s (%s)" % (rel, marker[:40]))
        changed += 1

    if changed:
        print("\n%d edit(s) applied. C++ only — the wasm client must be relinked." % changed)


if __name__ == "__main__":
    main()
