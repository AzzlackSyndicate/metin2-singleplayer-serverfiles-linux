#!/usr/bin/env bash
# =============================================================================
#  files/fixes/apply.sh -- defects in the shipped server files, fixed for
#  everybody.
#
#  Nothing in here is behind a switch, and nothing in here is a matter of taste.
#  Each one is a place where the files that shipped together contradict each
#  other, and where the intended answer is written down somewhere in the same
#  package. Sitting beside files/custom/ makes the difference visible: that
#  directory is what an operator chooses, this directory is what every server
#  gets.
#
#  What it applies:
#
#      fix_quest_reward_defects.py     eight quests whose rewards do not match
#                                      their own text -- among them the Dragon
#                                      Lair quest, which PAID the 150,000 Yang
#                                      fee it was written to CHARGE, repeatably,
#                                      on every server running these files
#      fix_item_stacking.py            Blessing Scrolls and Bravery Capes merge
#      fix_bare_stackable_token.py     eight more rows that claimed to stack in
#                                      a spelling the server does not read
#
#  Called by linux-port/docker/prepare-context.sh, unconditionally, after the
#  server tree has been staged and before the image is built from it. That is
#  the only window: share/ is baked into the image, and it is re-staged from the
#  pristine archive on every assembly, so a hand edit there does not survive a
#  single update.
#
#  IDEMPOTENT, every step. Run it twice and the second run changes nothing and
#  says so.
#
#  Usage:
#      ./apply.sh --tree <context>/game/src
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TREE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --tree)   TREE="$2"; shift 2 ;;
    -h|--help) sed -n '2,34p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

say()  { printf '   %s\n' "$*"; }
die()  { printf 'FATAL: %s\n' "$*" >&2; exit 1; }

[ -n "$TREE" ] || die "--tree is required (the staged game/src directory)"
SHARE="$TREE/serverfiles/share"
[ -d "$SHARE" ] || die "$SHARE not found -- --tree wants the game/src directory that holds serverfiles/share"

command -v python3 >/dev/null 2>&1 \
  || die "python3 is not installed. These fixes are Python patches applied to
       the staged server tree, so they cannot be applied without it. On Debian
       or Ubuntu: apt-get install -y python3"

export M2SHARE="$SHARE"

say "quest rewards, and the Dragon Lair money exploit"
python3 "$HERE/fix_quest_reward_defects.py"

say "items that would not stack"
python3 "$HERE/fix_item_stacking.py"
python3 "$HERE/fix_bare_stackable_token.py"

printf '\n   The shipped-file fixes are applied.\n'
