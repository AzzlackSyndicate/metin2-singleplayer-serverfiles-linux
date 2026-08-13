#!/usr/bin/env python3
"""Quests whose rewards contradict the quest's own text, including one exploit.

These are defects, not preferences, so they are applied to every server this
project builds and there is no switch for them. Each one is a place where the
code and the text shipped in the same package disagree with each other, and in
every case below the intended answer is written down somewhere in the pack
itself.

THE ONE THAT MATTERS MOST
-------------------------
dragon_lair_weekly PAYS the player the 150,000 Yang fee it is supposed to
CHARGE. The line reads `pc.changemoney(settings.amount_to_pay)' with no minus,
directly under a comment that says "player pays yang for the item", and the
branch above it only checks that the player COULD have afforded it -- so it
never runs out. Handing the items in again pays again. Anyone who noticed had
unlimited money, on any server running these files.

WHY THIS IS A SCRIPT AND NOT A HAND EDIT
----------------------------------------
share/ is baked into the game image and re-staging the tree overwrites every
file touched here. A replayable patch survives that; a hand edit does not.

WHY IT TOUCHES quest/object/ AS WELL AS THE .quest SOURCES
----------------------------------------------------------
A .quest file is never read by the server. `qc' compiles it into a tree of tiny
Lua chunks under quest/object/, and THAT is what the cores load at boot. The
image build (game/Dockerfile, stage 2b) deliberately compiles only the panel's
own web_admin.quest and leaves the 237 stock quests on the object files they
shipped with, byte for byte -- so editing a .quest source alone changes nothing
a player can see. Every logic change below is therefore applied twice: once to
the source, so the tree stays honest and a future full recompile reproduces it,
and once to the matching object/ chunk, so it actually takes effect. The object
chunks are plain text Lua with spaces around every token, which is why the
replacements look the way they do.

WHY ONLY translate.lua AND NOT translate_de.lua / translate_tr.lua
------------------------------------------------------------------
questlua.cpp:563 loads exactly one translation file, `<basepath>/translate.lua',
and locale_service.cpp:477 fixes that base path at "locale/english". The
fourteen other translate_*.lua files ship with the pack and are never opened.
Editing them would change nothing and would only invite the belief that the
German text is live.

THE REST OF THE LIST
--------------------
A sweep of all 303 quests for the same shape -- announced reward against paid
reward -- turned up these:

  * new_quest_premium_lv4 ("A new fragrance") calls pc.give_item2(rewardVnum),
    and `rewardVnum' is assigned nowhere in the file: not as a local, not as a
    global, not through pc.setqf. It is nil there, so the call hands the player
    nothing, and on a stricter binding it aborts the handler in the middle of
    the payout. The text promises Yang and only Yang ("Yang: %d"), and the Yang
    is paid by the line above, so the give_item2 call is dead code.
  * collect_herb_lv4 (quest name make_herb_lv4) returns out of the Shaman
    branch before the 1,000 Yang, the 500 experience and the hand-off to the
    levelup quest. Its five sibling quests all fall through instead.
  * new_quest_lv7 announces a random reward, then calls the same two random
    functions AGAIN to pay it, so the player is told one figure and given
    another.
  * marriage_manage quotes the divorce fee as MONEY_NEED_FOR_ONE/10000 -- "you
    need 50 Yang" against a charge of 500,000. Its own sibling string spells
    out the real number.
  * main_quest_lv14 passes `stone', the vnum of the Spirit Stone it hands over,
    to pc.give_exp2. The quest's own preview promises 48,000 experience beside
    the 10,000 Yang and the stone that the surrounding lines already deliver,
    so that is what it now pays; the completion text, which described a
    different quest's reward entirely, is corrected to match.
  * pre_event_heavens_cave sizes its THIRD reward slot by testing the FIRST
    slot's vnum, so the Sushi multiplier never fires. Every neighbouring branch
    in the chain tests potion3.

And one stale string, in subquest_47: both its call sites announce 1,300,000
experience and both pay 2,300,000, while a third branch of the same quest
already announces 2,300,000. The code is right and the string is the copy that
was not updated.

WHAT WAS FOUND AND DELIBERATELY NOT TOUCHED
-------------------------------------------
  * The main_quest_lv2/3/6/7/9/10/12/15/16/27/30/55 line and
    main_quest_flame_lv105 announce experience, Yang and items that the code
    does not pay -- in places off by two orders of magnitude, in places an item
    that is simply never given. That is not one bug, it is the whole main quest
    line disagreeing with a translate.lua from a different revision, and
    "fixing" it either multiplies rewards by up to 274 or rewrites a dozen
    reward texts. Which side is authoritative is a balance decision, so it is
    the operator's, not this script's.
  * event_flame_dungeon_open promises a teleport scroll and a passage ticket
    from vnums 71173 and 71174, and neither exists in item_proto.txt. It cannot
    be fixed here; item_proto.txt belongs to someone else.
  * Twelve subquest files pair each say_reward caption with the grant belonging
    to the NEXT caption. Every reward is delivered and the totals are right;
    only the per-line captions are rotated. Cosmetic, and twelve files of churn
    to fix.
  * arne_test2 passes a possibly-nil `Reward' to pc.change_money. It is a
    developer test quest.

The horse-medal time gates and the Musk Oil wording used to live in this file
as well. They are preferences rather than defects, so they moved to
files/custom/, behind the Custom Experience switch.

Idempotent. A second run reports `already patched' for every edit.

    M2SHARE=<context>/serverfiles/share python3 fix_quest_reward_defects.py
"""
import io
import os
import sys

