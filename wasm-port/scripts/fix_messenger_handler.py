#!/usr/bin/env python3
"""Stop the messenger calling into a window that has been torn down.

THE FAULT, as reported: a character in a guild of more than one member, or one
that has added a friend, is unplayable. The screen goes light grey at login and
never finishes loading. Both parties are affected the moment a friendship is
made, and only the browser client is.

THE CHAIN, read out of the sources rather than guessed:

  1. interfacemodule.Close() -> wndMessenger.Destroy()      interfacemodule.py:502
  2. MessengerWindow.Destroy() sets board, scrollBar and    uimessenger.py:400
     resizeButton to None -- but leaves `isLoaded' at 1,
     and does NOT unregister itself from the messenger.
  3. CPythonMessenger::m_poMessengerHandler still points    PythonMessenger.cpp
     at that window.
  4. The next friend or guild member to arrive calls
     OnLogin/OnLogout on it -> OnRefreshList()
     -> __LocateMember(), whose only guard is
     `if self.isLoaded==0: return' -- which is false --
     and the next line is `self.scrollBar.Hide()'          uimessenger.py:448
     on None.

Step 4 is reached ONLY when there is a friend, or a guild member who is not
yourself: an empty friend list and a one-man guild never call the handler at
all. That is exactly the reported trigger, and it is why the bug looks like it
is about guilds when it is about the messenger.

TWO FIXES, AND BOTH ARE NEEDED:

  * uimessenger.py: Destroy() now unregisters the handler and puts `isLoaded'
    back to 0, so the guard that was already written for this case starts
    telling the truth.

  * PythonMessenger.cpp: SetMessengerHandler(None) has to MEAN none. Python's
    None is a real object, not a null pointer, so `if (m_poMessengerHandler)'
    was true for it and every call went on to fail silently inside
    PyObject_GetAttrString. Without this, the Python fix above would have no
    effect whatsoever -- which is worth knowing before anyone tries the one
    without the other.

    The same function also kept a BORROWED reference: PyTuple_GetObject hands
    back what PyTuple_GetItem returned, and that pointer was then held for the
    life of the client. It now takes a reference of its own. A native build
    tends to get away with reading a freed PyObject; WebAssembly traps on it.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")

CPP = os.path.join(ROOT, "src/PyLib/src/bindings/messenger/PythonMessenger.cpp")
PY = os.path.join(ROOT, "bin/pack/root/uimessenger.py")

CPP_OLD = """void CPythonMessenger::SetMessengerHandler(PyObject* poHandler)
{
	m_poMessengerHandler = poHandler;
}"""

CPP_NEW = """void CPythonMessenger::SetMessengerHandler(PyObject* poHandler)
{
	// uimessenger.py says "there is no handler any more" by passing None, and
	// None is an object, not a null pointer -- so every `if (m_poMessengerHandler)'
	// in this file used to be true for it, and every call then failed quietly
	// inside PyObject_GetAttrString. Treating it as nothing here is what makes
	// those guards mean what they were written to mean.
	if (poHandler == Py_None)
		poHandler = nullptr;

	// The caller's reference is BORROWED -- PyTuple_GetObject passes on what
	// PyTuple_GetItem returned -- and this pointer is kept for as long as the
	// client runs. Without a reference of our own the window can be collected
	// while C++ still points at it, and the next friend or guild member to
	// arrive reads a freed PyObject. A native build usually gets away with
	// that; WebAssembly traps.
	//
	// Incremented before the old one is released, so setting the same handler
	// twice cannot free it in between.
	Py_XINCREF(poHandler);
	Py_XDECREF(m_poMessengerHandler);
	m_poMessengerHandler = poHandler;
}"""

PY_OLD = """	def Destroy(self):
		self.board = None
		self.scrollBar = None
		self.resizeButton = None
		self.friendNameBoard = None
		self.questionDialog = None
		self.popupDialog = None
		self.familyGroup = None

		self.whisperButton = None
		self.removeButton = None
"""

PY_NEW = """	def Destroy(self):
		## Stop the messenger calling into this window before taking its
		## widgets away. Without this the C++ side keeps the handler, and the
		## next friend or guild member to arrive walks into the None's below.
		messenger.SetMessengerHandler(None)

		## __LocateMember() guards itself with `if self.isLoaded==0: return',
		## which is exactly the right guard for a window in this state -- it
		## was simply never told. Leaving it at 1 is what turned
		## self.scrollBar into a None with a method call on it.
		self.isLoaded = 0

		self.board = None
		self.scrollBar = None
		self.resizeButton = None
		self.friendNameBoard = None
		self.questionDialog = None
		self.popupDialog = None
		self.familyGroup = None

		self.whisperButton = None
		self.removeButton = None
"""


def patch(path, old, new, marker):
    if not os.path.isfile(path):
        sys.exit("not found: %s (set M2WASM to the client tree)" % path)

    s = io.open(path, encoding="utf-8", errors="surrogateescape").read()
    if marker in s:
        print("already patched: %s" % os.path.basename(path))
        return False
    if s.count(old) != 1:
        sys.exit("anchor not found exactly once in %s" % path)

    io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(
        s.replace(old, new, 1))
    print("patched: %s" % os.path.basename(path))
    return True


def main():
    patch(CPP, CPP_OLD, CPP_NEW, "poHandler == Py_None")
    patch(PY, PY_OLD, PY_NEW, "SetMessengerHandler(None)\n\n\t\t## __LocateMember")


if __name__ == "__main__":
    main()
