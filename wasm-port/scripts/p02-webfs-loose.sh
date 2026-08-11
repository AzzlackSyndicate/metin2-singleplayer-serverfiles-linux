#!/usr/bin/env bash
# IDEMPOTENT PATCH — tools/wasm/build-webfs.py (browser-only build tool; no client code,
# no CMake, nothing the desktop build reads).
#
# WHAT IT FIXES. build-webfs.py enumerates the corpus by walking the pack directories
# named in bin/pack/index.dev, and nothing else:
#
#     for pi, (name, container) in enumerate(packs):
#         root = pack_root / name
#         for dirpath, _dirnames, filenames in os.walk(root):
#
# Everything the client reads that does NOT live inside a pack is therefore absent from
# the manifest. The measured casualty is the music: bin/BGM holds 25 loose .mp3 (83.9 MB),
# of which only the 5 that are ALSO in the `bgm` pack reached the browser. The other 20 —
# every map theme, 74.6 MB — were never packaged, so the browser played no map music and
# the options list, which is built from what exists, offered a single entry.
#
# The desktop never had this problem because its dist stages bin/BGM straight into
# dist/desktop/BGM (src/UserInterface/CMakeLists.txt:1007, "streamed loose by the sound
# path") and CEterPackManager::GetFromFile opens it off the real filesystem. In a browser
# there is no real filesystem: the manifest IS the filesystem, so a file that is not in it
# does not exist.
#
# THE NAMES ARE THE WHOLE TRICK, and the contract is already in place:
#   * the manifest is folded (lowercased) throughout — build-webfs.fold() is
#     CEterPackManager::ConvertFileName in Python — so the on-disk `BGM` directory and the
#     client's lowercase `bgm/enter_the_east.mp3` meet at the same key. Nothing extra is
#     needed for case, and nothing may be added: WebProvider folds the asked name too.
#   * loose names are keyed relative to the DATA ROOT (bin/), not to a pack, because that
#     is the name the client asks with. WebProvider::listFiles publishes each key twice —
#     bare, and as "pack/<pack>/"+key — and StripDiskPrefix inverts the second, so a bare
#     data-root key resolves exactly as the client spells it.
#
# The files are carried by one synthetic pack appended to the manifest and to the emitted
# index.dev, so InitPacks registers it like any other WEB pack. No client change.
#
# APPENDED, NOT PREPENDED — measured, not preferred. The only names that exist both loose
# and in a pack are the 5 bgm/*.mp3, and they are BYTE-IDENTICAL (md5 verified), so
# content addressing collapses them to one blob and the registration order is
# unobservable. Appending keeps every existing chunk's contents — and therefore its
# content-addressed URL — unchanged, so a returning player downloads only the new music
# instead of the whole corpus again. If a loose file ever has to SHADOW a pack file
# (the desktop is SEARCH_FILE_FIRST, mainPosix.cpp:116), move the row to the front.
set -uo pipefail

F=/opt/m2wasm/tools/wasm/build-webfs.py
MARK="M2WASM-LOOSE-PATCH"

[ -f "$F" ] || { echo "FATAL: $F not found"; exit 1; }
if grep -q "$MARK" "$F"; then
    echo "already patched ($MARK present) — nothing to do"
    exit 0
fi

cp -n "$F" "$F.m2orig" 2>/dev/null || true
echo "-- backup: $F.m2orig"

python3 - "$F" <<'PYEOF'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text()

