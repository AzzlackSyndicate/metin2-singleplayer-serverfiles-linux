#!/usr/bin/env python3
"""High Risk -- a permanently switchable free-PvP mode, offered at level 15.

WHAT THE MODE IS

A character that has reached level 15 may choose High Risk. After that it is
free game: anybody may attack and kill it, its own empire included, and nobody
is punished for doing so. In exchange it earns 50% more experience and finds
50% more drops, and when it dies it drops items the way a Cruel character does
-- the bottom band of the stock alignment table, bags and worn equipment alike,
with the stock quantities and the stock protections. No Risk is the ordinary
game and is what a character has until it says otherwise. Either choice can be
reversed at any time by talking to a Guardian or a City Guard.

WHY IT IS SPLIT THE WAY IT IS

The choice lives in a quest flag written by a new quest file, `high_risk.quest'.
What the flag MEANS is in the game core. That split is not a preference, it is
the only shape this source allows, and the three reasons are worth writing down
because each of them was the obvious approach first:

  * THE DROP-ON-DEATH RULE CANNOT BE A QUEST. There is no `dead' event in this
    quest engine -- the parser knows click, kill, timer, levelup, login, logout,
    button, info, chat, in, out, use, server_timer, enter, leave, letter, take,
    target, party_kill, unmount, pick, sig_use and item_informer, and nothing
    else (questmanager.cpp, CQuestManager::Initialize). Worse, qc does not
    validate event names at all, so `when dead begin ... end' compiles cleanly
    and is then never called: a silent no-op that looks exactly like working
    code.

  * THE BONUSES CANNOT BE AFFECTS. affect.add() ignores which affect type you
    would like and always uses AFFECT_QUEST_START_IDX -- 1000 -- keying only the
    apply within it (questlua_affect.cpp). affect.get_apply_on() can only ask
    about a type, not about a type and an apply. The server-wide movement-speed
    quest already owns type 1000 and decides whether to re-apply itself by
    asking `affect.get_apply_on(1000) == nil'; a second quest affect would
    answer that question wrongly and speed_boost.quest would stop re-applying
    the movement bonus after every death. The two would fight, and the
    movement bonus would lose.

  * POINT_MALL_ITEMBONUS IS NOT WIRED TO ANYTHING. It is the obvious lever for
    "+50% drop" and the item mall's own point type, but the two lines in
    ITEM_MANAGER::GetDropPct that would read it are commented out in this
    source. Setting it would raise a number in the character window and change
    no drop at all. POINT_MALL_EXPBONUS, its neighbour, IS wired up -- but
    granting it means a PointChange that persists in the player row, which is a
    worse thing to leave behind than a flag.

WHAT CARRIES THE PvP HALF -- AND THE VISIBILITY, FOR FREE

A High Risk character wears the killer flag permanently, and that one fact does
almost everything the specification asks for, using only stock code:

  * CPVPManager::CanAttack returns true the moment it sees
    `pkVictim->IsKillerMode()', before empire, guild or PK mode are consulted.
    So a High Risk character is attackable by anyone, including its own empire.
    pvp.cpp is not touched at all.
  * CHARACTER::Dead skips the attacker's alignment penalty when the victim was
    a killer, so killing a High Risk character costs the killer nothing. Again
    stock, again untouched.
  * The flag travels to every nearby client in the character packet's
    bStateFlag (ADD_CHARACTER_STATE_KILLER, set in m_bAddChrState and sent from
    char.cpp), so a High Risk character is DRAWN the way a player-killer is
    drawn -- to itself and to everyone around it. That is the answer to "make
    it evident to the player": it is the one marker in this protocol that the
    server can turn on unilaterally and that everybody can see, and it means
    exactly the right thing.

Two stock safety nets survive untouched and are worth knowing about: a victim
in PK_MODE_PROTECT on its own empire's map is still protected, which keeps
under-15s and GMs out of this entirely, and players cannot select PROTECT
themselves (do_pkmode refuses it), so nobody can hide behind it. Town safe
zones (ATTR_BANPK) also still protect, in battle_is_attackable, above all of
this -- "anywhere" in the specification stops at the safe zone, deliberately:
killing inside one would break every shop and vendor standing in it.

The flag is cleared on death and expires on its own after 30 seconds, and there
is no death event to catch either. It is re-armed in UpdateKillerMode, which
the recovery event already calls for every player about every three seconds, so
it comes back within one cycle without a timer of its own -- and it drops away
within one cycle when the player switches to No Risk.

WHAT THIS SCRIPT CHANGES

The core, five hunks, all keyed off one inline helper so that removing the
feature is removing one header and five short blocks:

  game/src/server/game/src/high_risk.h     NEW. IsHighRiskMode() and the three
                                           named constants. Nothing else.
  char_battle.cpp  UpdateKillerMode()      keeps the killer flag lit
  char_battle.cpp  ItemDropPenalty()       forces the Cruel band, and lets the
                                           mode past the level-50 gate
  char_battle.cpp  the experience path     +50%
  item_manager.cpp GetDropPct()            +50%

The data side:

  files/high_risk.quest                    NEW. The flag, the offer at 15, and
                                           the Guardian's menu.
  prepare-context.sh                       stages it into the game context
  game/Dockerfile                          compiles it and lists it in
                                           locale_list, mirroring speed_boost

The core change is INERT without the quest: no quest, no flag, and all five
checks fall through to stock behaviour. Deleting high_risk.quest -- or setting
M2_HIGH_RISK=0 before prepare-context.sh runs -- turns the mode off completely
without touching a line of C++.

WHY THIS IS A SCRIPT AND NOT AN EDIT

game/src is deleted and re-staged from the porting tree every time
prepare-context.sh runs, so a hand-edited char_battle.cpp is silently reverted
on the next reassembly. Re-run this script after prepare-context.sh -- or run
it against the porting tree itself with --server, so that the staging carries
the change forward.

Idempotent. A second run prints `already patched' for everything it finds done.

    python3 add_high_risk_mode.py
    python3 add_high_risk_mode.py --server /opt/m2port/port40250 --repo ../..

NOTHING IS BUILT OR RESTARTED HERE. The core must be recompiled and the game
image rebuilt for any of it to take effect; the quest is compiled by qc inside
the image build.
"""
import argparse
import io
import os
import sys

