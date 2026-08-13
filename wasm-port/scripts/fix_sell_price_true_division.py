#!/usr/bin/env python3
"""The sell dialog's "weird decimal numbers" are Python 3 true division.

WHAT THE OPERATOR SEES: vendoring an item to a shop opens the confirmation
dialog with a price like `1234.5678' instead of `1234'.

WHAT IT ACTUALLY IS: the price handed to the dialog is computed as

    itemPrice = itemPrice * itemCount / 5

In Python 2 that `/' between two ints was floor division and the result was an
int. In Python 3 it is true division and the result is a float -- every time,
not only when the division has a remainder, so `5000 / 5' is `1000.0'.

The float then meets localeInfo.NumberToMoneyString, which does NOT format a
number: it does `number = str(number)' and then slices the digits into groups
(CutMoneyString for the Korean/Japanese/Chinese forms, a range()-and-join
comprehension for the western ones). Given "1234.5678" instead of "1234" the
slice offsets land on and after the decimal point, which is why the result reads
as a mangled decimal rather than as a rounded price. Nothing downstream of the
dialog is affected -- OnSellItem sends only the slot and the count, so the server
has always paid the right amount. This is cosmetic, exactly as reported.

THE SAME THREE LINES EXIST IN THREE FILES, because the sell path was copied:
uishop.py (dropping an item onto the open shop window), uiinventory.py
(right-click sell from the inventory while a shop is open) and uidragonsoul.py
(the dragon soul inventory's own sell). All three feed the same dialog, so all
three are patched.

WHAT WAS DELIBERATELY NOT CHANGED: the `itemCount / itemPrice / 5' in the
Is1GoldItem() branch divides the count BY the price, which looks like a typo in
the original for `itemCount * itemPrice'. It is left as it is -- only the
division semantics are restored to what Python 2 gave it. Fixing the arithmetic
would change what the dialog claims an item is worth, which is a decision for
whoever owns the price table, not for a py3 port. The `/' in uioption.py and
uisystemoption.py (`GetSoundVolume() / 5.0') genuinely wants a float and is left
alone, as is every `/' that is already wrapped in int().

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")

# (path, marker that only exists AFTER patching, old text, new text)
EDITS = [
    (
        "bin/pack/root/uishop.py",
        "itemPrice = itemPrice * max(1, attachedCount) // 5",
        """					itemPrice = attachedCount / itemPrice / 5
				else:
					itemPrice = itemPrice * max(1, attachedCount) / 5
""",
        """					itemPrice = attachedCount // itemPrice // 5
				else:
					## `//', not `/': the dialog runs this through
					## localeInfo.NumberToMoneyString, which formats by slicing the
					## digits of str(price) into groups. A float turns "1234" into
					## "1234.5678" and the slices land on the decimal point. Python 2
					## divided two ints into an int here.
					itemPrice = itemPrice * max(1, attachedCount) // 5
""",
    ),
    (
        "bin/pack/root/uiinventory.py",
        "itemPrice = itemPrice * itemCount // 5",
        """				itemPrice = itemCount / itemPrice / 5
			else:
				itemPrice = itemPrice * itemCount / 5
""",
        """				itemPrice = itemCount // itemPrice // 5
			else:
				## `//' for the same reason as in uishop.py: NumberToMoneyString
				## formats by slicing the digits of str(price), so a float prints as
				## a mangled decimal.
				itemPrice = itemPrice * itemCount // 5
""",
    ),
    (
        "bin/pack/root/uidragonsoul.py",
        "itemPrice = itemPrice * itemCount // 5",
        """				itemPrice = itemCount / itemPrice / 5
			else:
				itemPrice = itemPrice * itemCount / 5
""",
        """				itemPrice = itemCount // itemPrice // 5
			else:
				## `//' for the same reason as in uishop.py: NumberToMoneyString
				## formats by slicing the digits of str(price), so a float prints as
				## a mangled decimal.
				itemPrice = itemPrice * itemCount // 5
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
