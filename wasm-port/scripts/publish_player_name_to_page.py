#!/usr/bin/env python3
"""Let the page know which character is playing, so a crash report can say so.

A crash report that names the character can be held against the server's own
logs for the same minute; without it, "somebody crashed" is where the trail
ends. The name is not otherwise reachable from JavaScript -- it lives in the
wasm heap and never crosses the boundary -- so it has to be handed over on
purpose, which is also the only honest way to send it: the dialog can then list
it among what leaves the machine.

CPythonPlayer::SetName is the one place the client learns it, and it is called
on entering the game and on nothing else. So the value published here is exactly
"the character now being played", and it is cleared to an empty string when the
name is.

EM_ASM is compiled out entirely on native builds (the whole body is inside
__EMSCRIPTEN__), so the desktop client is untouched.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
SRC = os.path.join(ROOT, "src/PyLib/src/bindings/player/PythonPlayer.cpp")

OLD = """void CPythonPlayer::SetName(const char *name)
{
	m_stName = name;
}
"""

NEW = """void CPythonPlayer::SetName(const char *name)
{
	m_stName = name;

#ifdef __EMSCRIPTEN__
	// Hand the name to the page, for crash-report.js. This is the only way it
	// can be reported: JavaScript cannot see into the wasm heap, so a report
	// would otherwise say "a character crashed" and stop there. Published
	// deliberately and named in the dialog, rather than scraped.
	EM_ASM({
		globalThis.__m2PlayerName = UTF8ToString($0);
	}, m_stName.c_str());
#endif
}
"""


def main():
    if not os.path.isfile(SRC):
        sys.exit("not found: %s (set M2WASM to the client tree)" % SRC)

    s = io.open(SRC, encoding="utf-8", errors="surrogateescape").read()
    if "__m2PlayerName" in s:
        print("already patched")
        return
    if s.count(OLD) != 1:
        sys.exit("anchor not found exactly once")

    if "#include <emscripten.h>" not in s:
        # Guarded, because this file is compiled for the native clients too.
        s = s.replace("#include <algorithm>",
                      "#include <algorithm>\n#ifdef __EMSCRIPTEN__\n"
                      "#include <emscripten.h>\n#endif", 1)

    io.open(SRC, "w", encoding="utf-8", errors="surrogateescape", newline="").write(
        s.replace(OLD, NEW, 1))
    print("patched: the character name is published to the page on login")


if __name__ == "__main__":
    main()