DEFAULT_SERVER = r"C:\Users\hatip\Metin2Server"

# The repository this script lives in: <repo>/wasm-port/scripts/<this file>
DEFAULT_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# The new header. Everything the mode means to the core is reachable from here,
# so that "what does High Risk do" and "how do I take it out again" have one
# answer each.
# ---------------------------------------------------------------------------
HIGH_RISK_H = '''#ifndef __INC_METIN_II_HIGH_RISK_H__
#define __INC_METIN_II_HIGH_RISK_H__

// High Risk -- an optional, permanently switchable free-PvP mode.
//
// A character that chose High Risk is free game TO OTHER CHARACTERS WHO ALSO
// CHOSE IT: any of them may attack and kill it, its own empire and its own guild
// included, and nobody is punished for doing so. Against a character that did
// NOT choose it there is no fight in either direction -- it cannot be killed by
// one and it cannot hunt one. The mode is a pool of people who consented, not a
// licence over people who did not. In exchange its members earn
// HIGH_RISK_EXP_BONUS_PCT more experience and find HIGH_RISK_DROP_BONUS_PCT more
// drops, and when one dies it drops items as a Cruel character does.
//
// Guild wars, castle sieges, duels and the arena are all settled in
// battle_is_attackable ABOVE CPVPManager::CanAttack, so they keep working
// between a High Risk and a No Risk player. Those are consensual by their own
// rules; this mode only governs the open world.
//
// The choice itself belongs to the player and lives in a quest flag written by
// share/locale/<lang>/quest/high_risk.quest. It is read here rather than
// mirrored onto CHARACTER because the quest flag is already the persistent,
// per-character, database-backed store for exactly this kind of decision --
// duplicating it would mean keeping two copies honest across login, logout,
// character deletion and the panel, for no gain.
//
// Everything below is inert on a server without that quest file: no character
// can set the flag, GetFlag returns 0 for every one of them, and every caller
// falls through to stock behaviour. That is deliberate -- this feature has to
// be removable without unpicking the core.
//
// The places that ask:
//
//   pvp.cpp          CPVPManager::CanAttack()       the pool rule: a High Risk
//                                                   and a No Risk character
//                                                   never fight, either way
//   char_battle.cpp  CHARACTER::UpdateKillerMode()  keeps the killer flag lit,
//                                                   which is what lets two of
//                                                   them reach each other and
//                                                   what MARKS them, server
//                                                   side, in every stock client
//   char_battle.cpp  CHARACTER::ItemDropPenalty()   the Cruel drop band, and
//                                                   the level-50 exemption
//   char_battle.cpp  the experience award           the experience bonus
//   item_manager.cpp ITEM_MANAGER::GetDropPct()     the drop bonus

#include "char.h"
#include "questmanager.h"

// Quest flags are namespaced by the quest they were written from:
// pc.setqf("mode", 1) inside `quest high_risk' stores "high_risk.mode"
// (questlua_pc.cpp writes GetCurrentQuestName() + "." + name). Renaming the
// quest file renames this flag, and the mode would silently switch itself off
// for every character that had it.
#define HIGH_RISK_QUEST_FLAG	"high_risk.mode"

enum EHighRisk
{
	// Both are percentages added on top of whatever the character had earned
	// anyway, not replacements for it.
	HIGH_RISK_EXP_BONUS_PCT		= 50,
	HIGH_RISK_DROP_BONUS_PCT	= 50,

	// The last row of aItemDropPenalty_kor in char_battle.cpp: the band a
	// character below -120000 alignment falls into -- Cruel. It is the harshest
	// row in the table and the one the mode borrows wholesale, so that the
	// quantities, the anti-drop item flags and the drop-everything-when-the-bags-
	// are-empty case all stay exactly as they are for a genuinely cruel player.
	HIGH_RISK_ALIGN_INDEX_CRUEL	= 8,

	// How often a death actually costs something. The Cruel band the mode
	// borrows is rolled as `pct >= number(1, 1000)' for the bags and
	// `pct >= number(1, 100)' for what is worn, so its shipped 100 and 20 mean
	// 10% and 20%. Those were judged too mild for a mode a player opts into for
	// a reward, so High Risk -- and ONLY High Risk -- uses the two below
	// instead. A genuinely Cruel character is untouched by this and still drops
	// on the shipped odds; the quantities (8 from the bags, 1 worn) come from
	// the Cruel row for both, because only the frequency was in question.
	HIGH_RISK_INVENTORY_PCT		= 500,	// of 1000 -> 50%
	HIGH_RISK_EQUIPMENT_PCT		= 10,	// of  100 -> 10%
};

inline bool IsHighRiskMode(LPCHARACTER ch)
{
	if (!ch || !ch->IsPC())
		return false;

	// GetPCForce and not GetPC: GetPC reassigns the quest manager's current PC
	// and current character as a side effect, which is fine from inside a quest
	// and ruinous from the middle of the combat code -- it would rewrite the
	// context of whatever quest happened to be running. GetPCForce is the
	// accessor that promises not to (questmanager.h).
	quest::PC * pPC = quest::CQuestManager::instance().GetPCForce(ch->GetPlayerID());

	if (!pPC)
		return false;

	return pPC->GetFlag(HIGH_RISK_QUEST_FLAG) != 0;
}

#endif
'''


