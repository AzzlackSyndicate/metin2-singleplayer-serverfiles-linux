#!/usr/bin/env python3
"""500 MB downloaded, 500 MB thrown away, every single visit.

    [webfs] background fill: stopping at the 500 MB budget (122 chunks).
    [webfs] background fill complete: 122 chunks, 503 MB in 35 s

...on the first load, and on the next one, and on the one after that. The
player pays for the same bytes every session and so does the server.

WHAT WAS MEASURED, BEFORE ANYTHING WAS CHANGED
==============================================

The obvious suspect was the Cache-Control header, so that was measured first
rather than reasoned about, on both paths that can serve /play:

    curl -I http://127.0.0.1:7788/play/<hash>.bin
        Cache-Control: public, max-age=31536000, immutable
    curl -I http://127.0.0.1:7788/play/manifest.bin
        Cache-Control: no-store

Both correct. The nginx path agrees -- installer/install.sh builds a $uri map
with the same three rules and gates it on $status so a 404 cannot be kept. The
server is not the bug and files/admin_panel.py was NOT touched.

So the browser is throwing them away. Headless Chrome, one persistent profile,
a tiny page that fetches chunk URLs and discards them, and a server that logs
every request it answers -- the same shape as the background fill:

    130 chunks (548 MB), loaded three times
        run 1: 130 requests   run 2: 0 requests   run 3: 0 requests

Which looks fine, and is the trap. The fill is not the only thing that reads
this corpus: the game reads the rest of it on demand, through fs.fetchSync,
into the same HTTP cache. Replaying that:

    act A  the fill:        chunks   0..129  (548 MB)  -> 130 requests
    act B  a play session:  chunks 130..418 (1198 MB)  -> 289 requests
    act C  the next visit:  chunks   0..129  (548 MB)  -> 130 requests

Every one of the 130 prefetched chunks is gone. Not some -- all of them.

The HTTP cache is not a store, it is a shared least-recently-used pool with a
ceiling. On this machine, with 921 GB free, that ceiling measured at about
1.2 GB: one pass over the 1.75 GB corpus evicts its own beginning before it
reaches its end, and a second pass over the whole corpus re-requested 418 of
419 chunks. The 500 MB the fill downloads would survive alone; it never is
alone. It shares that pool with the game's own on-demand reads and with every
other site the player visits.

THE FIX, AND WHAT IT IS NOT
===========================

Chunks go into Cache Storage -- caches.open(), cache.match(), cache.put().
That is a different pool: it belongs to the origin, it is governed by the
storage quota rather than by an LRU ceiling shared with the whole browser, and
nothing evicts an entry except real storage pressure or the player clearing
site data. Chunk URLs are content-addressed, so an entry can never go stale
and there is no invalidation logic to get wrong.

NOT IndexedDB. The unit of storage here is exactly "a Response for an
immutable URL", which is what Cache Storage stores natively; IndexedDB would
mean a schema, a key convention and hand-written handling of 4 MB
ArrayBuffers, for the same bytes, under the same quota, with nothing gained.

NOT a service worker, although one would be the complete fix: only a worker
can answer the SYNCHRONOUS XHR in fs.fetchSync out of Cache Storage, which
nothing running on the page can do. It is left out deliberately -- a second
file with its own update lifecycle, and a wedged worker serving a stale
index.html is a worse failure than the one being fixed. The sync path keeps
the HTTP cache and is no worse off than it is today, because a tier 2 hit
costs it nothing.

manifest.bin and index.dev stay out of Cache Storage entirely. Cache Storage
ignores Cache-Control -- it stores what it is told to store -- so routing the
mutable root through it would defeat the `no-store' the server sends and make
a patched client load the previous corpus. Only fs.url(idx) goes in.

Two more things fall out of doing it this way:

  * The fill stops decoding what it discards. On a miss the Response is handed
    to the cache unread, so no 4 MB ArrayBuffer is ever created; on a hit
    nothing is fetched at all. The 250 ms pause between chunks exists to give
    the collector room between those buffers, so it is skipped when there was
    nothing to collect -- a returning player's fill finishes in about a second
    instead of ticking through 35.

  * sweepCache() deletes every entry the current manifest does not name. New
    data does not invalidate a content-addressed chunk, it ORPHANS it: the old
    chunk keeps its name and nothing will ever ask for it again. One pass over
    the keys after boot is the whole garbage collector, and it is what stops a
    client that has seen three data versions from storing three corpora.

Every failure path degrades to a plain fetch. No Cache Storage on the origin
(it needs https or localhost, so a LAN address over plain http has none), an
open that fails, a quota that refuses the write -- each one logs a line and
falls back to exactly today's behaviour. None of them can break the client.

HOW TO SEE IT WORK
==================

Load /play/, let the fill finish, reload. The console's ready line names how
many chunks came from the local cache, and the fill's completion line splits
its total into "already stored" and "downloaded". The network tab is the
independent check: 122 chunk requests on the first visit, 0 on the second.

Idempotent. Run against /opt/m2wasm; a second run reports `already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")

REL = "tools/wasm/webfs.js"

# The marker exists only after patching. It is the section title of the new
# block rather than the first line of any replacement, because several of these
# replacements begin with a line that is unchanged.
MARKER = "TIER 2: CACHE STORAGE"

EDITS = [
    # ── the file's own argument for the design, which was measurably wrong ──
    (
        """// ═══════════════════════════════════════════════════════════════════════════════════
