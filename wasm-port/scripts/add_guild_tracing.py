#!/usr/bin/env python3
"""Trace RecvGuild, because a second guild member kills the browser client.

TEMPORARY. Diagnostic, not a fix.

THE FAULT, as established by the operator's own experiment: a character in a
guild of ONE logs in fine. Invite somebody, and from that moment the browser
client freezes -- on accepting, and on every login afterwards. Take the
character out of the guild in the database and it logs in again. The Windows
client is unaffected throughout, with the same account and the same data, so
the server is not at fault and this port is.

The console said `Uncaught RuntimeError: null function' -- a call through a
null pointer, which is an abort, not a stream desync. But that trace was taken
during a window in which the browser was mixing a cached index.js with a fresh
index.wasm, so it is not trustworthy and has to be produced again.

Two things are worth knowing about RecvGuild before reading its output:

  * it reads each sub-payload by STRUCT SIZE and ignores the `size' field the
    packet carries, so a width mismatch would desync the stream rather than be
    contained. The sizes were checked against the server's guild.h and match
    (13 bytes per member, 38 with the name, both packed) -- but the trace
    prints them anyway, because that check was made by reading, not measuring.

  * the member loop only enters the branch for a FOREIGN member when the guild
    has more than one, which is exactly the condition that breaks. Messenger
    entry, name plate and the member page are all reached from there.

Prints the sub-header and declared size of every guild packet, each member as
it is parsed, and whether the loop's byte accounting came out even. The last
line before the abort names the place.

Read it with the console's filter box set to GUILD.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
SRC = os.path.join(ROOT, "src/PyLib/src/bindings/net/PythonNetworkStreamPhaseGame.cpp")

PAIRS = [
    ("""    TPacketGCGuild GuildPacket;
	if (!Recv(sizeof(GuildPacket), &GuildPacket))
		return false;

	switch(GuildPacket.subheader)""",
     """    TPacketGCGuild GuildPacket;
	if (!Recv(sizeof(GuildPacket), &GuildPacket))
		return false;

	SPDLOG_DEBUG("GUILD: sub={} size={} (header struct is {} bytes)",
	             int(GuildPacket.subheader), int(GuildPacket.size), int(sizeof(GuildPacket)));

	switch(GuildPacket.subheader)"""),

    ("""			for (; iPacketSize > 0;)
			{
				TPacketGCGuildSubMember memberPacket;
				if (!Recv(sizeof(memberPacket), &memberPacket))
					return false;
""",
     """			SPDLOG_DEBUG("GUILD: list, {} payload bytes, member struct is {}",
			             iPacketSize, int(sizeof(TPacketGCGuildSubMember)));
			for (; iPacketSize > 0;)
			{
				TPacketGCGuildSubMember memberPacket;
				if (!Recv(sizeof(memberPacket), &memberPacket))
					return false;

				SPDLOG_DEBUG("GUILD:   member pid={} grade={} job={} level={} nameflag={} left={}",
				             memberPacket.pid, int(memberPacket.byGrade), int(memberPacket.byJob),
				             int(memberPacket.byLevel), int(memberPacket.byNameFlag), iPacketSize);
"""),

    ("""			__RefreshGuildWindowInfoPage();
			__RefreshGuildWindowMemberPage();
			__RefreshMessengerWindow();
			__RefreshCharacterWindow();
			break;""",
     """			SPDLOG_DEBUG("GUILD: list done, {} bytes over/under", iPacketSize);
			__RefreshGuildWindowInfoPage();
			__RefreshGuildWindowMemberPage();
			__RefreshMessengerWindow();
			__RefreshCharacterWindow();
			break;"""),
]


def main():
    if not os.path.isfile(SRC):
        sys.exit("not found: %s (set M2WASM to the client tree)" % SRC)

    s = io.open(SRC, encoding="utf-8", errors="surrogateescape").read()
    if "GUILD: sub=" in s:
        print("already patched")
        return

    for old, new in PAIRS:
        if s.count(old) != 1:
            sys.exit("anchor not found exactly once:\n%s" % old[:70])
        s = s.replace(old, new, 1)

    io.open(SRC, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)
    print("patched: RecvGuild traces every sub-packet and every member")


if __name__ == "__main__":
    main()