# ---------------------------------------------------------------------------
# The quest. It owns the flag and the two ways a player sets it, and nothing
# else. See the header comment inside the file for why it owns so little.
# ---------------------------------------------------------------------------
HIGH_RISK_QUEST = '''-- =============================================================
-- High Risk -- an optional, permanently switchable free-PvP mode.
--
-- WHAT IT IS
--
-- From level 15 a character may choose High Risk. It is free game
-- after that: anyone may attack and kill it, its own empire
-- included, and nobody is punished for doing so. In exchange it
-- earns 50% more experience and finds 50% more drops, and when it
-- dies it drops items the way a Cruel character does -- out of the
-- bags and off the body, with the stock quantities and the stock
-- protections. No Risk is the ordinary game and is what a
-- character has until it says otherwise. Either choice can be
-- reversed at any time at a Guardian or a City Guard.
--
-- WHAT THIS FILE OWNS, AND WHAT IT DOES NOT
--
-- One quest flag, `high_risk.mode', and the two ways a player sets
-- it: the offer at level 15 and the Guardian's menu. Everything
-- the flag then MEANS is in the game core -- see high_risk.h and
-- the four places that include it. The split is not a compromise:
--
--   * There is no `dead' event in this quest engine, so the
--     drop-on-death rule could not be written here at all. Worse,
--     qc does not check event names, so `when dead' would compile
--     and then never fire.
--   * affect.add() is hard-wired to affect type 1000
--     (AFFECT_QUEST_START_IDX) whatever apply is passed to it, and
--     affect.get_apply_on() can only ask about a type. The
--     server-wide movement-speed quest already owns that type and
--     decides whether to re-apply itself by asking whether type
--     1000 is present; a second quest affect would answer that
--     question wrongly and the movement bonus would stop coming
--     back after death. So the experience and drop bonuses cannot
--     be affects, and the core reads them off this flag instead.
--
-- Deleting this file switches the mode off completely: no
-- character can set the flag again and every check in the core
-- falls through to stock behaviour.
--
-- HOW A PLAYER KNOWS WHICH MODE THEY ARE IN
--
--   * A High Risk character wears the killer flag permanently, so
--     it is drawn the way a player-killer is drawn -- to itself
--     and to everyone around it. That is the core's doing
--     (CHARACTER::UpdateKillerMode); it is named here because it
--     is the main answer to this question.
--   * A line in the chat window at every login, while the mode is
--     on.
--   * A line at every switch, and the Guardian's menu opens by
--     stating the mode the character is in right now.
--
-- WHY THE OFFER RUNS IN A TIMER AND NOT IN THE EVENT ITSELF
--
-- A dialogue opened straight out of a `login' or `levelup' handler
-- races the client's own start-up, and no stock quest in this tree
-- does it -- they all go through a letter the player clicks.
-- Timers are the exception: dragon_lair_weekly and
-- main_quest_lv66 both open dialogues from a player timer. Three
-- seconds after the level-up, or twenty after a login, is late
-- enough that there is a window to draw into.
--
-- WHY THERE ARE TWO FLAGS AND NOT ONE
--
-- `mode' is the choice; `asked' records that the offer has been
-- made. They cannot be one flag, because pc.setqf(name, 0) DELETES
-- the flag and pc.getqf then returns 0 -- so "chose No Risk" and
-- "has never been asked" would be the same value.
-- =============================================================
quest high_risk begin
	state start begin

		function offer()
			say_title("Risk mode")

			if pc.getqf("mode") == 1 then
				say("You are living in High Risk.")
			else
				say("You are living in No Risk.")
			end

			say("")
			say("High Risk: anyone may kill you, anywhere, your own")
			say("empire included, and they pay no price for it. In")
			say("exchange, 50% more experience and 50% more drops --")
			say("and you drop items when you die, as the cruellest do.")
			say("")
			say("No Risk: the ordinary rules.")

			local answer = select("High Risk", "No Risk")

			if answer == 1 then
				pc.setqf("mode", 1)
				say_title("High Risk")
				say("Then you are free game, and it shows. Any Guardian")
				say("or City Guard will take it back.")
				syschat("[Risk] High Risk: +50% experience, +50% drops. Anyone may kill you, your own empire included, and you drop items when they do.")
			else
				pc.setqf("mode", 0)
				say_title("No Risk")
				say("The ordinary rules, then. Come back if you change")
				say("your mind.")
				syschat("[Risk] No Risk: the ordinary rules apply.")
			end
		end

		when login begin
			if pc.getqf("mode") == 1 then
				syschat("[Risk] You are in High Risk: +50% experience, +50% drops, and anyone may kill you. Any Guardian or City Guard will change it.")
			end

			-- Characters that were already past level 15 when the mode
			-- arrived have never seen the offer. They get it here.
			if pc.get_level() >= 15 and pc.getqf("asked") == 0 then
				timer("high_risk_offer", 20)
			end
		end

		when levelup with pc.get_level() >= 15 and pc.getqf("asked") == 0 begin
			timer("high_risk_offer", 3)
		end

		-- `asked' is set before the dialogue opens, not after it is
		-- answered: a player who walks away from the window has still
		-- been offered the choice, and the Guardian is there for them.
		-- Testing it again here also covers the case where a login and
		-- a level-up both armed the timer.
		when high_risk_offer.timer with pc.getqf("asked") == 0 begin
			pc.setqf("asked", 1)
			high_risk.offer()
		end

		-- The town-square Guardian of each empire -- 11000 Shinsoo,
		-- 11002 Chunjo, 11004 Jinno -- and the City Guard that stands
		-- in all three capitals, 20354. The 111xx range carries the
		-- same names and the same models but is TYPE=MONSTER, so those
		-- vnums have no chat menu to hang this on.
		when 11000.chat."Risk mode" or 11002.chat."Risk mode" or 11004.chat."Risk mode" or 20354.chat."Risk mode" begin
			if pc.get_level() < 15 then
				say_title("Risk mode")
				say("That is a choice for grown men and women. Come back")
				say("when you have reached level 15.")
			else
				high_risk.offer()
			end
		end
	end
end
'''


