#!/usr/bin/env python3
"""Three quest defects the operator reported, fixed in the staged server tree.

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


(4) A REWARD THE QUEST PROMISES BUT DOES NOT HAND OVER
------------------------------------------------------
new_quest_premium_lv4 ("A new fragrance") pays out with

    say_reward(string.format(..._270_say_reward, pc.getqf("amountYang")))
    pc.change_money(pc.getqf("amountYang"))
    pc.give_item2(rewardVnum)

`rewardVnum' is assigned nowhere in the file -- not as a local, not as a global,
not through pc.setqf. It is nil at that point, so the call hands the player
nothing, and on a stricter binding it aborts the handler in the middle of the
payout. The text promises Yang and only Yang ("Yang: %d"), and the Yang is paid
by the line above, so the intent is unambiguous: the give_item2 call is dead
code and comes out.

A sweep of all 303 quests for the same shape -- announced reward against paid
reward -- turned up six more defects, all fixed here:

  * dragon_lair_weekly pays the player the 150,000 Yang fee it is supposed to
    charge. `pc.changemoney(settings.amount_to_pay)' with no minus, right under
    a comment that says "player pays yang for the item" and a branch that only
    checks the player COULD have afforded it. Repeatable, so it is an
    unlimited-Yang exploit as well as a wrong reward.
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


(5) THE HORSE MEDAL QUESTS WERE GATED BY THE CLOCK
---------------------------------------------------
Five quests take the Horse Medal (item 50050), and all five made the player
wait out real time:

  * horse_levelup -- training the horse from level 11 to 20 set a `next_time'
    flag to between 16 and 32 hours ahead and refused every further session
    until it passed. One training run per day, nine runs to finish.
  * pony_levelup -- the same cooldown on levels 1 to 10, 12 to 24 hours.
  * horse_upgrade -- the Stable Boy took the Horse Picture and set `make_time'
    8 to 16 hours ahead before he would sell the Armed Horse Book.
  * horse_upgrade2 -- the same wait again for the Military Horse Book.
  * pony_buy -- and again, before he would sell the horse itself.

All five are gone. The waiting state in the three upgrade quests is skipped
outright: the Stable Boy now moves the player straight to `buy'. The `login'
handler that used to release the waiting state is made unconditional as well,
which is what frees players who are sitting in that state right now with a
timestamp in the future -- without it they would still serve out the rest of
their wait.

What is deliberately LEFT ALONE: the 30-minute `limit_time' clock in
horse_upgrade, horse_upgrade2 and pony_buy. That is the mission timer for the
kill-100 qualification run, shown to the player as a countdown by q.set_clock.
It is the challenge, not a gate on repeating the quest.

Also left alone: the four blocks in pony_levelup that still WRITE `next_time'.
They sit in four near-identical copies through a 6,000-line file, and with the
one branch that read the flag gone they write something nothing looks at.

The dialogue that told the player to come back tomorrow is rewritten to match,
otherwise the Stable Boy would send them away from a book he is holding out.


(11) THE MUSK OIL QUEST POINTED AT AN ITEM SHOP THIS SERVER DOES NOT HAVE
-------------------------------------------------------------------------
The same new_quest_premium_lv4 asks for one Musk Oil (item 30177) and told the
player to order it "in a very special shop", follow a turning coin, pay in
Dragon Marks and collect it from the Storekeeper -- the Gameforge item shop
flow. There is no item shop here. Musk Oil is stocked by the General Store
Saleswoman for 100 Yang, and the quest itself only ever checks that the player
holds the item, so nothing but the wording was ever wrong. Three strings say it
the old way and all three are rewritten.

Idempotent. A second run reports `already patched' for every edit.
"""
import io
import os
import sys