# ── 1. the options ────────────────────────────────────────────────────────────
old_arg = "    ap.add_argument('--chunk-size', type=int, default=4 * 1024 * 1024)"
new_arg = """    ap.add_argument('--chunk-size', type=int, default=4 * 1024 * 1024)
    # ── M2WASM-LOOSE-PATCH: the trees the client reads that are NOT packs ──────────
    # bin/BGM is the whole known list, and it is a default rather than a flag because
    # forgetting it produces a client that runs perfectly and is silent — the same class
    # of failure as the missing font (WebProvider::listFiles). Verified against a real
    # session's census: of 5,922 names read, the only ones outside the packs were this
    # music and mark/10_0.tga, which the client WRITES at runtime.
    # Excluded on purpose: userdata/ (accounts.json — written, and credentials),
    # screenshot/ and upload/ (runtime output), miles/ and the *.dll at the root (native
    # Miles/Granny/SpeedTree binaries; the browser uses WebAudio and has no loader).
    ap.add_argument('--loose', action='append', default=None, metavar='DIR',
                    help='data-root tree to include besides the packs, repeatable '
                         '(default: BGM)')
    ap.add_argument('--no-loose', dest='loose', action='store_const', const=[],
                    help='package the packs only (the pre-2026-08 behaviour)')
    ap.add_argument('--loose-pack', default='loose',
                    help='name of the synthetic pack the loose files ride in')
    ap.add_argument('--data-root', default=None,
                    help='root the loose names are relative to (default: <pack>/..)')"""
assert s.count(old_arg) == 1, 'arg anchor'
s = s.replace(old_arg, new_arg)

# ── 2. the enumeration ────────────────────────────────────────────────────────
old_enum = """        seen_per_pack.append(n)

    total_bytes = sum(e[3] for e in entries)"""
new_enum = """        seen_per_pack.append(n)

    # ── M2WASM-LOOSE-PATCH: the files that live BESIDE the packs ──────────────────
    # The loop above walks pack directories, which is every file the packs carry and no
    # file they do not. bin/BGM is 25 loose .mp3 the sound path streams by name; on the
    # desktop dist it is hardlink-mirrored next to pack/ and answered by
    # CEterPackManager::GetFromFile off the real filesystem. The browser has no real
    # filesystem, so anything not in the manifest is missing — which is why the browser
    # had no map music while the desktop did.
    #
    # Keyed RELATIVE TO THE DATA ROOT and folded, i.e. exactly the name the client asks
    # with: bin/BGM/enter_the_east.mp3 -> 'bgm/enter_the_east.mp3'. The fold is what makes
    # the on-disk 'BGM' and the client's 'bgm' the same key, so no case handling is added
    # anywhere — WebProvider folds the asked name through the same transform.
    #
    # They ride in ONE synthetic pack, appended to both the manifest pack table and the
    # emitted index.dev, so InitPacks registers it exactly like a real WEB pack and
    # WebProvider answers the bare data-root name. See this patch's script header for why
    # appended and not prepended.
    loose_dirs = ['BGM'] if args.loose is None else args.loose
    data_root = Path(args.data_root).resolve() if args.data_root else pack_root.parent
    if loose_dirs:
        if any(pname == args.loose_pack for pname, _c in packs):
            sys.exit(f'--loose-pack name collides with a real pack: {args.loose_pack}')
        loose_pi = len(packs)
        packs.append((args.loose_pack, 'WEB'))
        index_rows.append((args.loose_pack, 'WEB'))
        n_loose, b_loose = 0, 0
        for d in loose_dirs:
            root = data_root / d
            if not root.is_dir():
                print(f'   ! --loose {d}: not a directory under {data_root}, skipped')
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                for fn in filenames:
                    real = Path(dirpath) / fn
                    try:
                        size = real.stat().st_size
                    except OSError:
                        continue
                    rel = str(real.relative_to(data_root)).replace('\\\\', '/')
                    entries.append((loose_pi, fold(rel), real, size))
                    n_loose += 1
                    b_loose += size
        print(f'== loose: {n_loose} files, {b_loose / 1e6:.1f} MB from '
              f'{", ".join(loose_dirs)} -> pack "{args.loose_pack}" '
              f'(names relative to {data_root})')

    total_bytes = sum(e[3] for e in entries)"""
assert s.count(old_enum) == 1, 'enum anchor'
s = s.replace(old_enum, new_enum)

p.write_text(s)
print('-- patched')
PYEOF
rc=$?
[ $rc -eq 0 ] || { echo "FATAL: patch failed rc=$rc"; exit $rc; }

python3 -c "import ast,sys; ast.parse(open('$F').read())" && echo "-- syntax OK"
grep -c "$MARK" "$F"