# ---------------------------------------------------------------------------
# (relative path, marker present only AFTER patching, old text, new text)
#
# The marker is spelled out rather than taken from the first line of the
# replacement: several of these replacements begin with an unchanged line, and
# using that would report an unpatched file as already done.
# ---------------------------------------------------------------------------
SERVER_EDITS = [
    # -- the killer flag, which carries the PvP half and the visibility -------
    (
        "game/src/server/game/src/char_battle.cpp",
        "IsHighRiskMode",
        '''#include "questmanager.h"
#include "questlua.h"
''',
        '''#include "questmanager.h"
#include "questlua.h"
#include "high_risk.h"
''',
    ),
    (
        "game/src/server/game/src/char_battle.cpp",
        "// A High Risk character wears the killer flag permanently",
        '''void CHARACTER::UpdateKillerMode()
{
	if (!IsKillerMode())
		return;
''',
        '''void CHARACTER::UpdateKillerMode()
{
	// A High Risk character wears the killer flag permanently, and that single
	// fact carries most of the mode. CPVPManager::CanAttack returns true the
	// moment it sees IsKillerMode() on the victim, before empire, guild or PK
	// mode are consulted -- which is how two High Risk players reach each other
	// anywhere, their own empire and guild included. The rule that they may
	// reach ONLY each other is the one edit this mode makes to pvp.cpp, a few
	// lines above that shortcut. Dead() then skips the attacker's alignment
	// penalty for the same reason, so killing one costs nothing. And the flag
	// travels to every nearby client in bStateFlag, which is what marks the
	// mode: server side, in every stock client, with no client change at all --
	// it is drawn the way a player-killer is drawn, to the player and to
	// everybody around them.
	//
	// This is also where it is re-armed. The flag is cleared on death and
	// expires by itself after thirty seconds, and this source has no death event
	// in the quest engine to catch either -- but this function is called from
	// the recovery event, which runs for every player about every three seconds,
	// so it returns within one cycle without a timer of its own. It falls away
	// just as quickly when the player switches back to No Risk.
	if (IsHighRiskMode(this))
	{
		if (!IsKillerMode())
			SetKillerMode(true);

		// The killer flag only settles the VICTIM side: it is what lets other
		// High Risk players reach this one. Who this character may hit is still
		// decided by its own PK mode further down CanAttack, and that mode is
		// PK_MODE_PEACE unless the player went and changed it -- so without the
		// line below the mode reads "everyone in the pool may hit you, you may
		// hit nobody", which is not a mode anyone would choose.
		//
		// Forcing it here rather than once, when the player opts in, is
		// deliberate: this runs from the recovery event every few seconds, so
		// the mode also survives the places that reset it behind the player's
		// back -- SetLevel() puts every character back to PEACE on the way out
		// of PROTECT, and the arena hands both fighters PEACE when a duel ends.
		// A one-shot assignment would be silently undone by either.
		//
		// The two guards are the game's own rules, not new ones. A game master
		// is held in PK_MODE_PROTECT by SetLevel() and must stay there, and
		// do_pkmode already refuses anything but PEACE below PK_PROTECT_LEVEL --
		// so this must not hand out FREE where a player could not ask for it.
		// SetPKMode() itself is free to call repeatedly: it returns immediately
		// when the mode is unchanged, and sends the update packet when it is
		// not, which is what moves the combat-mode selector in the client.
		if (GetGMLevel() == GM_PLAYER &&
				GetLevel() >= PK_PROTECT_LEVEL &&
				GetPKMode() != PK_MODE_FREE)
			SetPKMode(PK_MODE_FREE);

		return;
	}

	if (!IsKillerMode())
		return;
''',
    ),
    # -- the drop-on-death penalty -------------------------------------------
    (
        "game/src/server/game/src/char_battle.cpp",
        "// A High Risk character is past this gate",
        '''	if (false == LC_IsYMIR())
	{
		if (GetLevel() < 50)
			return;
	}
''',
        '''	if (false == LC_IsYMIR())
	{
		// A High Risk character is past this gate at any level it can reach the
		// mode at. Outside Korea nobody below 50 drops anything on death, which
		// would leave every High Risk character between 15 and 49 with the whole
		// reward of the mode and none of its price -- the one thing the mode
		// must not be. The stock floor of 10 a few lines down still applies to
		// everybody.
		if (GetLevel() < 50 && !IsHighRiskMode(this))
			return;
	}
''',
    ),
    (
        "game/src/server/game/src/char_battle.cpp",
        "HIGH_RISK_ALIGN_INDEX_CRUEL",
        '''	else
		iAlignIndex = 8;

	std::vector<std::pair<LPITEM, int> > vec_item;
''',
        '''	else
		iAlignIndex = 8;

	// A High Risk character drops as though it stood at the bottom of the
	// alignment table -- the Cruel band -- whatever its real alignment says, and
	// its real alignment is left alone. Borrowing the band rather than inventing
	// a rule is the whole point: the quantities, the anti-drop item flags and
	// the drop-everything-when-the-bags-are-empty case then behave for a High
	// Risk character exactly as they do for a genuinely cruel one, which is what
	// was asked for.
	if (IsHighRiskMode(this))
		iAlignIndex = HIGH_RISK_ALIGN_INDEX_CRUEL;

	std::vector<std::pair<LPITEM, int> > vec_item;
''',
    ),
    # -- killing inside the pool costs no reputation ---------------------------
    # This already held by accident: the penalty is skipped when the victim wears
    # the killer flag, and a High Risk victim wears it permanently. Relying on
    # that is too delicate to leave alone, because the flag BLINKS -- Dead()
    # clears it a few lines below this test and UpdateKillerMode only puts it
    # back on the next recovery tick, and there is a window right after a player
    # opts in where it has not been lit yet. A death inside either window would
    # quietly cost the killer 20,000 alignment for a kill the mode says is free.
    # Asking the mode directly has no such window.
    (
        "game/src/server/game/src/char_battle.cpp",
        "IsHighRiskMode(this) &&",
        "\t\t\t\tif (!isAgreedPVP && !isUnderGuildWar && !IsKillerMode() && GetAlignment() >= 0 && !isDuel && !isForked)\n",

        "\t\t\t\t// !IsHighRiskMode(this) is not redundant beside !IsKillerMode():\n"
        "\t\t\t\t// the flag it tests is cleared further down this same function\n"
        "\t\t\t\t// and only re-lit on the next recovery tick, so it is briefly\n"
        "\t\t\t\t// false for a character that is still very much in the mode.\n"
        "\t\t\t\t// Killing inside the pool must never cost reputation, so the\n"
        "\t\t\t\t// mode is asked directly rather than through its symptom.\n"
        "\t\t\t\tif (!isAgreedPVP && !isUnderGuildWar && !IsKillerMode() && !IsHighRiskMode(this) && GetAlignment() >= 0 && !isDuel && !isForked)\n",
    ),
    # -- High Risk is a POOL, not a licence ------------------------------------
    # The mode used to make its wearer attackable by everybody, which meant a No
    # Risk player could kill one and take the items, and a High Risk player could
    # hunt people who never opted in. Both were wrong. One line fixes both
    # directions: if exactly one of the two chose the mode, they never fight.
    #
    # Nothing else is needed. Two High Risk characters still reach each other
    # through the killer flag the mode already lights permanently -- the
    # `pkVictim->IsKillerMode()' shortcut further down -- which sits below the
    # party check, so party members still cannot hit each other. Two No Risk
    # characters never see this at all and behave exactly as the game shipped.
    #
    # PLACEMENT IS LOAD-BEARING, twice over:
    #   * it goes AFTER `if (pkVictim->IsNPC() || pkChr->IsNPC()) return true;'
    #     -- a monster is not in High Risk, so a gate placed above that line
    #     would read mob-versus-player as a mismatch and make every High Risk
    #     character immune to monsters.
    #   * it goes in CPVPManager::CanAttack and not in battle_is_attackable,
    #     because guild war, castle sieges, the arena and duels are all decided
    #     ABOVE this call (battle.cpp) and must keep working between a High Risk
    #     and a No Risk player. Those are consensual; this rule is about the
    #     open world, where consent is exactly what the mode encodes.
    (
        "game/src/server/game/src/pvp.cpp",
        "IsHighRiskMode",
        '''#include "sectree_manager.h"''',
        '''#include "sectree_manager.h"
#include "high_risk.h"''',
    ),
    (
        "game/src/server/game/src/pvp.cpp",
        # Must be a phrase that survives on ONE line of the replacement: a
        # marker split by a comment wrap is never found again, and the second
        # run then tries to re-apply an anchor that is already gone.
        "High Risk only ever pairs with High Risk",
        '''	if (pkVictim->IsObserverMode() || pkChr->IsObserverMode())
		return false;

	{
		BYTE bMapEmpire = SECTREE_MANAGER::instance().GetEmpireFromMapIndex(pkChr->GetMapIndex());
''',
        '''	if (pkVictim->IsObserverMode() || pkChr->IsObserverMode())
		return false;

	// High Risk only ever pairs with High Risk. If exactly one of the two chose
	// High Risk they cannot touch each other, in either direction: a No Risk
	// player cannot kill one for the items it drops, and a High Risk player
	// cannot grief somebody who never opted in. Both of them chose, or neither
	// fights.
	//
	// This is deliberately above the empire test below, which returns true for
	// any two players of different empires. Without that ordering the rule would
	// hold inside an empire and quietly fail between them, which is where most
	// open-world killing actually happens.
	if (IsHighRiskMode(pkChr) != IsHighRiskMode(pkVictim))
		return false;

	{
		BYTE bMapEmpire = SECTREE_MANAGER::instance().GetEmpireFromMapIndex(pkChr->GetMapIndex());
''',
    ),
    # -- how OFTEN a High Risk death costs something ---------------------------
    # Written with explicit \t escapes rather than as a block string: the two
    # lines being replaced are tab-indented, and a block string that picked up
    # spaces instead would fail the anchor check at build time on somebody
    # else's machine rather than here.
    (
        "game/src/server/game/src/char_battle.cpp",
        "HIGH_RISK_INVENTORY_PCT",
        "\tTItemDropPenalty & r = table[iAlignIndex];\n"
        "\tsys_log(0, \"%s align %d inven_pct %d equip_pct %d\", GetName(), iAlignIndex, r.iInventoryPct, r.iEquipmentPct);\n"
        "\n"
        "\tbool bDropInventory = r.iInventoryPct >= number(1, 1000);\n"
        "\tbool bDropEquipment = r.iEquipmentPct >= number(1, 100);\n",

        "\tTItemDropPenalty & r = table[iAlignIndex];\n"
        "\n"
        "\t// High Risk rolls on its OWN odds, not the Cruel row's -- see high_risk.h\n"
        "\t// for the two numbers and why they are not the shipped ones. Only the\n"
        "\t// frequency is replaced here: the quantities further down still come from\n"
        "\t// the band, and a character that is genuinely Cruel still rolls exactly as\n"
        "\t// it always did.\n"
        "\tconst int iInvPct = IsHighRiskMode(this) ? (int) HIGH_RISK_INVENTORY_PCT : r.iInventoryPct;\n"
        "\tconst int iEqpPct = IsHighRiskMode(this) ? (int) HIGH_RISK_EQUIPMENT_PCT : r.iEquipmentPct;\n"
        "\n"
        "\t// The EFFECTIVE odds are logged, not the row's, because this line is the\n"
        "\t// only way to tell from outside whether a death was judged High Risk.\n"
        "\tsys_log(0, \"%s align %d inven_pct %d equip_pct %d high_risk %d\", GetName(), iAlignIndex, iInvPct, iEqpPct, IsHighRiskMode(this) ? 1 : 0);\n"
        "\n"
        "\tbool bDropInventory = iInvPct >= number(1, 1000);\n"
        "\tbool bDropEquipment = iEqpPct >= number(1, 100);\n",
    ),
    # -- the experience bonus -------------------------------------------------
    (
        "game/src/server/game/src/char_battle.cpp",
        "HIGH_RISK_EXP_BONUS_PCT",
        '''	iExp += (iExp * to->GetPoint(POINT_MALL_EXPBONUS)/100);
	iExp += (iExp * to->GetPoint(POINT_EXP)/100);
''',
        '''	iExp += (iExp * to->GetPoint(POINT_MALL_EXPBONUS)/100);
	iExp += (iExp * to->GetPoint(POINT_EXP)/100);

	// The experience half of High Risk. It is added here, in the same shape as
	// the bonuses above it, rather than by granting POINT_MALL_EXPBONUS: that
	// point would work, but granting it means a PointChange that persists in the
	// player row, and a mode the player can switch off twice a day has no
	// business leaving anything behind in the character it switched off on.
	if (IsHighRiskMode(to))
		iExp += (iExp * HIGH_RISK_EXP_BONUS_PCT / 100);
''',
    ),
    # -- the drop bonus -------------------------------------------------------
    (
        "game/src/server/game/src/item_manager.cpp",
        "IsHighRiskMode",
        '''#include "item.h"
#include "item_manager.h"
''',
        '''#include "item.h"
#include "item_manager.h"
#include "high_risk.h"
''',
    ),
    (
        "game/src/server/game/src/item_manager.cpp",
        "HIGH_RISK_DROP_BONUS_PCT",
        '''	//if (pkKiller->GetPoint(POINT_MALL_ITEMBONUS) > 0)
	//iDeltaPercent += iDeltaPercent * pkKiller->GetPoint(POINT_MALL_ITEMBONUS) / 100;
''',
        '''	//if (pkKiller->GetPoint(POINT_MALL_ITEMBONUS) > 0)
	//iDeltaPercent += iDeltaPercent * pkKiller->GetPoint(POINT_MALL_ITEMBONUS) / 100;

	// The drop half of High Risk. The two dead lines above are left exactly as
	// they are and are worth reading before touching this: POINT_MALL_ITEMBONUS
	// is the obvious lever for "+50% drop", it is what the item mall would use,
	// and in this source it is wired to nothing at all -- so setting the point
	// would raise a number in the character window and change no drop. The bonus
	// is applied to iDeltaPercent instead, which every common drop, mob drop,
	// drop group, metin stone and quest drop below is scaled by, so +50% here is
	// +50% across the whole table rather than on one path of it.
	if (IsHighRiskMode(pkKiller))
		iDeltaPercent += iDeltaPercent * HIGH_RISK_DROP_BONUS_PCT / 100;
''',
    ),
]


