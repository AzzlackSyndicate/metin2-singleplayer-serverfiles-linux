# Adding items to NPC shops (r40250 / TMP4 server files)

How to put an item into an NPC's shop at a price of your choosing. Everything
below was learned (partly the hard way) while putting the Wind Shoes into the
general stores; section *A worked example* at the end is that whole job written
out, and is the thing to copy and change the vnums in.

Nothing here is done for you by the panel — this is manual server work, and it
needs a shell on the machine and the database root password.

---

## Where these paths are

The Docker stack keeps the game's data tree inside the `game` container:

| | |
|---|---|
| `share/conf/item_proto.txt` | `/opt/metin2/share/conf/item_proto.txt` |
| a shell in the game container | `docker compose exec game sh` |
| the database | `docker compose exec mariadb mariadb -uroot -p"$M2_DB_ROOT_PASSWORD"` |
| the root password | `M2_DB_ROOT_PASSWORD` in `/opt/metin2/stack/.env` |
| restarting the game | `docker compose restart game` (from `/opt/metin2/stack`) |

**One thing to know before you start.** `share/` is baked into the game image,
not a volume. A change you make inside a running container survives a
`docker compose restart`, and is lost the next time the image is rebuilt or the
container recreated (`docker compose up -d --build`). If you want the change to
be permanent, make the same edit to `game/src/serverfiles/share/conf/item_proto.txt`
in the build context and rebuild. The database side (`player.shop_item`) lives
in a volume and does survive.

On other layouts the paths differ but nothing else does. The rest of this
document is about the game, not about the host.

---

## The three pieces involved

| Piece | Where | What it controls |
|---|---|---|
| `player.shop` | MySQL | Which shop vnum belongs to which NPC (`npc_vnum`) |
| `player.shop_item` | MySQL | What is on the shelf: `(shop_vnum, item_vnum, count)` |
| `item_proto.txt` | `share/conf/item_proto.txt` | **The item's price** (and every other item property) |

There is no price column in `shop_item`. The price an NPC charges is computed
in the game core (`shop.cpp`, `CShop::SetShopItems`):

```
price = item_proto.gold × count
```

So "sell for 1 yang" means `gold = 1` on the item and `count = 1` on the
shelf. (Exception: items with the `COUNT_PER_1GOLD` flag price differently —
none of the items we touched have it.)

## Trap 1: MySQL is NOT the source of truth for item_proto

The db core **rebuilds the whole `player.item_proto` table from
`item_proto.txt` at every boot** (`ClientManagerBoot.cpp`,
`MirrorItemTableIntoDB`). A price changed only via SQL works until the next
restart, then silently reverts. Change the **txt**; update SQL too only so
the change is live before the restart.

`player.shop` and `player.shop_item` have no txt counterpart — for those,
MySQL **is** the source of truth. (Same for `mob_proto.txt` → `player.mob_proto`,
should you ever touch mob data: txt wins there too.)

## Trap 2: the symlink

In the original server-file layout, `db/item_proto.txt` is a **symlink** to
`share/conf/item_proto.txt`. It is ONE file. A loop that "backs up and edits
both" reads its own first-pass output as the backup of the second file. Resolve
with `readlink -f` and edit the real path once.

## Trap 3: the column numbers

`item_proto.txt` is tab-separated, names are EUC-KR encoded (use
`export LC_ALL=C` so awk treats bytes, never re-encode the file). The reader
(`db/src/ProtoReader.cpp`) indexes columns **0-based** (`dataArray[9]` = gold),
so in 1-based awk fields:

| awk field | column |
|---|---|
| `$1` | vnum (plain number, or a range `a~b`) |
| `$2` | name (Korean, EUC-KR) |
| `$10` | **gold** ← the shop sale price factor |
| `$11` | shop_buy_price |
| `$12` | refined_vnum — **do not touch** (first attempt set it to 1 by accident) |

Edit pattern:

```sh
export LC_ALL=C
REAL=$(readlink -f /opt/metin2/share/conf/item_proto.txt)
awk -F'\t' 'BEGIN{OFS="\t"} $1==VNUM {$10=PRICE; $11=PRICE} {print}' "$REAL" > "$REAL.new"
mv "$REAL.new" "$REAL"
```

## The shops in these server files

From `player.shop` joined with `player.mob_proto` (shop grid holds at most
40 items; counts as of 2026-08-07 in parentheses):

| shop vnum | NPC | Name | items |
|---|---|---|---|
| 1 | 9001 | Weapon Shop Dealer (24) |
| 2 | 9009 | Fisherman (7) |
| **3** | **9003** | **General Store Saleswoman** (26) — *"the general vendor in every city"* |
| 4 | 9002 | Armour Shop Dealer (17) |
| 5 | 9007 | Weapon Shop Dealer (12) |
| 6 | 9008 | Armour Shop Dealer (24) |
| 7 | 9005 | Storekeeper (5) |
| 8 | 9004 | Event Helper (6) |
| 9 | 20042 | Peddler (3) |
| 10 | 20015 | Deokbae (1) |
| 11 | 20349 | Stable Boy (3) |
| 13 | 20001 | Alchemist (2) |

