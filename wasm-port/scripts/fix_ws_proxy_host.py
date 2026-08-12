#!/usr/bin/env python3
"""Dial the proxy by the NAME the page was opened with, not by 255.255.255.255.

A server reached through a domain was unreachable from the browser client. The
page opened `wss://255.255.255.255/to/<game>:<port>` and every connection
failed; the same server named by its IP address worked.

255.255.255.255 is INADDR_NONE. CNetworkAddress::SetIP() -- which
CNetworkStream::Connect calls on the remap host -- goes through inet_addr(),
and inet_addr parses dotted quads and nothing else. Handed a hostname it
returns -1, GetIP renders that back as 255.255.255.255, and the WebSocket shim
faithfully dials it. mainPosix.cpp accepts a hostname on purpose (its character
class is the DNS set), so the two ends disagreed.

Fixing it in C++ is not possible and would be wrong if it were: there is no
resolver in a browser, and resolving the name would break TLS anyway, because
the certificate is issued for the NAME. So the shim -- which is the last thing
to touch the URL before `new WebSocket` -- takes the host from the page's own
query string, with the same regex and the same character class mainPosix.cpp
reads it with.

Scoped to connections the remap claimed: the rewrite sits inside the test for
`__m2ProxyDest', so the guild-mark connector's zero-port address, which the
remap deliberately leaves alone, keeps the address it was given.

Exercised before shipping, against the real shim rather than a copy of it:

    named proxy behind TLS   ws://255.255.255.255:443/  ->
                             wss://metin2-sp.example.com:443/to/<game>%3A11000
    numeric proxy, no TLS    unchanged behaviour
    no stash (guild mark)    address untouched
    page without serverHost  address untouched

Idempotent. Run it against /opt/m2wasm; a second run reports `already patched'.
Rebuilding after this changes index.js, which is where pre.js ends up.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
SRC = os.path.join(ROOT, "tools/wasm/pre.js")

TLS_BLOCK = """  var TLS = /[?&]serverTLS=1(?:&|$)/.test(
    (typeof location !== 'undefined' && location.search) || '');
"""

HOST_BLOCK = """  // The proxy's host, taken from the page URL rather than from the address the
  // runtime derived. CNetworkAddress::SetIP() goes through inet_addr(), which
  // parses dotted quads and nothing else: handed a NAME it returns INADDR_NONE,
  // so a proxy named rather than numbered reached this shim as
  // ws://255.255.255.255/ and every connection failed. NetStream.cpp cannot fix
  // that itself -- there is no resolver in a browser, and resolving would be
  // wrong anyway, because a wss:// certificate is issued for the NAME.
  //
  // Same regex and same character class as mainPosix.cpp reads it with, so the
  // two cannot disagree about what a host may contain.
  var HOST = (function () {
    var m = /[?&]serverHost=([A-Za-z0-9.\\-]+)/.exec(
      (typeof location !== 'undefined' && location.search) || '');
    return m ? decodeURIComponent(m[1]) : '';
  })();
"""

TLS_UPGRADE = "        if (TLS) url = url.replace(/^ws:\\/\\//i, 'wss://');\n"

REWRITE = """        // Inside the stash test on purpose: a stash means the remap applied and
        // this URL is the proxy's. Connections the remap deliberately leaves
        // alone -- the guild-mark connector's, which carries port 0 -- have no
        // stash and keep the address they were given.
        if (HOST) url = url.replace(/^(wss?:\\/\\/)([^\\/?#]*)/i, function (_, scheme, authority) {
          var port = /:(\\d+)$/.exec(authority);
          return scheme + HOST + (port ? ':' + port[1] : '');
        });
"""


def main():
    if not os.path.isfile(SRC):
        sys.exit("not found: %s (set M2WASM to the client tree)" % SRC)

    s = io.open(SRC, encoding="utf-8", errors="surrogateescape").read()

    if "var HOST = (function ()" in s and "scheme + HOST" in s:
        print("already patched")
        return

    if s.count(TLS_BLOCK) != 1:
        sys.exit("the TLS block was not found unchanged -- refusing to guess where "
                 "HOST belongs")
    if s.count(TLS_UPGRADE) != 1:
        sys.exit("the ws->wss upgrade was not found unchanged -- refusing to guess")

    s = s.replace(TLS_BLOCK, TLS_BLOCK + HOST_BLOCK, 1)
    s = s.replace(TLS_UPGRADE, TLS_UPGRADE + REWRITE, 1)

    io.open(SRC, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)
    print("patched: the shim dials the host named in the page URL")


if __name__ == "__main__":
    main()
