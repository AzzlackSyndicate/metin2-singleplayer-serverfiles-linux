# wasm-port/scripts

The client tree lives at `/opt/m2wasm` (Old Metin2, branch `feat/bgfx-renderer`)
and **can never be committed here** — it carries Ymir/Webzen material. These
scripts are therefore the only durable record of every change made to it: a fresh
checkout plus these files reproduces the working client.

Pruned from 374 to the set below. What went: one-shot probes whose answers are
written down in the session reports. What stayed: anything that changes source,
the tools that found real defects, and the build/run chain.

## Reproducing the port on a fresh checkout

Run the source patches in any order — each is idempotent and reports
`already patched` on a second run — then apply the one patch file, then build.

| Group | Files |
|---|---|
| Encryption and packet layout | `port_cipher.sh` `port_packets.py` `port_netstream.py` `port_accountconnector.py` `port_cmake2.py` `port_gamestream.py` `port_boundary.py` `fix_completed.py` `fix_pointchange.py` `fix_time32.py` `fix_viewequip.py` `fix_cassert.py` `fix_min.py` |
| Rendering and effects | `fix_armor_skin.py` `fix_effect_crc_key.py`(+2) `fix_effect_handle_rest.py`(+2) `fix_affect_effect_handle.py` `fix_effect_ageing.py`(+comment) `fix_damage_digit_textures.py`(+2) `fix_effect_check_name_spelling.py` |
| World and interaction | `patch_bgfx_object_height.py` `fix_pick_collision.py` `fix_composite_bone.py` `fix_ground_item_boundbox.py` `fix_ground_item_pick.py` `fix_attack_direction.py` `fix_quest_target_minimap_mark.py` `fix_stringtable_owner.py` |
| Sound | `bgm_patch_loose_file_case.py` |
| Toolchain and corpus | `p01-python-hostrunner.sh` `p02-webfs-loose.sh` |
| **Input and pick anchor** | `input-and-pick.patch` → `git apply` |
| Diagnostics, applied | `add_special_effect_tracing.py` `add_stone_smoke_tracing.py` `fix_pickdiag2.py` `probe_quest_target.py`(+fix) `port_hexlog.py` |

`input-and-pick.patch` covers the camera coalescing, pointer lock, the wheel
summation and the zero-extent pick anchor. It exists as a patch rather than as
scripts because **its scripts were destroyed by a prune** whose keep-pattern read
`fix_*.py` and so matched neither `fix-attack-direction.py` (hyphen) nor
`wc01/wc04/wc05/wb18-*.sh`. The branch is untracked, so git could not restore
them; the patch was reconstructed from the applied diff and verified to reverse
cleanly against the tree. `fix_attack_direction.py` was likewise regenerated —
note the underscore.

**If you prune this folder again: match on content, not on filename shape.**

## Tools worth keeping

| | |
|---|---|
| `wirediff.py` | every packet size, both compilers — gdb against the server binary vs. a program compiled against the client's `Packet.h`. Found four of the five layout defects. |
| `headermatch.py` | the same, keyed by **header number**, which is the unit that decides whether the stream desyncs. Names mislead: the client carries `ItemSet2`, `TargetCreateNew`, `MainCharacter2_EMPIRE` under the server's `ItemSet`, `TargetCreate`, `MainCharacter`. |
| `structdiff2.py` | header-parsing predecessor. Kept because it shows what hand-parsing misses. |
| `hsprobe.py` `login_probe.py` | speak the protocol directly, no client needed |
| `ws_probe.py` `ws_selftest.py` | the same through the WebSocket bridge |
| `k10-ktxconv.cpp` `k11-build.sh` | KTX2/ASTC converter with the mip-chain correction. The texture work is deferred, not abandoned. |
| `wb-cdp*.py` `wb01-install-chrome.sh` | drive Chrome over CDP from plain Python — no node in this WSL |

## Build and run

`setup.sh` → `deps2.sh` → `fetchsdk.sh` → `buildpy.sh` → `fetchbin.sh` → `buildclient.sh`
for the desktop; `w05-build-wasm.sh` for the browser (it refuses to trigger a
desktop build, which would fail with ETXTBSY against a running client);
`w10-rebuild-fs.sh` for the data corpus alone. `launch.sh` starts the client with
the pack census and effect tracing enabled; `webplay.sh` serves the browser
client and the ws2tcp proxy so it can be played from Windows.

`disable_enc.sh` and `revert.sh` touch the **live VPS**. `revert.sh` is the one
that puts it back.