# The build pipeline. prepare-context.sh stages the quest into the game context
# and the Dockerfile's quest stage compiles it; both mirror what speed_boost.quest
# already does, because that path is the one that has been proven to work here.
PIPELINE_EDITS = [
    (
        "prepare-context.sh",
        "M2_HIGH_RISK",
        '''else
  rm -f "$HERE/game/quest/speed_boost.quest"
  info "no movement-speed bonus (M2_MOVE_SPEED_BONUS=0)"
fi
''',
        '''else
  rm -f "$HERE/game/quest/speed_boost.quest"
  info "no movement-speed bonus (M2_MOVE_SPEED_BONUS=0)"
fi

# -----------------------------------------------------------------------------
say "High Risk mode"
# A third quest, staged the same way and for the same reason: it runs inside the
# game cores. It owns the player's choice of mode and nothing else -- what the
# choice MEANS is compiled into the cores (high_risk.h and the four places that
# include it), because this quest engine has no death event and its affect API
# cannot be shared with the movement-speed quest.
#
# Nothing is substituted into the file: the two bonus percentages are named
# constants in high_risk.h, not text in here, because they are read by the core
# and not by the quest.
#
# M2_HIGH_RISK=0 leaves it out. The core change is inert without it -- no
# character can set the flag, so every check falls through to stock behaviour --
# which is what makes the mode removable without rebuilding the source.
if [ "${M2_HIGH_RISK:-1}" = "1" ] && [ -f "$PANEL_SRC/high_risk.quest" ]; then
  cp -a "$PANEL_SRC/high_risk.quest" "$HERE/game/quest/high_risk.quest"
  info "high_risk.quest -> game/quest/  (offered at level 15, switchable at any Guardian)"
elif [ "${M2_HIGH_RISK:-1}" = "1" ]; then
  rm -f "$HERE/game/quest/high_risk.quest"
  info "WARNING: $PANEL_SRC/high_risk.quest not found -- no High Risk mode"
else
  rm -f "$HERE/game/quest/high_risk.quest"
  info "High Risk mode NOT staged (M2_HIGH_RISK=0)"
fi
''',
    ),
    (
        "game/Dockerfile",
        "high_risk.quest",
        '''      if [ -f /quest-add/speed_boost.quest ]; then \\
        sed 's/\\r$//' /quest-add/speed_boost.quest > /tmp/qc-work/speed_boost.quest; \\
        ( cd /tmp/qc-work && /src/server/game/src/quest/qc speed_boost.quest ); \\
        test -s /tmp/qc-work/object/state/speed_boost \\
          || { echo "FATAL: qc produced no state file for speed_boost ($lang)"; \\
               find /tmp/qc-work/object; exit 1; }; \\
        cp -a /tmp/qc-work/speed_boost.quest "$out/speed_boost.quest"; \\
      fi; \\
''',
        '''      if [ -f /quest-add/speed_boost.quest ]; then \\
        sed 's/\\r$//' /quest-add/speed_boost.quest > /tmp/qc-work/speed_boost.quest; \\
        ( cd /tmp/qc-work && /src/server/game/src/quest/qc speed_boost.quest ); \\
        test -s /tmp/qc-work/object/state/speed_boost \\
          || { echo "FATAL: qc produced no state file for speed_boost ($lang)"; \\
               find /tmp/qc-work/object; exit 1; }; \\
        cp -a /tmp/qc-work/speed_boost.quest "$out/speed_boost.quest"; \\
      fi; \\
      if [ -f /quest-add/high_risk.quest ]; then \\
        sed 's/\\r$//' /quest-add/high_risk.quest > /tmp/qc-work/high_risk.quest; \\
        ( cd /tmp/qc-work && /src/server/game/src/quest/qc high_risk.quest ); \\
        test -s /tmp/qc-work/object/state/high_risk \\
          || { echo "FATAL: qc produced no state file for high_risk ($lang)"; \\
               find /tmp/qc-work/object; exit 1; }; \\
        test -d /tmp/qc-work/object/20354/chat \\
          || { echo "FATAL: qc produced no Guardian chat handler for high_risk ($lang) --"; \\
               echo "       that is the only way a player can leave High Risk again"; \\
               find /tmp/qc-work/object; exit 1; }; \\
        cp -a /tmp/qc-work/high_risk.quest "$out/high_risk.quest"; \\
      fi; \\
''',
    ),
    (
        "game/Dockerfile",
        "grep -qx 'high_risk.quest'",
        '''      if [ -f /quest-add/speed_boost.quest ]; then \\
        grep -qx 'speed_boost.quest' "$out/locale_list" \\
          || echo 'speed_boost.quest' >> "$out/locale_list"; \\
      fi; \\
''',
        '''      if [ -f /quest-add/speed_boost.quest ]; then \\
        grep -qx 'speed_boost.quest' "$out/locale_list" \\
          || echo 'speed_boost.quest' >> "$out/locale_list"; \\
      fi; \\
      if [ -f /quest-add/high_risk.quest ]; then \\
        grep -qx 'high_risk.quest' "$out/locale_list" \\
          || echo 'high_risk.quest' >> "$out/locale_list"; \\
      fi; \\
''',
    ),
]


