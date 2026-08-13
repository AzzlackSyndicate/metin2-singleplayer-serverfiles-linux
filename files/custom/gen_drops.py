# -*- coding: utf-8 -*-
"""Add a `Type limit' drop group to every metin stone and every boss.

`limit' is the group type whose entries are rolled INDEPENDENTLY, one per item:

    DWORD dwPct = (DWORD)(10000.0f * fPercent);      // 2.0 -> 20000
    if (v[i].dwPct >= (DWORD)number(1, 1000000))     // 20000/1000000 = 2 %

`drop' would have been wrong: there the percentages are shares of ONE weighted
roll (CMobItemGroup::AddItem accumulates them and GetOne picks a single entry),
so adding eleven lines at 2 to a group whose four existing lines carry 0.8 each
would have taken 88 % of that mob's drops away from what it drops today.

`limit' groups live in their own map, keyed by mob vnum, so they coexist with
the `drop' and `kill' groups the same mob already has -- mob 1069 in this file
has all three. But only ONE per mob: where a target already has one, the items
are appended to it with continuing numbering instead of a second group.

Level_limit is the level of the KILLER (item_manager.cpp: iLevel =
pkKiller->GetLevel()), so 1 means "always".

The two bonus items are 39028 and 39029, NOT the 71051/71052 that used to be
here. All four re-roll random bonuses, but not the same ones. 71051 and 71052
are USE_SPECIAL and are dispatched by vnum (char_item.cpp:3829, :3882) into
CItem::AddRareAttribute / CItem::ChangeRareAttribute (item_attribute.cpp:377,
:351), which write m_aAttr[5] and m_aAttr[6] -- the two RARE slots, the ones
past the five normal ones, that almost nothing in this game ever fills. 39028
and 39029 are USE_CHANGE_ATTRIBUTE and USE_ADD_ATTRIBUTE (char_item.cpp:4553,
:4658) and go to CItem::ChangeAttribute / CItem::AddAttribute
(item_attribute.cpp:188, :222), which work on m_aAttr[0..3] -- the four bonuses
a player actually reads off an item. Dropping the rare pair gave players two
items that appear to do nothing on everything they own.
"""
import io
import re
import sys

SRC, TARGETS, OUT = sys.argv[1], sys.argv[2], sys.argv[3]

ITEMS = [                      # (vnum, count)
    (25040, 1),                # Blessing Scroll
    (39028, 1),                # Enchant Item      -- re-rolls bonuses 1-4
    (39029, 1),                # Reinforce Item    -- adds a bonus, 1-4
    (71094, 1),                # Concentrated Reading
    (71001, 1),                # Exorcism Scroll
    (70005, 1),                # Experience Ring   (equipment, 60 min)
    (72005, 1),                # Thief's Gloves     (equipment, 30 min)
    (70038, 5), (70038, 10), (70038, 25), (70038, 50),   # Bravery Cape, stacked
]
PCT = {"metin": "5", "boss": "5", "sub": "5"}

targets = {}
for line in io.open(TARGETS, encoding="latin-1"):
    parts = line.split()
    if len(parts) == 2 and parts[0].isdigit():
        targets[int(parts[0])] = parts[1]

s = io.open(SRC, encoding="latin-1", newline="").read()

# Which mobs already have a `limit' group, and where that group's body ends.
group_re = re.compile(r"Group\s+[^\n]*\n\{(.*?)\n\}", re.S)
existing_limit = {}
for m in group_re.finditer(s):
    body = m.group(1)
    if re.search(r"^\s*Type\s+limit\s*$", body, re.M):
        mob = re.search(r"^\s*Mob\s+(\d+)\s*$", body, re.M)
        if mob:
            existing_limit[int(mob.group(1))] = m

def lines_for(kind, start):
    pct = PCT[kind]
    out = []
    for i, (vnum, count) in enumerate(ITEMS, start):
        out.append("\t%d\t%d\t%d\t%s" % (i, vnum, count, pct))
    return out

added_groups = 0
merged = 0

# 1. merge into the groups that already exist -- highest offset first, so the
#    earlier replacements do not move the later ones.
for mob in sorted(existing_limit, key=lambda v: -existing_limit[v].start()):
    if mob not in targets:
        continue
    m = existing_limit[mob]
    body = m.group(1)
    nums = [int(x) for x in re.findall(r"^\s*(\d+)\t", body, re.M)]
    start = (max(nums) + 1) if nums else 1
    new_body = body.rstrip("\n") + "\n" + "\n".join(lines_for(targets[mob], start))
    s = s[:m.start(1)] + new_body + s[m.end(1):]
    merged += 1

# 2. append a group for everyone else
chunks = []
for mob in sorted(targets):
    if mob in existing_limit:
        continue
    kind = targets[mob]
    chunks.append("Group\tbonus_%s_%d\n{\n\tLevel_limit\t1\n\tMob\t%d\n\tType\tlimit\n%s\n}\n"
                  % (kind, mob, mob, "\n".join(lines_for(kind, 1))))
    added_groups += 1

if not s.endswith("\n"):
    s += "\n"
s += "".join(chunks)

io.open(OUT, "w", encoding="latin-1", newline="").write(s)
print("new groups: %d   groups extended: %d   items per mob: %d"
      % (added_groups, merged, len(ITEMS)))
