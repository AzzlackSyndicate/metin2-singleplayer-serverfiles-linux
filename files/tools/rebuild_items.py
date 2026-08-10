#!/usr/bin/env python3
"""Rebuild the displayed names in files/items.json from the server's own files.

    python3 files/tools/rebuild_items.py --conf /path/to/serverfiles/share/conf

Why this exists: items.json used to be a file with no way back to where it came
from, and it was built from item_names_de.txt. The pack's ACTIVE name file is
item_names.txt, which is English -- Languages.txt says the default is EN, and
there is no item_names_en.txt precisely because the plain one is it. So the
panel spent its life showing German names to people whose own game client was
showing English ones, and no amount of reading admin_panel.py would have said
why.

What it does:

  * `n' (the name shown, and searched) comes from item_names.txt.
  * `k' (search keywords) keeps whatever was there and gains the German and
    Turkish names, whole. Matching is on substrings, so "vollmondschwert" and
    "vollmondschwert+9" both still find the item -- while splitting them on the
    plus would add "0" and "9" as keywords that match half the index.
  * `v' and `c' are not touched. The category comes from item_proto and there
    is nothing here that could improve on it.

Items whose vnum item_names.txt does not carry keep the name they had. There
are about 4300 of those and the server has no name for them either; they are
mostly fish, blend stones and other things no name file in the pack mentions.

Idempotent: running it twice changes nothing the second time.
"""
import argparse, io, json, os, sys

DEFAULT_CONF = "/opt/m2port/dockerctx/game/src/serverfiles/share/conf"


def read_names(conf, fname, encodings):
    """vnum -> name, from a two-column VNUM<TAB>LOCALE_NAME file.

    The files are not all in one encoding: the German one is latin-1 and the
    Turkish one is cp1254, so each is tried in turn rather than assumed.
    """
    path = os.path.join(conf, fname)
    if not os.path.exists(path):
        return {}
    raw = open(path, "rb").read()
    for enc in encodings:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("latin-1")

    out = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            out[int(parts[0])] = parts[1].strip()
        except ValueError:
            continue                      # the VNUM/LOCALE_NAME header line
    return out


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conf", default=DEFAULT_CONF,
                    help="the serverfiles' share/conf directory (default: %(default)s)")
    ap.add_argument("--items", default=os.path.join(os.path.dirname(here), "items.json"),
                    help="the index to rewrite (default: %(default)s)")
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    if not os.path.isdir(args.conf):
        sys.exit("no such directory: %s\n"
                 "Point --conf at the serverfiles' share/conf." % args.conf)

    en = read_names(args.conf, "item_names.txt",    ("utf-8", "latin-1"))
    de = read_names(args.conf, "item_names_de.txt", ("utf-8", "latin-1"))
    tr = read_names(args.conf, "item_names_tr.txt", ("utf-8", "cp1254", "latin-1"))
    if not en:
        sys.exit("%s/item_names.txt is missing or empty -- nothing to build from."
                 % args.conf)
    print("names read: EN %d, DE %d, TR %d" % (len(en), len(de), len(tr)))

    items = json.load(open(args.items, encoding="utf-8"))
    renamed = untouched = 0

    for it in items:
        name_en = en.get(it["v"])
        if not name_en:
            untouched += 1
            continue

        words = []

        def add(w):
            w = w.strip().lower()
            if w and w not in words and w != name_en.lower():
                words.append(w)

        for w in str(it.get("k", "")).split():   # existing keywords are words
            add(w)
        add(de.get(it["v"], ""))                 # the other names go in whole
        add(tr.get(it["v"], ""))

        if it["n"] != name_en:
            renamed += 1
        it["n"] = name_en
        it["k"] = " ".join(words)

    print("renamed: %d, left alone (no English name): %d" % (renamed, untouched))
    if args.dry_run:
        print("--dry-run: nothing written")
        return

    with io.open(args.items, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print("wrote %s (%d entries)" % (args.items, len(items)))


if __name__ == "__main__":
    main()
