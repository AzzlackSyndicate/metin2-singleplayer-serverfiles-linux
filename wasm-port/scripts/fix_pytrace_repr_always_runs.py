#!/usr/bin/env python3
"""Stop building a Python repr of every binding argument on a discarded log line.

MEASURED, not guessed:

    CMakeLists.txt:405   add_compile_definitions(SPDLOG_ACTIVE_LEVEL=SPDLOG_LEVEL_TRACE)
                         -- unconditional, every configuration, all 904 wasm
                         translation units
    src/PyTrace.h        PyObject_Repr(poArgs) runs BEFORE any level check
    1160                 call sites of PyTrace() across src/**.cpp

Every call from Python into C++ passes through one of those 1160 entry points,
including trivial getters like wndMgrGetScreenWidth. Each one allocates a str,
reprs every element of the argument tuple, joins them, encodes to UTF-8, hands
the result to a log macro that throws it away -- because SPDLOG_TRACE is gated
at RUN time and Release runs at `debug' -- and then frees it.

SPDLOG_ACTIVE_LEVEL only decides whether the macro is COMPILED IN. It has never
decided whether the repr happens. The repr was never inside the macro.

The fix is to ask the logger the question the macro would ask, before doing any
work. When trace logging is on the output is byte-identical; when it is off,
which is always in a shipped client, the whole function is a predicted branch.

WHAT THIS IS NOT. It is not a measured speedup: nobody has counted binding calls
per frame in a browser on real hardware, and this tree's only frame numbers come
from a software rasteriser in a container. It is the removal of work that is
provably pointless -- which is worth doing on its own terms, and is cheap enough
that waiting for the measurement would cost more than the change.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
SRC = os.path.join(ROOT, "src/PyTrace.h")

OLD = """inline void PyTrace(const char* funcName, PyObject* poArgs)
{
    if (!poArgs)
    {
        SPDLOG_TRACE("[Py] -> {}", funcName);
        return;
    }
"""

NEW = """inline void PyTrace(const char* funcName, PyObject* poArgs)
{
    // Ask the logger FIRST. SPDLOG_ACTIVE_LEVEL decides whether the macro below
    // is compiled in; it says nothing about the PyObject_Repr further down,
    // which sits outside the macro and therefore ran on every one of the 1160
    // binding entry points -- hundreds of times a frame -- to build a string
    // that a `debug'-level logger immediately discarded.
    //
    // With tracing on, the output is unchanged. With it off, which is every
    // shipped client, this whole function is one predicted branch.
    if (!spdlog::should_log(spdlog::level::trace))
        return;

    if (!poArgs)
    {
        SPDLOG_TRACE("[Py] -> {}", funcName);
        return;
    }
"""


def main():
    if not os.path.isfile(SRC):
        sys.exit("not found: %s (set M2WASM to the client tree)" % SRC)

    s = io.open(SRC, encoding="utf-8", errors="surrogateescape").read()
    if "Ask the logger FIRST" in s:
        print("already patched")
        return
    if s.count(OLD) != 1:
        sys.exit("anchor not found exactly once")
    if "spdlog" not in s:
        sys.exit("PyTrace.h does not include spdlog -- check the header before patching")

    io.open(SRC, "w", encoding="utf-8", errors="surrogateescape", newline="").write(
        s.replace(OLD, NEW, 1))
    print("patched: PyTrace returns before it reprs anything")


if __name__ == "__main__":
    main()
