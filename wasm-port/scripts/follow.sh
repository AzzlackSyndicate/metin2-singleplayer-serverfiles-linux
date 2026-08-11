#!/bin/sh
# One stream of each pattern, byte for byte. The first divergence is the bug.
# Stream 4 = the quiet one (26 bytes out), stream 6 = the talkative one.
for s in 4 6; do
    echo "==================== Strom $s ===================="
    tshark -r /tmp/auth.pcap -q -z "follow,tcp,hex,$s" 2>/dev/null | head -45
    echo
done