def read(path):
    return io.open(path, encoding="utf-8", errors="surrogateescape", newline="").read()


def write(path, text):
    io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(text)


def apply_edits(root, edits, label):
    """Anchored replacements under `root'. Returns the number of files changed."""
    changed = 0

    for rel, marker, old, new in edits:
        path = os.path.join(root, rel.replace("/", os.sep))

        if not os.path.isfile(path):
            sys.exit("not found: %s (wrong --%s root?)" % (path, label))

        s = read(path)

        if marker in s:
            print("  already patched: %s (%s)" % (rel, marker.strip()[:48]))
            continue

        if s.count(old) != 1:
            sys.exit("anchor found %d times, expected 1, in %s\n"
                     "the source is not the baseline this was written against; "
                     "do not force it" % (s.count(old), path))

        write(path, s.replace(old, new, 1))
        print("  patched: %s" % rel)
        changed += 1

    return changed


def write_new_file(path, content, what):
    """Create or refresh a file this script owns outright."""
    if os.path.isfile(path) and read(path) == content:
        print("  already patched: %s" % what)
        return 0

    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        sys.exit("not found: %s (wrong root?)" % parent)

    write(path, content)
    print("  written: %s" % what)
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--server", default=os.environ.get("M2SERVER", DEFAULT_SERVER),
                    help="the server installation / build context (default: %s)" % DEFAULT_SERVER)
    ap.add_argument("--repo", default=os.environ.get("M2REPO", DEFAULT_REPO),
                    help="the repository root that holds files/ and linux-port/")
    ap.add_argument("--core-only", action="store_true",
                    help="only the C++ half, against --server. This is how the "
                         "BUILD runs it: prepare-context.sh has already staged "
                         "the quest and the pipeline edits are already in the "
                         "repository, so touching either from here would write "
                         "into a checkout during a build.")
    args = ap.parse_args()

    server = os.path.abspath(args.server)

    if args.core_only:
        # The half that was missing from every build. Everything below the
        # marker in main() edits the repository, which is right when a person
        # runs this by hand and wrong when prepare-context.sh runs it.
        if not os.path.isdir(server):
            sys.exit("not found: %s (use --server)" % server)
        print("game core: %s" % server)
        changed = write_new_file(
            os.path.join(server, "game", "src", "server", "game", "src", "high_risk.h"),
            HIGH_RISK_H, "game/src/server/game/src/high_risk.h")
        changed += apply_edits(server, SERVER_EDITS, "server")
        if not changed:
            print("already patched: nothing to do.")
        return

    repo = os.path.abspath(args.repo)

    if not os.path.isdir(server):
        sys.exit("not found: %s (use --server)" % server)
    if not os.path.isdir(os.path.join(repo, "files")):
        sys.exit("not found: %s/files (use --repo)" % repo)

    changed = 0

    print("game core: %s" % server)
    changed += write_new_file(
        os.path.join(server, "game", "src", "server", "game", "src", "high_risk.h"),
        HIGH_RISK_H, "game/src/server/game/src/high_risk.h")
    changed += apply_edits(server, SERVER_EDITS, "server")

    # The quest source lives in the repository, next to the other two quests
    # prepare-context.sh stages; the copy in the build context is what the
    # image actually compiles, and is refreshed here so that a rebuild started
    # without re-running prepare-context.sh still gets it.
    print("quest: %s" % repo)
    changed += write_new_file(os.path.join(repo, "files", "high_risk.quest"),
                              HIGH_RISK_QUEST, "files/high_risk.quest")
    changed += write_new_file(os.path.join(server, "game", "quest", "high_risk.quest"),
                              HIGH_RISK_QUEST, "game/quest/high_risk.quest")

    print("build pipeline:")
    changed += apply_edits(server, PIPELINE_EDITS, "server")
    changed += apply_edits(os.path.join(repo, "linux-port", "docker"),
                           PIPELINE_EDITS, "repo")

    if changed:
        print("\n%d file(s) changed. Nothing was built and nothing was restarted:" % changed)
        print("the game core has to be recompiled and the game image rebuilt")
        print("(the image's quest stage runs qc over high_risk.quest) before any")
        print("of this takes effect.")
    else:
        print("\nalready patched: nothing to do.")


if __name__ == "__main__":
    main()
