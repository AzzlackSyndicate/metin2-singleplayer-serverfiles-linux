#!/bin/sh
# tshark understands LINUX_SLL2 and can follow a TCP stream, which is exactly
# what is needed: the payload of each direction, in order, duplicates resolved.
set -e
export DEBIAN_FRONTEND=noninteractive
if ! command -v tshark >/dev/null 2>&1; then
    echo "wireshark-common wireshark-common/install-setuid boolean false" | debconf-set-selections
    apt-get install -y -qq tshark >/dev/null 2>&1 || { echo "tshark nicht installierbar"; exit 1; }
fi
echo "=== tshark: $(tshark -v 2>/dev/null | head -1) ==="

echo
echo "=== die Stroeme ==="
tshark -r /tmp/auth.pcap -T fields -e tcp.stream -e tcp.srcport -e tcp.dstport -e frame.time_relative 2>/dev/null \
  | awk '$3==11000 {if(!(seen[$1]++)) printf "  Strom %-3s Quellport %-7s ab t=%.1fs\n", $1, $2, $4}'

echo
echo "=== Nutzbytes je Strom und Richtung ==="
tshark -r /tmp/auth.pcap -T fields -e tcp.stream -e tcp.srcport -e tcp.len 2>/dev/null \
  | awk '$3>0 {if($2==11000) srv[$1]+=$3; else cli[$1]+=$3}
         END {for (s in srv) printf "  Strom %-3s Client->Server %-6s Server->Client %s\n", s, cli[s]+0, srv[s]+0}' \
  | sort -t' ' -k3 -n