# The staged tree that gets built into the game image. Override for a checkout
# in another location.
SHARE = os.environ.get(
    "M2SHARE", r"C:\Users\hatip\Metin2Server\game\src\serverfiles\share")

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

    # ---------------------------------------------------------------- (5) ---
    # horse_levelup: the once-a-day training cooldown, both the guard that
    # turned the player away and the flag it read.
    (
        QUEST + "/horse_levelup.quest",
        "-- The daily training cooldown",
        'elseif get_time()<pc.getqf("next_time") then\n'
        "\t\t\t\tsay_title(gameforge.horse_levelup._240_sayTitle)\n"
        "\t\t\t\tsay(gameforge.horse_levelup._270_say)\n"
        "\t\t\telseif horse.get_stamina_pct()<=10 then\n",
        "-- The daily training cooldown stood here: a branch that compared\n"
        '\t\t\t-- get_time() against the "next_time" flag and sent the player away\n'
        "\t\t\t-- until it had passed. Training is now repeatable.\n"
        "\t\t\telseif horse.get_stamina_pct()<=10 then\n",
    ),
    (
        QUEST + "/horse_levelup.quest",
        '-- Nothing sets "next_time" any more',
        "if is_test_server() then\n"
        '\t\t\t\t\tpc.setqf("next_time", get_time()+10)\n'
        "\t\t\t\telse\n"
        '\t\t\t\t\tpc.setqf("next_time", get_time()+number(16, 32)*60*60)\n'
        "\t\t\t\tend\n"
        "\t\t\t\tif horse.get_level()==11 then\n",
        '-- Nothing sets "next_time" any more; the branch that read it is gone.\n'
        "\t\t\t\tif horse.get_level()==11 then\n",
    ),
    (
        OBJECT + "/20349/chat/horse_levelup.start.0.script",
        'setstate ( "need_item50050" ) \nelseif horse . get_stamina_pct ( ) <= 10 then',
        'setstate ( "need_item50050" ) \n'
        'elseif get_time ( ) < pc . getqf ( "next_time" ) then \n'
        "say_title ( gameforge . horse_levelup . _240_sayTitle ) \n"
        "say ( gameforge . horse_levelup . _270_say ) \n"
        "elseif horse . get_stamina_pct ( ) <= 10 then",
        'setstate ( "need_item50050" ) \n'
        "elseif horse . get_stamina_pct ( ) <= 10 then",
    ),
    (
        OBJECT + "/20349/chat/horse_levelup.start.0.script",
        'say ( gameforge . horse_levelup . _310_say ) \nif horse . get_level ( ) == 11 then',
        "if is_test_server ( ) then \n"
        'pc . setqf ( "next_time" , get_time ( ) + 10 ) \n'
        "else \n"
        'pc . setqf ( "next_time" , get_time ( ) + number ( 16 , 32 ) * 60 * 60 ) \n'
        "end \n"
        "if horse . get_level ( ) == 11 then",
        "if horse . get_level ( ) == 11 then",
    ),

    # horse_upgrade: the Armed Horse Book is handed over on the spot.
    (
        QUEST + "/horse_upgrade.quest",
        "-- The Stable Boy used to disappear for 8 to 16 hours",
        "if is_test_server() then\n"
        '\t\t\t\tpc.setqf("make_time", get_time()+10)\n'
        "\t\t\telse\n"
        '\t\t\t\tpc.setqf("make_time", get_time()+number(8, 16)*60*60)\n'
        "\t\t\tend\n"
        "\t\t\tsetstate(wait)\n",
        "-- The Stable Boy used to disappear for 8 to 16 hours to make the\n"
        '\t\t\t-- book, parked in state "wait" behind a "make_time" flag. The book\n'
        "\t\t\t-- is ready when he says it is.\n"
        "\t\t\tsetstate(buy)\n",
    ),
    (
        QUEST + "/horse_upgrade.quest",
        "when login begin\n\t\t\tsetstate(buy)",
        'when login with get_time()>=pc.getqf("make_time") begin\n'
        "\t\t\tsetstate(buy)\n",
        "when login begin\n"
        "\t\t\tsetstate(buy)\n",
    ),
    (
        OBJECT + "/20349/chat/horse_upgrade.report.1.script",
        'say ( gameforge . horse_upgrade . _170_say ) \nsetstate ( "buy" )',
        "if is_test_server ( ) then \n"
        'pc . setqf ( "make_time" , get_time ( ) + 10 ) \n'
        "else \n"
        'pc . setqf ( "make_time" , get_time ( ) + number ( 8 , 16 ) * 60 * 60 ) \n'
        "end \n"
        'setstate ( "wait" ) \n',
        'setstate ( "buy" ) \n',
    ),
    (
        # Frees anyone already parked in the waiting state with a timestamp in
        # the future. The marker has to be the comment: the patched body is a
        # substring of the unpatched one, so anything shorter would report a
        # fresh tree as already done.
        OBJECT + "/notarget/login/horse_upgrade.wait",
        "-- The 8-to-16-hour wait for the Armed Horse Book is gone",
        'if get_time ( ) >= pc . getqf ( "make_time" ) then setstate ( "buy" ) \n return end ',
        "-- The 8-to-16-hour wait for the Armed Horse Book is gone; this releases\n"
        "-- anyone still parked in the state with a timestamp in the future.\n"
        'setstate ( "buy" ) \n return ',
    ),

    # horse_upgrade2: the same wait again, for the Military Horse Book.
    (
        QUEST + "/horse_upgrade2.quest",
        "-- The Stable Boy used to disappear for 8 to 16 hours",
        "if is_test_server() then\n"
        '\t\t\t\tpc.setqf("make_time", get_time()+10)\n'
        "\t\t\telse\n"
        '\t\t\t\tpc.setqf("make_time", get_time()+number(8, 16)*60*60)\n'
        "\t\t\tend\n"
        "\t\t\tsetstate(wait)\n",
        "-- The Stable Boy used to disappear for 8 to 16 hours to make the\n"
        '\t\t\t-- book, parked in state "wait" behind a "make_time" flag. The book\n'
        "\t\t\t-- is ready when he says it is.\n"
        "\t\t\tsetstate(buy)\n",
    ),
    (
        QUEST + "/horse_upgrade2.quest",
        "when login begin\n\t\t\tsetstate(buy)",
        'when login with get_time()>=pc.getqf("make_time") begin\n'
        "\t\t\tsetstate(buy)\n",
        "when login begin\n"
        "\t\t\tsetstate(buy)\n",
    ),
    (
        OBJECT + "/20349/chat/horse_upgrade2.report.1.script",
        'say ( gameforge . horse_upgrade2 . _270_say ) \nsetstate ( "buy" )',
        "if is_test_server ( ) then \n"
        'pc . setqf ( "make_time" , get_time ( ) + 10 ) \n'
        "else \n"
        'pc . setqf ( "make_time" , get_time ( ) + number ( 8 , 16 ) * 60 * 60 ) \n'
        "end \n"
        'setstate ( "wait" ) \n',
        'setstate ( "buy" ) \n',
    ),
    (
        OBJECT + "/notarget/login/horse_upgrade2.wait",
        "-- The 8-to-16-hour wait for the Military Horse Book is gone",
        'if get_time ( ) >= pc . getqf ( "make_time" ) then setstate ( "buy" ) \n return end ',
        "-- The 8-to-16-hour wait for the Military Horse Book is gone; this releases\n"
        "-- anyone still parked in the state with a timestamp in the future.\n"
        'setstate ( "buy" ) \n return ',
    ),

    # pony_buy: the same 8-to-16-hour wait once more, this time before the
    # Stable Boy will sell the horse itself. The chat handler in state `buy'
    # carries the timestamp a SECOND time, in its `with' clause -- harmless for
    # a player who never enters the waiting state, fatal for one released from
    # it, who would arrive in `buy' and find the Stable Boy mute.
    (
        QUEST + "/pony_buy.quest",
        "-- The Stable Boy used to disappear for 8 to 16 hours",
        "if is_test_server() then\n"
        '\t\t\t\tpc.setqf("make_time", get_time()+10)\n'
        "\t\t\telse\n"
        '\t\t\t\tpc.setqf("make_time", get_time()+number(8, 16)*60*60)\n'
        "\t\t\tend\n"
        "\t\t\tsetstate(wait)\n",
        "-- The Stable Boy used to disappear for 8 to 16 hours to fetch the\n"
        '\t\t\t-- horse, parked in state "wait" behind a "make_time" flag.\n'
        "\t\t\tsetstate(buy)\n",
    ),
    (
        QUEST + "/pony_buy.quest",
        "when login begin\n\t\t\tsetstate(buy)",
        'when login with get_time()>=pc.getqf("make_time") begin\n'
        "\t\t\tsetstate(buy)\n",
        "when login begin\n"
        "\t\t\tsetstate(buy)\n",
    ),
    (
        QUEST + "/pony_buy.quest",
        "_270_npcChat with horse.get_grade()==0 begin",
        "_270_npcChat with horse.get_grade()==0 and get_time()>=pc.getqf(\"make_time\") begin",
        "_270_npcChat with horse.get_grade()==0 begin",
    ),
    (
        OBJECT + "/20349/chat/pony_buy.report.1.script",
        'say ( gameforge . pony_buy . _200_say ) \nsetstate ( "buy" )',
        "if is_test_server ( ) then \n"
        'pc . setqf ( "make_time" , get_time ( ) + 10 ) \n'
        "else \n"
        'pc . setqf ( "make_time" , get_time ( ) + number ( 8 , 16 ) * 60 * 60 ) \n'
        "end \n"
        'setstate ( "wait" ) \n',
        'setstate ( "buy" ) \n',
    ),
    (
        OBJECT + "/notarget/login/pony_buy.wait",
        "-- The 8-to-16-hour wait for the horse is gone",
        'if get_time ( ) >= pc . getqf ( "make_time" ) then setstate ( "buy" ) \n return end ',
        "-- The 8-to-16-hour wait for the horse is gone; this releases anyone\n"
        "-- still parked in the state with a timestamp in the future.\n"
        'setstate ( "buy" ) \n return ',
    ),
    (
        # The marker has to be the comment here too: without it the patched
        # condition is a prefix of the unpatched one. questmanager.cpp:1624
        # hands these chunks to luaL_loadbuffer verbatim, so a comment is just
        # a comment.
        OBJECT + "/20349/chat/pony_buy.buy.0.when",
        "-- The make_time half of this condition is gone",
        'return horse . get_grade ( ) == 0 and get_time ( ) >= pc . getqf ( "make_time" )',
        "-- The make_time half of this condition is gone with the wait it guarded.\n"
        "return horse . get_grade ( ) == 0",
    ),

    # pony_levelup: a 12-to-24-hour cooldown on training the horse from level 1
    # to 10. Only the guard is removed. The four blocks that WRITE "next_time"
    # stay: they are spread through a 6,000-line file in four near-identical
    # copies, and with nothing left to read the flag they are inert.
    (
        QUEST + "/pony_levelup.quest",
        "-- The daily training cooldown",
        'elseif get_time()<pc.getqf("next_time") then\n'
        "\t\t\t\tsay_title(gameforge.horse_exchange_ticket._20_sayTitle)\n"
        "\t\t\t\tsay(gameforge.pony_levelup._540_say)\n"
        "\t\t\telseif horse.get_stamina_pct()<=10 then\n",
        "-- The daily training cooldown stood here: a branch that compared\n"
        '\t\t\t-- get_time() against the "next_time" flag and sent the player away\n'
        "\t\t\t-- until it had passed. Training is now repeatable. The blocks that\n"
        "\t\t\t-- still set the flag are inert now that nothing reads it.\n"
        "\t\t\telseif horse.get_stamina_pct()<=10 then\n",
    ),
    (
        OBJECT + "/20349/chat/pony_levelup.start.0.script",
        'setstate ( "need_item50050" ) \nelseif horse . get_stamina_pct ( ) <= 10 then',
        'setstate ( "need_item50050" ) \n'
        'elseif get_time ( ) < pc . getqf ( "next_time" ) then \n'
        "say_title ( gameforge . horse_exchange_ticket . _20_sayTitle ) \n"
        "say ( gameforge . pony_levelup . _540_say ) \n"
        "elseif horse . get_stamina_pct ( ) <= 10 then",
        'setstate ( "need_item50050" ) \n'
        "elseif horse . get_stamina_pct ( ) <= 10 then",
    ),

    # The dialogue that promised a wait. Left in place: horse_levelup._270_say
    # ("Your horse needs rest. Try again tomorrow."), which the removed branch
    # was the only caller of, and horse_upgrade._210_say /
    # horse_upgrade2._320_say, which only a player already parked in the old
    # waiting state can still reach.
    (
        TRANSLATE,
        'gameforge.horse_levelup._380_say = "Did everything work out? Your results',
        'gameforge.horse_levelup._380_say = "Did everything work out? Your training will'
        "[ENTER]continue tomorrow. Today's results have been[ENTER]recorded on your Horse"
        ' Medal. "',
        'gameforge.horse_levelup._380_say = "Did everything work out? Your results'
        "[ENTER]have been recorded on your Horse Medal. Come back[ENTER]whenever you want"
        ' to train again. "',
    ),
    (
        TRANSLATE,
        "gameforge.horse_upgrade._170_say = \"Well done.[ENTER]If you want to improve"
        " your horse now, you have[ENTER]to exchange your Horse Picture for the Armed"
        "[ENTER]Horse Book. I have one ready",
        "gameforge.horse_upgrade._170_say = \"Well done.[ENTER]If you want to improve"
        " your horse now, you have[ENTER]to exchange your Horse Picture for the Armed"
        "[ENTER]Horse Book. That's going to take a while; you'd[ENTER]best come back"
        " tomorrow. And don't forget, you[ENTER]also need 500,000 Yang to buy the Armed"
        ' Horse[ENTER]Book. "',
        "gameforge.horse_upgrade._170_say = \"Well done.[ENTER]If you want to improve"
        " your horse now, you have[ENTER]to exchange your Horse Picture for the Armed"
        "[ENTER]Horse Book. I have one ready for you right here.[ENTER]And don't forget,"
        ' you also need 500,000 Yang to[ENTER]buy it. "',
    ),
    (
        TRANSLATE,
        "1,000,000 Yang. The book is finished, so speak",
        'gameforge.horse_upgrade2._270_say = "Well done! If you want to improve your'
        " horse now,[ENTER]you need to exchange your Armed Horse Book for[ENTER]the"
        " Military Horse Book. It will also cost you[ENTER]1,000,000 Yang. It's going to"
        ' take some time to[ENTER]finish the book, so come again tomorrow. "',
        'gameforge.horse_upgrade2._270_say = "Well done! If you want to improve your'
        " horse now,[ENTER]you need to exchange your Armed Horse Book for[ENTER]the"
        " Military Horse Book. It will also cost you[ENTER]1,000,000 Yang. The book is"
        ' finished, so speak[ENTER]to me again as soon as you have the money. "',
    ),

    # --------------------------------------------------------------- (11) ---
    # Musk Oil, item 30177, is stocked by the General Store Saleswoman for
    # 100 Yang. The item shop, the turning coin, the Dragon Marks and the
    # Storekeeper pickup all describe a shop this server does not run.
    (
        TRANSLATE,
        "Oil. The General Store Saleswoman keeps a bottle",
        'gameforge.new_quest_premium_lv4._180_say = "Ahh, Lilac! Now I just need some'
        " unusual Musk[ENTER]Oil. You can only order it in a very special[ENTER]shop. A"
        " turning coin will show you the way. You[ENTER]will need some Dragon Marks, but"
        " I'm sure you've[ENTER]got some stashed away, haven't you? I would be"
        "[ENTER]eternally grateful! Afterwards, you can pick up[ENTER]your order from the"
        ' Storekeeper. Then I would[ENTER]finally have all the ingredients! "',
        'gameforge.new_quest_premium_lv4._180_say = "Ahh, Lilac! Now I just need some'
        " unusual Musk[ENTER]Oil. The General Store Saleswoman keeps a bottle"
        "[ENTER]behind her counter and asks barely a hundred Yang[ENTER]for it. Bring me"
        " one and I would be eternally[ENTER]grateful! Then I would finally have all"
        ' the[ENTER]ingredients! "',
    ),
    (
        TRANSLATE,
        "Oil. Please buy another bottle from the General",
        'gameforge.new_quest_premium_lv4._280_say = "I can\'t produce my fragrance'
        " without the Musk[ENTER]Oil. Please let yourself be led by the turning"
        '[ENTER]coin and get me another specimen. "',
        'gameforge.new_quest_premium_lv4._280_say = "I can\'t produce my fragrance'
        " without the Musk[ENTER]Oil. Please buy another bottle from the General"
        '[ENTER]Store Saleswoman and bring it to me. "',
    ),
    (
        TRANSLATE,
        "Dealer. I can buy it from the General Store",
        'gameforge.new_quest_premium_lv4._300_say = "I need to take some Musk Oil to the'
        " Weapon Shop[ENTER]Dealer. I'm supposed to let myself be led by the[ENTER]turning"
        ' coin, order an unusual fragrance oil and[ENTER]pick it up from the Storekeeper. "',
        'gameforge.new_quest_premium_lv4._300_say = "I need to take some Musk Oil to the'
        " Weapon Shop[ENTER]Dealer. I can buy it from the General Store"
        '[ENTER]Saleswoman for about a hundred Yang. "',
    ),
    (
        TRANSLATE,
        "ride, and I have one right here. Don't forget",
        'gameforge.pony_buy._200_say = "You have finished the test successfully, very'
        "[ENTER]good. You need a Horse Picture to be able to[ENTER]ride. It will take some"
        " time before I can make[ENTER]you one. Come back tomorrow. Don't forget that"
        '[ENTER]the Horse Picture costs 100,000 Yang! "',
        'gameforge.pony_buy._200_say = "You have finished the test successfully, very'
        "[ENTER]good. You need a Horse Picture to be able to[ENTER]ride, and I have one"
        " right here. Don't forget[ENTER]that the Horse Picture costs 100,000 Yang! \"",
    ),
]


def main():
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

    print("\n%d edit(s) applied, %d already in place." % (changed, skipped))


if __name__ == "__main__":
    main()