// THE CACHE IS TWO TIERS AND THE SECOND ONE IS THE BROWSER'S
// ═══════════════════════════════════════════════════════════════════════════════════
//
// Tier 1 is `blobs`, an LRU of decoded chunks in JS memory, capped (default 96 MB) because
// a browser tab is not a machine with 32 GB of headroom and a wasm32 client is already
// carrying its own 256 MB heap.
//
// Tier 2 is the HTTP cache. Chunk URLs are content-addressed, so they are immutable and
// served with `max-age=31536000, immutable`: evicting from tier 1 costs a request that
// the browser answers from disk without touching the network. That is what makes a small
// tier 1 acceptable, and it is why this file does NOT reach for IndexedDB — that would be
// a third copy of bytes the browser is already storing, with a quota to manage.
""",
        """// ═══════════════════════════════════════════════════════════════════════════════════
// THE CACHE IS THREE TIERS AND THE MIDDLE ONE HAD TO BE BUILT
// ═══════════════════════════════════════════════════════════════════════════════════
//
// Tier 1 is `blobs`, an LRU of decoded chunks in JS memory, capped (default 96 MB) because
// a browser tab is not a machine with 32 GB of headroom and a wasm32 client is already
// carrying its own 256 MB heap.
//
// Tier 3 is the browser's own HTTP cache, and this file used to stop there, on the
// argument that a content-addressed URL served `immutable` is already stored by the
// browser and storing it a second time would only be a quota to manage. The header is
// right — it was measured on both paths that serve /play — and the conclusion was still
// wrong, because the HTTP cache is not a store. It is a shared least-recently-used pool
// with a ceiling: MEASURED at about 1.2 GB on a machine with 921 GB free, so one pass
// over the 1.75 GB corpus evicts its own beginning before it reaches its end. The 500 MB
// the background fill downloads is small enough to survive on its own and never is alone
// — it shares that pool with the chunks the game reads on demand and with every other
// site the player visits. Measured end to end: fill 130 chunks, read the rest of the
// corpus the way a session does, come back, and all 130 are gone.
//
// Tier 2 is therefore Cache Storage, which is a different pool. It belongs to this
// origin, it is governed by the storage quota rather than by a ceiling shared with the
// whole browser, and nothing removes an entry but real storage pressure or the player
// clearing site data. Content-addressed names mean an entry can never go stale, so there
// is no invalidation to write — only garbage to collect, which sweepCache() does.
//
// NOT IndexedDB: the unit of storage here is exactly "a Response for an immutable URL",
// which is what Cache Storage keeps natively. IndexedDB would be a schema, a key
// convention and hand-written handling of 4 MB ArrayBuffers, for the same bytes, under
// the same quota.
//
// NOT a service worker either, though one would be the complete fix — only a worker can
// answer the synchronous XHR in fs.fetchSync out of tier 2, which nothing on the page
// can do. That is a second file with its own update lifecycle, and a wedged worker
// serving a stale index.html is a worse failure than the one being fixed here. The sync
// path keeps tier 3 and is no worse off for any of this.
""",
    ),
    # ── counters, so the operator can read the answer off the console ──
    (
        """    netRequests: 0,
    netBytes: 0,
    evicted: 0,
  };