# The staged tree that gets built into the game image: <context>/serverfiles/share.
SHARE = os.environ.get("M2SHARE", "")

QUEST = "locale/english/quest"
OBJECT = QUEST + "/object"
TRANSLATE = "locale/english/translate.lua"

# Every file here is read and written as latin-1, which is a byte-for-byte
# round trip for the whole 0-255 range. The pack's text files are cp1252 and
# the object chunks are ASCII; decoding them as anything cleverer would risk
# rewriting bytes this patch never meant to touch.
#
# (relative path, marker that exists ONLY after patching, old text, new text)
EDITS = [
    # ---------------------------------------------------------------- (4) ---
    (
        QUEST + "/new_quest_premium_lv4.quest",
        "rewardVnum is never assigned",
        'pc.change_money(pc.getqf("amountYang") )\r\n'
        "                pc.give_item2(rewardVnum)\r\n",
        'pc.change_money(pc.getqf("amountYang") )\r\n'
        "                -- The line that stood here read pc.give_item2(rewardVnum), and\r\n"
        "                -- rewardVnum is never assigned anywhere in this quest, so the\r\n"
        "                -- call handed over nothing at best. The reward text promises\r\n"
        "                -- Yang alone, and the line above pays it.\r\n",
    ),
    (
        OBJECT + "/9001/chat/new_quest_premium_lv4.give_trader_premiumitem.0.script",
        'pc . change_money ( pc . getqf ( "amountYang" ) ) \npc . remove_item',
        'pc . change_money ( pc . getqf ( "amountYang" ) ) \n'
        "pc . give_item2 ( rewardVnum ) \n",
        'pc . change_money ( pc . getqf ( "amountYang" ) ) \n',
    ),

    # dragon_lair_weekly: the fee is CREDITED instead of charged.
    (
        QUEST + "/dragon_lair_weekly.quest",
        "-- The sign was missing",
        "pc.changemoney(settings.amount_to_pay) -- player pays yang for the item",
        "-- The sign was missing: this line CREDITED the fee it was meant to\r\n"
        "\t\t\t\t\t-- charge, so every hand-over paid the player 150,000 Yang instead\r\n"
        "\t\t\t\t\t-- of taking it. The branch above only checks that the player COULD\r\n"
        "\t\t\t\t\t-- have afforded it, which is what made this repeatable.\r\n"
        "\t\t\t\t\tpc.changemoney(-settings.amount_to_pay)",
    ),
    (
        OBJECT + "/30122/chat/dragon_lair_weekly.hunt_item.0.script",
        "pc . changemoney ( - settings . amount_to_pay )",
        "pc . changemoney ( settings . amount_to_pay ) \n",
        "pc . changemoney ( - settings . amount_to_pay ) \n",
    ),

    # collect_herb_lv4 (quest name make_herb_lv4): Shamans alone were paid in
    # weapon only.
    (
        QUEST + "/collect_herb_lv4.quest",
        "No `return' here",
        "\t\t\t\t\t\tsay_reward(gameforge.collect_herb_lv4._150_sayReward)\n"
        "\t\t\t\t\t\treturn\n",
        "\t\t\t\t\t\tsay_reward(gameforge.collect_herb_lv4._150_sayReward)\n"
        "\t\t\t\t\t\t-- No `return' here: it jumped over the Yang, the experience and\n"
        "\t\t\t\t\t\t-- the hand-off to the levelup quest below, so a Shaman finished\n"
        "\t\t\t\t\t\t-- this quest with the weapon and nothing else. The five sibling\n"
        "\t\t\t\t\t\t-- collect_herb quests all fall through here.\n",
    ),
    (
        OBJECT + "/20084/chat/make_herb_lv4.go_to_disciple.0.script",
        "collect_herb_lv4 . _150_sayReward ) \nelse ",
        "say_reward ( gameforge . collect_herb_lv4 . _150_sayReward ) \nreturn \nelse \n",
        "say_reward ( gameforge . collect_herb_lv4 . _150_sayReward ) \nelse \n",
    ),

    # new_quest_lv7: the announced reward and the paid reward are two separate
    # dice rolls.
    (
        QUEST + "/new_quest_lv7.quest",
        "These called reward_exp() and reward() again",
        "pc.give_exp2(new_quest_lv7.reward_exp())\n"
        "                pc.change_money(new_quest_lv7.reward())\n",
        "-- These called reward_exp() and reward() again, and both roll the\n"
        "                -- dice a second time, so the two amounts announced just above\n"
        "                -- were never the amounts actually paid.\n"
        "                pc.give_exp2(reward_exp)\n"
        "                pc.change_money(reward)\n",
    ),
    (
        OBJECT + "/20008/chat/new_quest_lv7.back_to_octavio.0.script",
        "pc . give_exp2 ( reward_exp ) ",
        "pc . give_exp2 ( new_quest_lv7 . reward_exp ( ) ) \n"
        "pc . change_money ( new_quest_lv7 . reward ( ) ) \n",
        "pc . give_exp2 ( reward_exp ) \npc . change_money ( reward ) \n",
    ),

    # marriage_manage: the divorce fee is quoted at a ten-thousandth of what is
    # charged. The sibling string _760_sayReward spells out the real figure.
    (
        QUEST + "/marriage_manage.quest",
        "-- No division here",
        "\t\t\tsay_reward(string.format(gameforge.marriage_manage._750_sayReward,"
        " MONEY_NEED_FOR_ONE/10000))\n",
        "\t\t\t-- No division here: the string is \"you need %s Yang\" and the charge\n"
        "\t\t\t-- further down is the full 500,000, so the /10000 quoted a divorce at\n"
        "\t\t\t-- 50 Yang. It is a leftover from the Korean currency scale.\n"
        "\t\t\tsay_reward(string.format(gameforge.marriage_manage._750_sayReward,"
        " MONEY_NEED_FOR_ONE))\n",
    ),
    (
        OBJECT + "/11000/chat/marriage_manage.start.0.script",
        "_750_sayReward , MONEY_NEED_FOR_ONE ) )",
        "_750_sayReward , MONEY_NEED_FOR_ONE / 10000 ) )",
        "_750_sayReward , MONEY_NEED_FOR_ONE ) )",
    ),
    (
        OBJECT + "/11002/chat/marriage_manage.start.0.script",
        "_750_sayReward , MONEY_NEED_FOR_ONE ) )",
        "_750_sayReward , MONEY_NEED_FOR_ONE / 10000 ) )",
        "_750_sayReward , MONEY_NEED_FOR_ONE ) )",
    ),
    (
        OBJECT + "/11004/chat/marriage_manage.start.0.script",
        "_750_sayReward , MONEY_NEED_FOR_ONE ) )",
        "_750_sayReward , MONEY_NEED_FOR_ONE / 10000 ) )",
        "_750_sayReward , MONEY_NEED_FOR_ONE ) )",
    ),

    # main_quest_lv14: an item vnum used as an experience amount.
    (
        QUEST + "/main_quest_lv14.quest",
        "-- `stone' is the vnum of the Spirit Stone",
        "pc.give_exp2( stone )\n",
        "-- `stone' is the vnum of the Spirit Stone handed over two lines\n"
        "\t\t\t-- down, somewhere between 28030 and 28243. Paid out as experience it\n"
        "\t\t\t-- gave whatever that number happened to be. This quest's own preview,\n"
        "\t\t\t-- _40_sayReward, promises 48,000 experience with the 10,000 Yang and\n"
        "\t\t\t-- the Spirit Stone that the two lines below already deliver.\n"
        "\t\t\tpc.give_exp2( 48000 )\n",
    ),
    (
        OBJECT + "/notarget/target/main_quest_lv14.gotoboss2.0.script",
        "pc . give_exp2 ( 48000 )",
        "pc . give_exp2 ( stone ) \n",
        "pc . give_exp2 ( 48000 ) \n",
    ),
    (
        # The completion text described a different quest's reward entirely:
        # 20,000 Yang and a Lump of Gold, neither of which this quest gives.
        TRANSLATE,
        "48,000 experience points.[ENTER]You have received 10,000 Yang.[ENTER]You have"
        " received a Spirit Stone",
        'gameforge.main_quest_lv14._130_sayReward = "You have received 20,000 experience'
        " points.[ENTER]You have received 20,000 Yang.[ENTER]You have received a Lump of"
        ' Gold. "',
        'gameforge.main_quest_lv14._130_sayReward = "You have received 48,000 experience'
        " points.[ENTER]You have received 10,000 Yang.[ENTER]You have received a Spirit"
        ' Stone. "',
    ),

    # subquest_47 announces 1,300,000 experience at both call sites and pays
    # 2,300,000 at both. Its own sibling string _250_sayReward, on a third
    # branch of the same quest, already says 2,300,000 -- so the number in the
    # code is the intended one and this string is the stale copy.
    (
        TRANSLATE,
        '_180_sayReward = "You receive 2,300,000',
        'gameforge.subquest_47._180_sayReward = "You receive 1,300,000 experience points. "',
        'gameforge.subquest_47._180_sayReward = "You receive 2,300,000 experience points. "',
    ),

    # pre_event_heavens_cave: the third reward slot is sized by testing the
    # FIRST slot's vnum. Every other branch in the same chain tests potion3.
    (
        QUEST + "/pre_event_heavens_cave.quest",
        "elseif potion3 > 50800 then",
        "elseif potion1 > 50800 then -- it is Sushi",
        "elseif potion3 > 50800 then -- it is Sushi",
    ),
    (
        OBJECT + "/20090/chat/pre_event_heavens_cave.pre_event_heavens_cave.0.script",
        "elseif potion3 > 50800 then",
        "elseif potion1 > 50800 then \n",
        "elseif potion3 > 50800 then \n",
    ),

]


def main():
    if not SHARE or not os.path.isdir(SHARE):
        sys.exit("set M2SHARE to the serverfiles share/ tree (got %r)" % SHARE)

    changed = 0
    skipped = 0

    for rel, marker, old, new in EDITS:
        path = os.path.join(SHARE, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            sys.exit("not found: %s (set M2SHARE to the serverfiles share/ tree)" % path)

        s = io.open(path, encoding="latin-1", newline="").read()
        if marker in s:
            print("already patched: %s" % rel)
            skipped += 1
            continue
        if s.count(old) != 1:
            sys.exit("anchor found %d times (want 1) in %s" % (s.count(old), rel))

        io.open(path, "w", encoding="latin-1", newline="").write(s.replace(old, new, 1))
        print("patched: %s" % rel)
        changed += 1

    print("%d edit(s) applied, %d already in place." % (changed, skipped))


if __name__ == "__main__":
    main()
