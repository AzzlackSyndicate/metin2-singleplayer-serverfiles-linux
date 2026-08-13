#!/usr/bin/env python3
"""The horse status bar indexed a list with a float and killed the client.

Reported from a live session, with the client's own two lines:

    PYEXC: BINARY_ServerCommand_Run raised TypeError:
           list indices must be integers or slices, not float
    RuntimeError: null function  ... __PyCallClassMemberFunc_ByCString

    def __GetHorseGrade(self, level):
        if 0 == level:
            return 0
        return (level-1)/10 + 1          # 2.4, not 2

    grade = self.__GetHorseGrade(level)
    self.__AppendText(localeInfo.LEVEL_LIST[grade])   # TypeError

Python 2 returned an integer here and the line worked for twenty years. The
try/except around it catches IndexError -- the failure the original author
expected -- and a TypeError is not one, so the exception left the script
entirely, reached the C++ that called it, and took the process with it.

A syntax-tree sweep of all 81 scripts found no other subscript with a division
inside it, and this is the only function whose return value is a division and is
then used as an index. It is the one instance of this shape in the tree.

`//` is the fix, and it is what the code always meant: a horse's grade is the
level divided into bands of ten.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
SRC = os.path.join(ROOT, "bin/pack/root/uiaffectshower.py")

OLD = """	def __GetHorseGrade(self, level):
		if 0 == level:
			return 0

		return (level-1)/10 + 1
"""

NEW = """	def __GetHorseGrade(self, level):
		if 0 == level:
			return 0

		## Floor division: the result indexes localeInfo.LEVEL_LIST, and in
		## Python 3 a plain `/' makes it a float, which a list refuses with a
		## TypeError -- not the IndexError the caller guards against. The
		## exception then left the script entirely and ended the client.
		return (level-1)//10 + 1
"""


def main():
    if not os.path.isfile(SRC):
        sys.exit("not found: %s (set M2WASM to the client tree)" % SRC)

    s = io.open(SRC, encoding="utf-8", errors="surrogateescape").read()
    if "Floor division: the result indexes" in s:
        print("already patched")
        return
    if s.count(OLD) != 1:
        sys.exit("anchor not found exactly once")

    io.open(SRC, "w", encoding="utf-8", errors="surrogateescape", newline="").write(
        s.replace(OLD, NEW, 1))
    print("patched: the horse grade is an integer again")


if __name__ == "__main__":
    main()
