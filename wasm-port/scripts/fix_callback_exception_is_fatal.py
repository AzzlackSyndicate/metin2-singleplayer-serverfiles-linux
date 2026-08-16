#!/usr/bin/env python3
"""A Python exception in a callback must be a log line, not the end of the client.

THE SECOND FAULT, and the more serious of the two. Measured:

    PCF: callable, tp_call=0x183d args=0xaa77578 -- calling now
    PCF: call returned 0x0              <- the method raised, normally
    Uncaught RuntimeError: null function

The call itself completes and returns NULL, which is CPython's ordinary way of
saying "an exception is set". What kills the client is what happens NEXT, in
the branch that is supposed to REPORT it:

    if (g_pkExceptionSender) g_pkExceptionSender->Clear();
    PyErr_Print();
    if (g_pkExceptionSender) g_pkExceptionSender->Send();

One of those three goes through a table slot that holds nothing. So any script
error anywhere in the UI -- a typo in a quest window, a None where a string was
expected -- takes the whole client down instead of printing a traceback. That
is a mine under every future change to the Python side, and it is why a
harmless SetName(None) turned into an unplayable character.

WHAT THIS DOES

Reads the exception out with PyErr_Fetch, turns it into text, and logs it. The
old reporting path is left in place but is no longer reached with a live
exception: it cannot fire, so it cannot trap. The client carries on with the
callback having failed, which is what the caller already expects -- every one
of them checks the bool and does nothing more than skip.

It is also the diagnostic that was missing: the log line names the exception
type, its message and the method it came from, so the NEXT script fault is
readable instead of fatal.

PyErr_Fetch/NormalizeException/Str are pure C API calls on objects CPython has
just created; none of them route through the emscripten call trampoline, which
is what the broken path appears to do.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
SRC = os.path.join(ROOT, "src/PyLib/src/launcher/PythonUtils.cpp")

HELPER = '''
// Turn the pending exception into one log line, and clear it.
//
// Everything a callback can raise ends up here. The old path called
// PyErr_Print() and then two virtual methods on a global; one of those traps
// under wasm, which turned every script error into a dead client. This does
// the same job with plain C API calls on objects that were just created.
static void _PyLog_PendingException(const char* where)
{
	if (!PyErr_Occurred())
		return;

	PyObject *type = nullptr, *value = nullptr, *tb = nullptr;
	PyErr_Fetch(&type, &value, &tb);
	PyErr_NormalizeException(&type, &value, &tb);

	const char* type_name = "Exception";
	PyObject* name_obj = nullptr;
	if (type)
	{
		name_obj = PyObject_GetAttrString(type, "__name__");
		if (name_obj)
		{
			const char* n = PyUnicode_AsUTF8(name_obj);
			if (n)
				type_name = n;
		}
	}

	const char* text = "(no message)";
	PyObject* str_obj = nullptr;
	if (value)
	{
		str_obj = PyObject_Str(value);
		if (str_obj)
		{
			const char* t = PyUnicode_AsUTF8(str_obj);
			if (t)
				text = t;
		}
	}

	SPDLOG_ERROR("PYEXC: {} raised {}: {}", where ? where : "(unknown)", type_name, text);

	Py_XDECREF(name_obj);
	Py_XDECREF(str_obj);
	Py_XDECREF(type);
	Py_XDECREF(value);
	Py_XDECREF(tb);
	PyErr_Clear();
}
'''

OLD_BLOCK = """	if (!poRet)
	{
		if (g_pkExceptionSender)
			g_pkExceptionSender->Clear();

		PyErr_Print();

		if (g_pkExceptionSender)
			g_pkExceptionSender->Send();
"""

# PUBLIC LINE: the name for the log comes from PyEval_GetFuncName(poFunc), the
# callable that is in scope at every one of these sites on the clean public base.
# (The private/dev tree restructures these functions to carry a c_szFunc name and
# uses that instead; this line's version is deliberately self-contained so it
# applies to the public base without that private change.)
NEW_BLOCK = """	if (!poRet)
	{
		// Log it and clear it HERE. Below this point the exception is gone, so
		// the reporting path cannot fire -- which is the point: one of its
		// calls traps under wasm and took the client with it.
		_PyLog_PendingException(PyEval_GetFuncName(poFunc));

		if (g_pkExceptionSender)
			g_pkExceptionSender->Clear();

		PyErr_Print();

		if (g_pkExceptionSender)
			g_pkExceptionSender->Send();
"""


def main():
    if not os.path.isfile(SRC):
        sys.exit("not found: %s (set M2WASM to the client tree)" % SRC)

    s = io.open(SRC, encoding="utf-8", errors="surrogateescape").read()
    if "_PyLog_PendingException" in s:
        print("already patched")
        return

    if "#include <spdlog/spdlog.h>" not in s:
        s = s.replace('#include "PythonUtils.h"',
                      '#include "PythonUtils.h"\n#include <spdlog/spdlog.h>', 1)

    # The helper goes in front of the first user of it.
    anchor = "bool __PyCallClassMemberFunc_ByCString(PyObject* poClass"
    idx = s.find(anchor, s.find(anchor) + 1)      # skip the forward declaration
    if idx < 0:
        idx = s.find(anchor)
    if idx < 0:
        sys.exit("could not find the definition to insert before")
    s = s[:idx] + HELPER.lstrip("\n") + "\n" + s[idx:]

    # Every place that reports a failed call gets the same treatment. There are
    # three: ByCString, ByPyString and the pre-resolved variant.
    count = s.count(OLD_BLOCK)
    if count < 1:
        sys.exit("the reporting block was not found in its expected shape")
    s = s.replace(OLD_BLOCK, NEW_BLOCK)

    io.open(SRC, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)
    print("patched: %d reporting site(s) now log the exception instead of dying on it" % count)


if __name__ == "__main__":
    main()
