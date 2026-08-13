#!/usr/bin/env python3
"""Calling your horse always works.

Using a Horse Medal rolls against a skill-dependent chance:

    function get_horse_summon_prob_pct()
        local skill_level=pc.get_skill_level(131)
        if skill_level==1 then return 15
        ... 20, 30, 40, 50, 60, 70, 80, 90 ...
        elseif skill_level>=10 then return 100 end
        return 10                                  -- no skill at all

    when 50051.use with horse.get_grade()==1 begin
        if pc.getsp()>=100 then
            if number(1, 100)<=horse_summon.get_horse_summon_prob_pct() then

So a rider without the skill fails nine times out of ten, loses 100 stamina on
every attempt, and learns nothing from it. The function now returns 100 and the
roll always passes. The stamina cost, the grade checks and every other condition
are untouched: this changes the odds, not the price.

TWO THINGS THIS SCRIPT LEARNED THE HARD WAY.

1. Editing the .quest source alone does nothing a player can see. The image
   compiles only web_admin.quest and high_risk.quest; the 237 stock quests run
   from pre-compiled plain-text chunks under object/. The DEFINITION lives in
   object/state/horse_summon -- the three per-item chunks under
   object/5005*/use/ only call it -- so that one file is what has to change.

2. `return' must be the last statement of its block in Lua, so it cannot simply
   be prepended to a function body: `return 100 local x = ...' is a syntax
   error. `do return 100 end' is the idiom, and it leaves the rest of the body
   in place as unreachable code rather than trying to delete it.

   The first version of this script tried to replace the whole body by matching
   up to the next `end', which matched the `end' of the if-chain instead of the
   function's own, and left a stray `end' and an orphaned `return 10' behind.
   That is why the source edit below replaces an EXACT known body rather than a
   regex span.

Idempotent. A second run reports `already patched'.
"""
import io
import os
import sys

SHARE = os.environ.get("M2SHARE", "")
QUEST = os.path.join(SHARE, "locale/english/quest")

SOURCE = os.path.join(QUEST, "horse_summon.quest")
STATE = os.path.join(QUEST, "object/state/horse_summon")

MARK = "always 100: see set_horse_summon_always_succeeds.py"

SRC_OLD = """function get_horse_summon_prob_pct()
	local skill_level=pc.get_skill_level(131)
	if skill_level==1 then
		return 15
	elseif skill_level==2 then
		return 20
	elseif skill_level==3 then
		return 30
	elseif skill_level==4 then
		return 40
	elseif skill_level==5 then
		return 50
	elseif skill_level==6 then
		return 60
	elseif skill_level==7 then
		return 70
	elseif skill_level==8 then
		return 80
	elseif skill_level==9 then
		return 90
	elseif skill_level>=10 then
		return 100
	end
	return 10
end"""

SRC_NEW = """function get_horse_summon_prob_pct()
	-- """ + MARK + """
	-- The skill still levels and still matters everywhere else; only the
	-- summon roll is taken out, because failing it costs 100 stamina and
	-- teaches nothing.
	return 100
end"""

CMP_OLD = "get_horse_summon_prob_pct= function ()"
CMP_NEW = "get_horse_summon_prob_pct= function () do return 100 end -- " + MARK + " "


def main():
    if not os.path.isdir(QUEST):
        sys.exit("not found: %s (set M2SHARE)" % QUEST)

    changed = 0

    for path, old, new in ((SOURCE, SRC_OLD, SRC_NEW), (STATE, CMP_OLD, CMP_NEW)):
        if not os.path.isfile(path):
            sys.exit("not found: %s" % path)
        s = io.open(path, encoding="utf-8", errors="surrogateescape").read()
        if MARK in s:
            print("already patched: %s" % os.path.basename(path))
            continue
        if s.count(old) != 1:
            sys.exit("anchor not found exactly once in %s" % path)
        io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(
            s.replace(old, new, 1))
        print("patched: %s" % os.path.basename(path))
        changed += 1

    if changed:
        print("\nThe game core must be restarted for the compiled chunk to be re-read.")


if __name__ == "__main__":
    main()