""",
        """    netRequests: 0,
    netBytes: 0,
    evicted: 0,
    cacheHits: 0,      // chunks tier 2 already had: this is the number that says it works
    cacheWrites: 0,
  };
""",
    ),
    # ── get() hands back the Response, because tier 2 stores Responses ──
    #
    # A body may be read once, so whether it is decoded or cached has to be
    # decided by the caller that knows which it wants.
    (
        """        if (!r.ok) throw new Error(url + ' -> HTTP ' + r.status);
        if (attempt > 0) status('');   // recovered: drop the retry banner
        return r.arrayBuffer();
""",
        """        if (!r.ok) throw new Error(url + ' -> HTTP ' + r.status);
        if (attempt > 0) status('');   // recovered: drop the retry banner
        fs.netRequests++;
        // The RESPONSE, not its bytes: a body is a stream that may be read once, and
        // only the caller knows whether it wants to decode it or hand it to tier 2.
        return r;
""",
    ),
    # ── the caching comment above get(), which claimed the blobs need no help ──
    (
        """  // Plain fetch, DEFAULT cache mode, deliberately. `force-cache` would also serve a stale
  // manifest.bin — the one URL in this deployment that must never be cached, because it is
  // the mutable root that names every chunk. The blobs need no help: their URLs are
  // content-addressed and the server marks them immutable, so the browser reuses them
  // without a revalidation request.
""",
        """  // Plain fetch, DEFAULT cache mode, deliberately. `force-cache` would also serve a stale
  // manifest.bin — the one URL in this deployment that must never be cached, because it is
  // the mutable root that names every chunk. The blobs are marked immutable and reused
  // without a revalidation request for as long as the browser feels like keeping them,
  // which is the part that turned out not to be long enough; loadChunk and ensureChunk
  // below are what makes a downloaded chunk stay downloaded.
