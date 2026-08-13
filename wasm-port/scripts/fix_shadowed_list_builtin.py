#!/usr/bin/env python3
"""`list = list(...)' — the line that made friends and guilds unplayable.

THE BUG, read straight off the client's own log after the reporting path was
made survivable:

    PYEXC: OnLogout raised UnboundLocalError:
           cannot access local variable 'list' where it is not associated with a value

    def FindMember(self, key):
        list = list(filter(lambda argMember, argKey=key: ..., self.memberList))
        if list:
            return list[0]
        return None

Binding `list' makes the name local for the WHOLE function, so the call on the
right-hand side reads the local before it exists. The function cannot run, ever.
FindMember is reached from __AddList, which is reached from BOTH OnLogin and
OnLogout -- so every friend and every guild member walked straight into it. That
is the whole bug: no dangling pointer, no reference counting, no WebAssembly.

It is a py2 -> py3 conversion artefact. The original read

    list = filter(lambda ..., self.memberList)

which worked, because in Python 2 filter() returned a list. Wrapping the
right-hand side in list(...) during the port turned a working line into one that
raises on every call.

WHY IT ONLY SHOWED UP IN THE BROWSER: it did not. The Windows client runs the
same broken function -- it simply survives the exception, because there the
reporting path prints a traceback and carries on. In this port that path traps,
so the same exception ends the client. Both are fixed; this file is the trigger,
fix_callback_exception_is_fatal.py is the reason it was fatal.

A syntax-tree sweep of all 81 scripts in root/ found exactly two functions that
bind and call the same builtin. Both are here. Nothing else in the tree has this
shape.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")

# (path, marker that only exists AFTER patching, old text, new text)
#
# The marker is explicit rather than "the first line of the replacement": that
# shortcut reported this very file as already patched, because the first line of
# the replacement is the unchanged `def FindMember(self, key):'.
EDITS = [
    (
        "bin/pack/root/uimessenger.py",
        "## NOT `list = list(...)'",
        """	def FindMember(self, key):
		list = list(filter(lambda argMember, argKey=key: argMember.IsSameKey(argKey), self.memberList))
		if list:
			return list[0]

		return None
""",
        """	def FindMember(self, key):
		## NOT `list = list(...)': assigning to `list' makes the name local to
		## this whole function, and the call on the right then reads it before
		## it exists -- UnboundLocalError on every single call. This function
		## is reached from OnLogin and OnLogout, so it was every friend and
		## every guild member.
		found = list(filter(lambda argMember, argKey=key: argMember.IsSameKey(argKey), self.memberList))
		if found:
			return found[0]

		return None
""",
    ),
    (
        "bin/pack/root/consolemodule.py",
        "names = list(self.functionDict.keys())",
        """				list = list(self.functionDict.keys())
				list.sort()
				Console.ShowNameList(list)
""",
        """				## Same shape as the one in uimessenger.py: binding `list'
				## makes the call on the right unreachable.
				names = list(self.functionDict.keys())
				names.sort()
				Console.ShowNameList(names)
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
            print("already patched: %s" % rel)
            continue
        if s.count(old) != 1:
            sys.exit("anchor not found exactly once in %s" % rel)

        io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(
            s.replace(old, new, 1))
        print("patched: %s" % rel)
        changed += 1

    if changed:
        print("\n%d file(s) changed." % changed)


if __name__ == "__main__":
    main()
