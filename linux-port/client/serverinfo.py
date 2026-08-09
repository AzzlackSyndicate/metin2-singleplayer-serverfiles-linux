# Points the [40250] client at a Linux-ported server -- WSL2 for a local
# playtest, or a VPS for a real deployment. This is a TEMPLATE: put your own
# address in SERVER_IP / SERVER_IP_TEST below before using it.
#
# Drop this next to Metin2Release.exe (the client reads the copy NEXT TO the
# .exe, not the one in root/ - a root/serverinfo.py is never read).
#
# Which address to put here:
#
#   WSL2 playtest -- the WSL virtual machine's address, which CHANGES whenever
#   WSL restarts. Re-check it with:
#       wsl -d Ubuntu-24.04 -- ip -4 addr show eth0
#
#   VPS -- the server's public IPv4 address. Use the ADDRESS, not a
#   Cloudflare-proxied hostname: the orange-cloud proxy only forwards HTTP and
#   HTTPS on a fixed port list, so it cannot carry the Metin2 protocol on
#   11000/13000. If you want a hostname here so clients survive a server move,
#   add a second DNS record with the GREY cloud (DNS only) and use that.
SERVER_NAME       = "Metin2 Linux Port (40250)"
SERVER_NAME_TEST  = "Metin2 Linux Port (40250)"
SERVER_IP         = "203.0.113.10"       # <-- CHANGE THIS
SERVER_IP_TEST    = "203.0.113.10"       # <-- CHANGE THIS

CH1_NAME          = "CH1"
CH2_NAME          = "CH2"
CH3_NAME          = "CH3"
CH4_NAME          = "CH4"

PORT_1            = 13000
PORT_2            = 13010
PORT_3            = 13020
PORT_4            = 13030
PORT_AUTH         = 11000
PORT_MARK         = 13000

STATE_NONE = "..."
STATE_DICT = {
	0 : "....",
	1 : "NORM",
	2 : "BUSY",
	3 : "FULL"
}

# Only channel 1 is started in the minimal test layout. Listing channels that
# are not running makes the client look broken when they fail to connect.
SERVER01_CHANNEL_DICT = {
	1:{"key":11,"name":CH1_NAME,"ip":SERVER_IP,"tcp_port":PORT_1,"udp_port":PORT_1,"state":STATE_NONE,},
}

SERVER02_CHANNEL_DICT = SERVER01_CHANNEL_DICT

REGION_NAME_DICT = {
	0 : "",
}

REGION_AUTH_SERVER_DICT = {
	0 : {
		1 : { "ip":SERVER_IP, "port":PORT_AUTH, },
	}
}

REGION_DICT = {
	0 : {
		1 : { "name" : SERVER_NAME, "channel" : SERVER01_CHANNEL_DICT, },
	},
}

MARKADDR_DICT = {
	10 : { "ip" : SERVER_IP, "tcp_port" : PORT_MARK, "mark" : "10.tga", "symbol_path" : "10", },
}

TESTADDR = {"ip":SERVER_IP, "tcp_port":PORT_1, "udp_port":PORT_1}
