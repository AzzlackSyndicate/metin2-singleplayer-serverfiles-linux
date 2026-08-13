#!/usr/bin/env python3
"""Log the messenger handler pointer at every set and every use.

TEMPORARY. Diagnostic, not a fix.

WHAT IS KNOWN, from a symbolised trace the operator captured:

    __PyCallClassMemberFunc_ByCString(_object*, char const*, _object*, _object**)
    PyCallClassMemberFunc(_object*, char const*, _object*)
    CPythonMessenger::OnFriendLogout(char const*)
    CPythonNetworkStream::GamePhase()

`null function' is a call through a table slot that holds nothing, and the only
indirect calls in that frame go through the object's own type -- tp_getattro in
PyObject_GetAttrString, tp_call in PyObject_CallObject. So m_poMessengerHandler
is not a live PyObject at the moment it is used.

WHAT IS RULED OUT ALREADY, by reading:

  * the singleton is a member of CPythonApplication, so it is constructed long
    before any packet arrives; the pointer starts as nullptr and is not garbage
    from a missing constructor.
  * the interpreter is never finalised between phases (Py_Finalize appears only
    in tests), so the object cannot be lost to a restart.
  * SetMessengerHandler now takes a reference of its own (Py_XINCREF), so plain
    reference counting cannot free it, and the cyclic collector will not touch
    an object with an untracked reference either.

Which leaves: somebody DECREFs it more often than they own it, or it is set to
something that was never a PyObject. Both are answered by printing the pointer,
its type and its reference count at the moment it is stored and at the moment
it is used -- the last line before the abort names the culprit.

Reading Py_REFCNT of a freed object is undefined, but it is a plain load: it
cannot trap, and a nonsense value IS the finding.

Read it with the console filter set to MSGR.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
SRC = os.path.join(ROOT, "src/PyLib/src/bindings/messenger/PythonMessenger.cpp")

PAIRS = [
    # Every use goes through one of these two, and both are on the crash path.
    ("""void CPythonMessenger::OnFriendLogin(const char * c_szKey/*, const char * c_szName*/)
{
	m_FriendNameMap.insert(c_szKey);
""",
     """void CPythonMessenger::OnFriendLogin(const char * c_szKey/*, const char * c_szName*/)
{
	m_FriendNameMap.insert(c_szKey);

	SPDLOG_ERROR("MSGR: OnFriendLogin '{}' handler={} refcnt={} type={}",
	             c_szKey ? c_szKey : "(null)", (void*)m_poMessengerHandler,
	             m_poMessengerHandler ? (long long)Py_REFCNT(m_poMessengerHandler) : -1LL,
	             (void*)(m_poMessengerHandler ? (void*)Py_TYPE(m_poMessengerHandler) : nullptr));
"""),

    ("""void CPythonMessenger::OnFriendLogout(const char * c_szKey)
{
	m_FriendNameMap.insert(c_szKey);
""",
     """void CPythonMessenger::OnFriendLogout(const char * c_szKey)
{
	m_FriendNameMap.insert(c_szKey);

	SPDLOG_ERROR("MSGR: OnFriendLogout '{}' handler={} refcnt={} type={}",
	             c_szKey ? c_szKey : "(null)", (void*)m_poMessengerHandler,
	             m_poMessengerHandler ? (long long)Py_REFCNT(m_poMessengerHandler) : -1LL,
	             (void*)(m_poMessengerHandler ? (void*)Py_TYPE(m_poMessengerHandler) : nullptr));
"""),

    # And the only place it is ever stored.
    ("""	if (poHandler == Py_None)
		poHandler = nullptr;
""",
     """	SPDLOG_ERROR("MSGR: SetMessengerHandler({}) was={} (refcnt in {} / out {})",
	             (void*)poHandler, (void*)m_poMessengerHandler,
	             (poHandler && poHandler != Py_None) ? (long long)Py_REFCNT(poHandler) : -1LL,
	             m_poMessengerHandler ? (long long)Py_REFCNT(m_poMessengerHandler) : -1LL);

	if (poHandler == Py_None)
		poHandler = nullptr;
"""),
]


def main():
    if not os.path.isfile(SRC):
        sys.exit("not found: %s (set M2WASM to the client tree)" % SRC)

    s = io.open(SRC, encoding="utf-8", errors="surrogateescape").read()
    if "MSGR: OnFriendLogout" in s:
        print("already patched")
        return

    if "#include <spdlog/spdlog.h>" not in s:
        # The lesson from BgfxResourceCache.cpp: without this include SPDLOG_*
        # expands to nothing, the build succeeds, and the trace that was meant
        # to settle the question is simply absent.
        s = s.replace('#include "PyTrace.h"',
                      '#include "PyTrace.h"\n#include <spdlog/spdlog.h>', 1)

    for old, new in PAIRS:
        if s.count(old) != 1:
            sys.exit("anchor not found exactly once:\n%s" % old[:70])
        s = s.replace(old, new, 1)

    io.open(SRC, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)
    print("patched: the messenger handler is logged at every set and every use")


if __name__ == "__main__":
    main()