**One shop definition covers every city.** The saleswoman in each town is the
same NPC vnum, so adding to shop 3 puts the item in all of them at once.
Shops 1001+ with `npc_vnum = 0` are quest-driven shops — leave them alone.

## Step by step

1. Confirm the item exists and find its current price fields:
   `awk -F'\t' '$1==VNUM {print $1, $10, $11, $12}' <real item_proto.txt path>`
   (Also check `files/items.json` — if the vnum is in there, the client knows
   the item and the panel's give-item search can find it.)
2. Patch `$10` (and usually `$11`) in the txt — see edit pattern above. Keep a
   `.orig` copy the first time.
3. Mirror the same values into SQL so they are right immediately:
   `UPDATE player.item_proto SET gold=X, shop_buy_price=X WHERE vnum=VNUM;`
4. Shelf it (idempotent):
   ```sql
   DELETE FROM player.shop_item WHERE shop_vnum=3 AND item_vnum=VNUM;
   INSERT INTO player.shop_item (shop_vnum, item_vnum, count) VALUES (3, VNUM, 1);
   ```
5. **Restart the game server** — shops AND prices are read once, at boot.
   Anyone playing is dropped for under a minute:
   ```sh
   cd /opt/metin2/stack && docker compose restart game
   ```
6. Verify after the restart (this catches every trap above at once):
   ```sql
   SELECT vnum, gold, shop_buy_price, refined_vnum FROM player.item_proto WHERE vnum=VNUM;
   SELECT * FROM player.shop_item WHERE item_vnum=VNUM;
   ```
   Ports up: `ss -ltn | grep -E ':(11000|1300[0-9])'` — and glance at the
   `syserr` files under the game's log directory (`pid_init` / `Start of pid`
   lines are normal boot noise, not errors).

---

## A worked example: Wind Shoes for 1 yang

This is the job in full — Wind Shoes (39036) and Wind Shoes+ (tradeable)
(72702) into the General Store Saleswoman's shop in every town, at 1 yang.
It is safe to run twice.

Two commands, because the file lives in the game container and the database
lives in another one. First the txt, inside the game container:

```sh
docker compose exec game sh -s <<'EOF'
set -e
export LC_ALL=C
REAL=$(readlink -f /opt/metin2/share/conf/item_proto.txt)
[ -f "$REAL.orig" ] || cp "$REAL" "$REAL.orig"
awk -F'\t' 'BEGIN{OFS="\t"} $1==39036 || $1==72702 {$10=1; $11=1} {print}' "$REAL" > "$REAL.new"
mv "$REAL.new" "$REAL"
awk -F'\t' '$1==39036 || $1==72702 {print "  "$1": gold="$10" shop_buy_price="$11}' "$REAL"
EOF
```

Then the database — the same prices so they are right before the restart, and
the shelf entries, which are the part that actually persists:

```sh
. /opt/metin2/stack/.env
docker compose exec -T mariadb mariadb -uroot -p"$M2_DB_ROOT_PASSWORD" <<'EOF'
UPDATE player.item_proto SET gold=1, shop_buy_price=1 WHERE vnum IN (39036, 72702);
DELETE FROM player.shop_item WHERE shop_vnum=3 AND item_vnum IN (39036, 72702);
INSERT INTO player.shop_item (shop_vnum, item_vnum, count) VALUES (3, 39036, 1), (3, 72702, 1);
EOF
```

Restart, then check that the price survived the boot — this is the one check
that proves Trap 1 did not bite:

```sh
docker compose restart game
sleep 20
docker compose exec -T mariadb mariadb -uroot -p"$M2_DB_ROOT_PASSWORD" -N \
    -e "SELECT vnum, gold, shop_buy_price FROM player.item_proto WHERE vnum IN (39036, 72702)"
```

If `gold` is still 1 there, the txt edit took and the shoes are on sale for
1 yang in every town. If it has reverted to the original price, the db core
rebuilt the table from a txt you did not actually change — check that
`readlink -f` resolved to the file you edited.

---

## Access notes

- Item names and icons come from the client's own proto, so **no client patch is
  needed**: the shop window shows the price the server sends, and the 2014
  client already knows every official item.
- Shop work is root work. A least-privilege panel database user cannot see the
  shop tables at all — that is the restriction doing its job, not a bug.
- Keep the `.orig` copy. It is the only way back to the shipped prices without
  re-extracting the server files.
