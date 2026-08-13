#!/usr/bin/env python3
"""Resolve the display name BEFORE building the list entry, in OnLogout.

THE BUG, measured rather than reasoned about. The client aborts with
`null function' the moment a character who has a friend, or a guild with more
than one member, finishes loading. The last four lines before the abort:

    PCF: enter class=0xab689b0 tp_name='MessengerWindow' func='OnLogout'
    PCF: getattr returned 0x4d98040     <- the attribute was found
    PCF: callable ... -- calling now
    PCF: call returned 0x0              <- the call RETURNED, raising

So OnLogout is reached, runs, and raises a Python exception. Everything after
that is the client trying to REPORT the exception, and dying in the attempt --
which is a second fault, and the reason a raised exception is fatal here
instead of merely logged.

This fixes the first fault: the exception itself.

    def OnLogin(self, groupIndex, key, name=None):
        if not name:
            name = key                                   # resolved FIRST
        group = self.groupList[groupIndex]
        member = self.__AddList(groupIndex, key, name)

    def OnLogout(self, groupIndex, key, name=None):
        group = self.groupList[groupIndex]
        member = self.__AddList(groupIndex, key, name)   # name is still None
        if not name:
            name = key                                   # too late

The C++ side only ever passes two arguments -- Py_BuildValue("(is)", group,
key) in CPythonMessenger -- so `name' is ALWAYS None on the way in. OnLogin
copes because it substitutes the key first; OnLogout hands the None straight to
__AddList -> AppendMember -> SetName(None), and that is what raises.

Which is why the two reported symptoms are one bug:

  * a friend who is OFFLINE arrives as OnFriendLogout -> OnLogout
  * every guild member arrives as LogoutGuildMember -> OnLogout

and why an online friend alone would not have shown it. It is also why the
Windows client is unaffected: on Python 2 the same SetName(None) passed
through, and this port runs CPython 3.14, where it does not.

The fix is to make OnLogout do what OnLogin already does, in the same order.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
PY = os.path.join(ROOT, "bin/pack/root/uimessenger.py")

OLD = """	def OnLogout(self, groupIndex, key, name=None):
		group = self.groupList[groupIndex]
		member = self.__AddList(groupIndex, key, name)
		if not name:
			name = key
		member.SetName(name)
"""

NEW = """	def OnLogout(self, groupIndex, key, name=None):
		## Before __AddList, not after: the caller in C++ only ever passes
		## (groupIndex, key), so `name' is always None here, and passing that
		## None on reaches SetName(None), which raises. OnLogin above has
		## always substituted the key first -- this is the same two lines, in
		## the same place.
		if not name:
			name = key
		group = self.groupList[groupIndex]
		member = self.__AddList(groupIndex, key, name)
		member.SetName(name)
"""


def main():
    if not os.path.isfile(PY):
        sys.exit("not found: %s (set M2WASM to the client tree)" % PY)

    s = io.open(PY, encoding="utf-8", errors="surrogateescape").read()
    if "## Before __AddList, not after" in s:
        print("already patched")
        return
    if s.count(OLD) != 1:
        sys.exit("anchor not found exactly once in %s" % PY)

    io.open(PY, "w", encoding="utf-8", errors="surrogateescape", newline="").write(
        s.replace(OLD, NEW, 1))
    print("patched: OnLogout resolves the name before it builds the entry")


if __name__ == "__main__":
    main()
