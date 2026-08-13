#!/usr/bin/env python3
"""The Horse Medal quests stop making the player wait out real time.

Part of the Custom Experience. This is a balance decision and not a defect,
which is why it sits behind a switch: the waits below are exactly what the
quests were written to do. They are simply a pace that suits a server with
thousands of players rather than one with a handful.

Five quests take the Horse Medal (item 50050), and all five made the player
wait on the clock:

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

WHY THIS TOUCHES quest/object/ AS WELL AS THE .quest SOURCES
------------------------------------------------------------
A .quest file is never read by the server. `qc' compiles it into a tree of tiny
Lua chunks under quest/object/, and THAT is what the cores load at boot. The
image build compiles only the panel's own quests and leaves the 237 stock
quests on the object files they shipped with, so editing a .quest source alone
changes nothing a player can see. Every logic change is therefore applied
twice: once to the source, so the tree stays honest and a future full recompile
reproduces it, and once to the matching object/ chunk, so it takes effect.

Only translate.lua is edited, never translate_de.lua or translate_tr.lua:
questlua.cpp:563 loads exactly one translation file and locale_service.cpp:477
fixes its base path at "locale/english". The other fourteen ship with the pack
and are never opened.

A WARNING THIS FILE PAID FOR
----------------------------
Every replacement below is an EXACT known span of text, asserted to occur
exactly once. An earlier attempt at this work matched "everything up to the
next `end'", hit the `end' of an inner `if' instead of the function's own, and
left a syntactically broken quest behind.

Idempotent. A second run reports `already patched' for every edit.

    M2SHARE=<context>/serverfiles/share python3 remove_horse_medal_timegates.py
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
