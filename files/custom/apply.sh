#!/usr/bin/env bash
# =============================================================================
#  files/custom/apply.sh -- everything behind "Enable Custom Experience?".
#
#  This is the whole of the answer to that question, in one place, replayable.
#  It exists because the changes below used to be made by hand on one machine:
#  fetch-sources.sh re-stages the server tree from the pristine archive on every
#  assembly, so a hand edit is silently reverted the next time anyone updates,
#  and the only copy of it lives on that one machine. A local test server ended
#  up missing content the live one had, and nobody could say why without
#  comparing two databases.
#
#  What it applies:
#
#      set_pickup_range.py                    600 units on foot, 800 mounted
#      set_horse_summon_always_succeeds.py    calling a horse always works
#      remove_horse_medal_timegates.py        no waiting out real time
#      musk_oil_from_the_general_store.py     the quest points where the oil is
#      gen_drops.py + targets.txt             a bonus drop group on 283 metins
#                                             and bosses
#      shop_musk_oil.sql                      the oil on the shop's counter
#
#  Called by linux-port/docker/prepare-context.sh when M2_CUSTOM_EXPERIENCE=1 --
#  which is the one moment that works: after the server tree has been staged and
#  before the image is built from it. The two other settings the switch carries,
#  High Risk and the movement-speed bonus, are staged by prepare-context.sh
#  itself because they are quests rather than patches.
#
#  IDEMPOTENT, every step. Run it twice and the second run changes nothing and
#  says so. Each script anchors on an exact known span of text and refuses to
#  guess rather than patch the wrong thing.
#
#  Usage:
#      ./apply.sh --tree <context>/game/src [--schema <context>/panel/schema]
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TREE=""
SCHEMA=""

while [ $# -gt 0 ]; do
  case "$1" in
    --tree)   TREE="$2"; shift 2 ;;
    --schema) SCHEMA="$2"; shift 2 ;;
    -h|--help) sed -n '2,34p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

say()  { printf '   %s\n' "$*"; }
die()  { printf 'FATAL: %s\n' "$*" >&2; exit 1; }

[ -n "$TREE" ] || die "--tree is required (the staged game/src directory)"
[ -d "$TREE" ] || die "$TREE does not exist"

SHARE="$TREE/serverfiles/share"
[ -d "$SHARE" ] || die "$SHARE not found -- --tree wants the game/src directory that holds serverfiles/share"

# Every patch below is Python. Checked once, here, so that a machine without it
# says so in one sentence instead of failing six times in a row.
command -v python3 >/dev/null 2>&1 \
  || die "python3 is not installed. The Custom Experience is a set of Python
       patches applied to the staged server tree, so it cannot be applied
       without it. On Debian or Ubuntu: apt-get install -y python3"

export M2SHARE="$SHARE"
export M2SRC="$TREE"

# -----------------------------------------------------------------------------
say "pick-up range"
python3 "$HERE/set_pickup_range.py"

say "horse summoning"
python3 "$HERE/set_horse_summon_always_succeeds.py"

say "horse medal time gates"
python3 "$HERE/remove_horse_medal_timegates.py"

say "Musk Oil quest wording"
python3 "$HERE/musk_oil_from_the_general_store.py"

# -----------------------------------------------------------------------------
say "bonus drops on metins and bosses"
# gen_drops.py GENERATES a table rather than editing one, and it is not
# idempotent by itself: run it against its own output and every mob gets the
# eleven items a second time. So it is never run against its own output. The
# file is checked for the groups this run would add, and if they are already
# there nothing happens at all; if they are not, the file is known to be
# untouched and a pristine copy is kept beside it before it is replaced.
#
# Keeping the input rather than trying to reverse the edit is deliberate. There
# is no safe way to unpick "everything this script appended" from a table that
# an operator may also have edited, and an approximate answer to that question
# corrupts drop tables silently.
DROPS="$SHARE/locale/english/mob_drop_item.txt"
[ -f "$DROPS" ] || die "$DROPS not found"

# gen_drops.py names every group it writes bonus_<kind>_<mob vnum>, and no group
# in the shipped table is named that way. Matched on the prefix alone rather
# than on bonus_metin_, so that an operator who narrows targets.txt to bosses
# only is still recognised as having run this -- otherwise the table would be
# generated a second time on top of itself.
if grep -q 'Group[[:space:]]*bonus_' "$DROPS"; then
  say "already patched: locale/english/mob_drop_item.txt"
else
  cp -a "$DROPS" "$DROPS.pristine"
  python3 "$HERE/gen_drops.py" "$DROPS.pristine" "$HERE/targets.txt" "$DROPS.new"
  mv "$DROPS.new" "$DROPS"
  say "patched: locale/english/mob_drop_item.txt (pristine copy kept beside it)"
fi

# -----------------------------------------------------------------------------
say "Musk Oil in the General Store"
# The only piece of this that is a database row rather than a file. It is staged
# into the panel's schema directory, because the panel's entrypoint applies
# every .sql in there on EVERY start -- not only when the database is first
# created -- which is the one mechanism in this project that reaches an existing
# database. The statement is an INSERT IGNORE against a unique key, so applying
# it a hundred times is the same as applying it once.
if [ -n "$SCHEMA" ]; then
  mkdir -p "$SCHEMA"
  cp -a "$HERE/shop_musk_oil.sql" "$SCHEMA/shop_musk_oil.sql"
  say "shop_musk_oil.sql -> panel/schema/"
else
  say "WARNING: no --schema given, so the shop row was not staged. Musk Oil"
  say "         will not be on the General Store Saleswoman's counter, and the"
  say "         quest that now sends players to her cannot be finished."
fi

printf '\n   The Custom Experience is applied.\n'