""",
    ),
    # ── tier 2 itself ──
    (
        """    return one();
  }

  function parseManifestHeader(u8) {
""",
        """    return one();
  }

  // ── TIER 2: CACHE STORAGE ────────────────────────────────────────────────────────
  //
  // See the header for what was measured and why the HTTP cache alone is not enough.
  // Everything below degrades to a plain fetch when anything goes wrong: a browser that
  // will not store the corpus must still be able to play it, so no failure in here is
  // allowed to reach a caller as a rejection.
  var CACHE_NAME = 'm2-webfs-chunks';
  var cacheOpen  = null;   // Promise<Cache|null>, resolved once and reused
  var cacheWrite = true;   // cleared for the session the first time a write is refused

  function chunkCache() {
    if (cacheOpen) return cacheOpen;
    cacheOpen = new Promise(function (resolve) {
      // file:// mode already has the whole corpus on disk. And `caches` is undefined
      // outside a secure context — which a LAN address over plain http is — so say that
      // out loud: it is one line here and an afternoon of wondering, otherwise, why the
      // same build keeps its data on one host and re-downloads it on another.
      if (localFiles) { resolve(null); return; }
      if (typeof caches === 'undefined' || !caches || !caches.open) {
        console.warn('[webfs] no Cache Storage on this origin (it needs https, or ' +
                     'localhost) — downloaded chunks will last only as long as the ' +
                     "browser's own HTTP cache decides to keep them.");
        resolve(null); return;
      }
      caches.open(CACHE_NAME).then(resolve, function (e) {
        console.warn('[webfs] caches.open failed (' + e + '); running without tier 2');
        resolve(null);
      });
    });
    return cacheOpen;
  }

  // Takes ownership of `resp` and is not waited on. A chunk that fails to persist costs
  // one request on the next visit, which is not worth a rejection anywhere upstream.
  // Quota is the failure worth naming and it is terminal for the session: every later
  // write would fail the same way, after cloning 4 MB to find that out.
  function cacheStore(c, url, resp) {
    c.put(url, resp).then(function () { fs.cacheWrites++; }, function (e) {
      cacheWrite = false;
      console.warn('[webfs] tier 2 refused the write (' + e + ') — the storage quota, ' +
                   'most likely. The rest of this session falls back to the HTTP cache.');
    });
  }

  // The boot set wants the bytes.
  function loadChunk(idx) {
    var url = fs.url(idx);
    return chunkCache().then(function (c) {
      if (!c) return get(url).then(decode);
      return c.match(url).catch(function () { }).then(function (hit) {
        if (hit) { fs.cacheHits++; return hit.arrayBuffer(); }
        return get(url).then(function (r) {
          // clone() before either half is touched: the body is a stream, and the copy
          // has to be taken while it is still unread.
          if (cacheWrite) cacheStore(c, url, r.clone());
          return decode(r);
        });
      });
    });
  }

  // The background fill only needs the chunk to BE here — it throws the bytes away. So a
  // hit fetches nothing, and a miss hands the whole Response to the cache unread, which
  // means no 4 MB ArrayBuffer is ever created for bytes nobody looks at. Resolves true
  // when tier 2 already had the chunk.
  function ensureChunk(idx) {
    var url = fs.url(idx);
    return chunkCache().then(function (c) {
      if (!c) return get(url).then(decode).then(function () { return false; });
      return c.match(url).catch(function () { }).then(function (hit) {
        if (hit) { fs.cacheHits++; return true; }
        return get(url).then(function (r) {
          if (cacheWrite) { fs.netBytes += fs.sizes[idx]; cacheStore(c, url, r); return false; }
          return decode(r).then(function () { return false; });
        });
      });
    });
  }

  function decode(r) {
    return r.arrayBuffer().then(function (b) { fs.netBytes += b.byteLength; return b; });
  }

  // New data does not invalidate a content-addressed chunk, it ORPHANS it: the old chunk
  // keeps its name and nothing will ever ask for it again. One pass over the keys after
  // boot is the entire garbage collector, and it is what stops a client that has seen
  // three data versions from storing three corpora.
  function sweepCache() {
    if (!fs.hashes.length) return;   // no manifest: every entry would look dead
    chunkCache().then(function (c) {
      if (!c) return;
      var live = Object.create(null);
      for (var i = 0; i < fs.hashes.length; ++i) live[fs.hashes[i] + '.bin'] = 1;
      return c.keys().then(function (reqs) {
        var dead = [];
        for (var k = 0; k < reqs.length; ++k) {
          // keys() hands back absolute URLs while fs.url() is relative to the page, so
          // the comparison is on the file name — which is the hash, which is the identity.
          var name = reqs[k].url.split('?')[0].split('/').pop();
          if (!live[name]) dead.push(reqs[k]);
        }
        if (!dead.length) return;
        console.log('[webfs] tier 2: dropping ' + dead.length +
                    ' chunk(s) this data version no longer names');
        return Promise.all(dead.map(function (r) { return c.delete(r); }));
      });
    }).catch(function (e) {
      console.warn('[webfs] tier 2 sweep failed (' + e + ') — harmless, it retries next load');
    });
  }

  function parseManifestHeader(u8) {
""",
    ),
    # ── the fill's own note about where the bytes end up ──
    (
        """  // The bytes are NOT put in tier 1. Storing 1.76 GB through an LRU capped at a few
  // hundred MB would evict exactly what the game is reading right now, and by the end
  // the cache would hold the chunks ranked LEAST useful -- the opposite of the point.
  // Tier 2 is the browser's own cache, the chunks are content-addressed and served
  // `immutable', so a discarded fetch still turns the next read from a network round
  // trip into a disk hit.
""",
        """  // The bytes are NOT put in tier 1. Storing 1.76 GB through an LRU capped at a few
  // hundred MB would evict exactly what the game is reading right now, and by the end
  // the cache would hold the chunks ranked LEAST useful -- the opposite of the point.
  // They go to tier 2 instead, which is where they survive the tab being closed; the
  // fill never decodes them, so on a return visit this loop is a few hundred lookups
  // against local storage rather than half a gigabyte off the wire.
""",
    ),
    # ── the fill: ask tier 2 first, and do not wait for a collector with nothing to do ──
    (
        """    var next = 0, done = 0, bytes = 0;
""",
        """    var next = 0, done = 0, bytes = 0, cached = 0;
""",
    ),
    (
        """    function later(fn) { return new Promise(function (r) { setTimeout(r, PAUSE); }).then(fn); }
""",
        """    function later(fn) { return new Promise(function (r) { setTimeout(r, PAUSE); }).then(fn); }
    // PAUSE buys the collector room between 4 MB network buffers. A chunk tier 2 already
    // had allocated nothing, so it waits for the event loop and nothing else.
    function soon(fn)  { return new Promise(function (r) { setTimeout(r, 0); }).then(fn); }
""",
    ),
    (
        """      var idx = order[next++];
      if (fs.blobs.has(idx)) return later(one);
      return get(fs.url(idx)).then(function (buf) {
        done++; bytes += buf.byteLength;          // discarded on purpose -- see above
        buf = null;
        if (badge) badge.set(bytes / BUDGET, (bytes / 1048576).toFixed(0), budgetMB);
        if (done % 25 === 0)
          console.log('[webfs] background fill: ' + done + '/' + order.length +
                      ' chunks, ' + (bytes / 1048576).toFixed(0) + ' / ' + budgetMB + ' MB');
        return later(one);
      }, function () {
""",
        """      var idx = order[next++];
      if (fs.blobs.has(idx)) return later(one);
      return ensureChunk(idx).then(function (fromCache) {
        done++; bytes += fs.sizes[idx];           // never decoded -- see above
        if (fromCache) cached++;
        if (badge) badge.set(bytes / BUDGET, (bytes / 1048576).toFixed(0), budgetMB);
        if (done % 25 === 0)
          console.log('[webfs] background fill: ' + done + '/' + order.length +
                      ' chunks, ' + (bytes / 1048576).toFixed(0) + ' / ' + budgetMB + ' MB');
        return fromCache ? soon(one) : later(one);
      }, function () {
""",
    ),
    (
        """      console.log('[webfs] background fill complete: ' + done + ' chunks, ' +
                  (bytes / 1048576).toFixed(0) + ' MB in ' + (ms / 1000).toFixed(0) + ' s');
""",
        """      console.log('[webfs] background fill complete: ' + done + ' chunks, ' +
                  (bytes / 1048576).toFixed(0) + ' MB in ' + (ms / 1000).toFixed(0) + ' s (' +
                  cached + ' already stored, ' + (done - cached) + ' downloaded)');
""",
    ),
    # ── the boot set goes through tier 2 too: it is the part the player waits for ──
    (
        """      var idx = next++;
      return get(fs.url(idx)).then(function (buf) {
        var u8 = new Uint8Array(buf);
        fs.store(idx, u8);
        fs.netRequests++;
        fs.netBytes += u8.length;
        done += fs.sizes[idx];
""",
        """      var idx = next++;
      return loadChunk(idx).then(function (buf) {
        var u8 = new Uint8Array(buf);
        fs.store(idx, u8);
        done += fs.sizes[idx];
""",
    ),
    # ── manifest.bin and index.dev: bodies, and deliberately not through tier 2 ──
    (
        """  var loading = get(resolve('manifest.bin'))
    .then(function (buf) {
      fs.manifest = new Uint8Array(buf);
      parseManifestHeader(fs.manifest);
      return get(resolve('index.dev'));
    })
    .then(function (buf) {
      fs.indexBytes = new Uint8Array(buf);
      return prefetchBoot();
    })
""",
        """  // Both of these are fetched, never stored: Cache Storage ignores Cache-Control — it
  // keeps what it is told to keep — so putting the mutable root in it would defeat the
  // `no-store` the server sends and hand a patched client the previous corpus.
  var loading = get(resolve('manifest.bin'))
    .then(function (r) { return r.arrayBuffer(); })
    .then(function (buf) {
      fs.manifest = new Uint8Array(buf);
      parseManifestHeader(fs.manifest);
      return get(resolve('index.dev'));
    })
    .then(function (r) { return r.arrayBuffer(); })
    .then(function (buf) {
      fs.indexBytes = new Uint8Array(buf);
      return prefetchBoot();
    })
""",
    ),
    # ── the ready line, and the sweep ──
    (
        """      console.log('[webfs] ready in ' + (ms / 1000).toFixed(1) + ' s: ' +
                  fs.hashes.length + ' chunks known, ' + fs.bootChunks + ' prefetched, ' +
                  (fs.netBytes / 1048576).toFixed(1) + ' MB decoded, cache cap ' +
                  (fs.cap / 1048576).toFixed(0) + ' MB');
      fs.loaded = true;
      if (fs.onload) fs.onload();
      // After the game is up, never before: the boot set is what the start needs, and
      // competing with it for the connection pool would delay the thing being waited on.
      backgroundFill();
""",
        """      console.log('[webfs] ready in ' + (ms / 1000).toFixed(1) + ' s: ' +
                  fs.hashes.length + ' chunks known, ' + fs.bootChunks + ' prefetched (' +
                  fs.cacheHits + ' already stored locally), ' +
                  (fs.netBytes / 1048576).toFixed(1) + ' MB off the network, cache cap ' +
                  (fs.cap / 1048576).toFixed(0) + ' MB');
      fs.loaded = true;
      if (fs.onload) fs.onload();
      // After the game is up, never before: the boot set is what the start needs, and
      // competing with it for the connection pool would delay the thing being waited on.
      sweepCache();
      backgroundFill();
""",
    ),
    # ── the summary on the way out ──
    (
        """      console.log('[webfs] ' + fs.netRequests + ' requests, ' +
                  (fs.netBytes / 1048576).toFixed(1) + ' MB, ' +
                  fs.missCount + ' blocking (' + (fs.missBytes / 1048576).toFixed(1) + ' MB), ' +
                  fs.evicted + ' chunks evicted');
""",
        """      console.log('[webfs] ' + fs.netRequests + ' requests, ' +
                  (fs.netBytes / 1048576).toFixed(1) + ' MB, ' +
                  fs.cacheHits + ' chunks served from tier 2, ' + fs.cacheWrites + ' stored, ' +
                  fs.missCount + ' blocking (' + (fs.missBytes / 1048576).toFixed(1) + ' MB), ' +
                  fs.evicted + ' chunks evicted');
""",
    ),
]


def main():
    path = os.path.join(ROOT, REL)
    if not os.path.isfile(path):
        sys.exit("not found: %s (set M2WASM to the client tree)" % path)

    s = io.open(path, encoding="utf-8", errors="surrogateescape").read()
    if MARKER in s:
        print("already patched: %s" % REL)
        return

    for old, new in EDITS:
        if s.count(old) != 1:
            sys.exit("anchor not found exactly once in %s (%d matches):\n%s"
                     % (REL, s.count(old), old.split("\n")[0]))
        s = s.replace(old, new, 1)

    io.open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)
    print("patched: %s" % REL)
    print("\nwebfs.js is copied into the client output as-is, so this needs no relink —\n"
          "but the copies under build-*/web and dist/browser are stale until the next\n"
          "package step puts the new one there.")


if __name__ == "__main__":
    main()
