#!/usr/bin/env bash
# IDEMPOTENT PATCH — extern/sdk/python/build-wasm3.sh only (a wasm-only script; the
# desktop build never reads it).
#
# WHAT IT FIXES. CPython's cross-build driver needs a node to run the freshly built
# wasm interpreter with. calculate_node_path() (Platforms/emscripten/__main__.py:438)
# goes STRAIGHT to nvm — `source ~/.nvm/nvm.sh && nvm install 24` — with no PATH lookup
# and no fallback, so on a host without nvm the build dies AFTER libffi and mpdecimal
# have been cross-compiled:
#
#     bash: line 1: /root/.nvm/nvm.sh: No such file or directory
#     subprocess.CalledProcessError: Command '['bash', '-c', 'source ~/.nvm/nvm.sh && ...
#
# That is not a missing dependency, it is a duplicated one: emsdk ALREADY ships node
# 24.19.0 (`~/emsdk/node/24.19.0_64bit/bin/node`), and 24 is exactly the version
# config.toml asks for. The driver's own documented escape is `--host-runner`, so this
# passes emsdk's node instead of installing a second copy through nvm.
#
# Conditional, so it changes nothing on a host that HAS nvm: the flag is added only when
# ~/.nvm/nvm.sh is absent AND an emsdk node exists. Otherwise the command is untouched.
set -uo pipefail

F=/opt/m2wasm/extern/sdk/python/build-wasm3.sh
MARK="M2WASM-HOSTRUNNER-PATCH"

[ -f "$F" ] || { echo "FATAL: $F not found"; exit 1; }

if grep -q "$MARK" "$F"; then
    echo "already patched ($MARK present) — nothing to do"
    exit 0
fi

OLD='python3 Platforms/emscripten build all py_cv_module__sqlite3=n/a'
grep -qF "$OLD" "$F" || { echo "FATAL: anchor line not found; refusing to guess"; exit 1; }

cp -n "$F" "$F.m2orig" 2>/dev/null || true
echo "-- backup: $F.m2orig"

python3 - "$F" <<'PYEOF'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text()
old = 'python3 Platforms/emscripten build all py_cv_module__sqlite3=n/a'
new = '''# ── M2WASM-HOSTRUNNER-PATCH: emsdk's node, not a second one through nvm ───────────
#
# The driver's calculate_node_path() (Platforms/emscripten/__main__.py:438) does not look
# on PATH at all — it shells out to `source ~/.nvm/nvm.sh && nvm install <ver>`. On a host
# with no nvm the build fails there, after libffi and mpdecimal have already been
# cross-compiled, and the traceback names nvm rather than the missing runner.
#
# emsdk already ships the node config.toml asks for (node-version 24; emsdk 4.0.12 carries
# 24.19.0), so --host-runner — the driver's own documented option — points at that one.
# Guarded both ways: a host that HAS nvm keeps the upstream behaviour untouched.
HOSTRUNNER_ARGS=()
if [[ ! -r "$HOME/.nvm/nvm.sh" ]]; then
    _m2_node="$(ls -d "$HOME"/emsdk/node/*/bin/node 2>/dev/null | sort -V | tail -1)"
    if [[ -x "$_m2_node" ]]; then
        echo "== host runner: $_m2_node (emsdk's node; this host has no ~/.nvm)"
        HOSTRUNNER_ARGS=(--host-runner "$_m2_node")
    else
        echo "WARNING: no nvm and no emsdk node — the driver will try nvm and fail." >&2
    fi
fi
python3 Platforms/emscripten build all py_cv_module__sqlite3=n/a "${HOSTRUNNER_ARGS[@]}"'''
assert s.count(old) == 1, f"anchor appears {s.count(old)} times"
p.write_text(s.replace(old, new))
print("-- patched")
PYEOF
rc=$?
[ $rc -eq 0 ] || { echo "FATAL: patch failed rc=$rc"; exit $rc; }

bash -n "$F" && echo "-- syntax OK"
grep -n "$MARK" "$F"
