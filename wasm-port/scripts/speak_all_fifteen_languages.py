#!/usr/bin/env python3
"""The browser client speaks English and always has. All fifteen were shipping.

WHAT WAS ASKED FOR: "Bir de 15 Dil Destegini de eklememiz gerekiyor Client
tarafina."

═══════════════════════════════════════════════════════════════════════════════
THE DATA WAS ALREADY THERE. MEASURED BEFORE ANYTHING WAS WRITTEN.
═══════════════════════════════════════════════════════════════════════════════

Decoding the shipped manifest (M2WF, tools/wasm/build-webfs.py writes it):

    locale_cz  229 files  9.2 MB      locale_it  229 files  9.3 MB
    locale_de  234 files 10.0 MB      locale_nl  229 files  9.2 MB
    locale_dk  229 files  9.2 MB      locale_pl  229 files  9.3 MB
    locale_en  229 files  8.8 MB      locale_pt  229 files  9.3 MB
    locale_es  229 files  9.3 MB      locale_ro  229 files  9.2 MB
    locale_fr  232 files  9.5 MB      locale_ru  229 files  9.3 MB
    locale_gr  233 files  9.5 MB      locale_tr  231 files  9.4 MB
    locale_hu  229 files  9.2 MB      ------------------------------
                                      139.5 MB, all fifteen, in the corpus

and bin/pack/index.dev lists every one of them as a FOLDER, so EterPackManager
already knows they exist. The build stages the whole of bin/pack (`full`), and
the streaming filesystem fetches a chunk only when something reads it — so those
139 MB have been sitting in the deployment costing nobody a single byte of
download, because nothing ever asked for them.

>>> WHAT WAS MISSING WAS ONE FILE, ABOUT TWENTY BYTES LONG. <<<

    src/PyLib/src/application/Application.cpp:249
        LocaleService_LoadConfig("locale.cfg");
        SetDefaultCodePage(LocaleService_GetCodePage());

    src/PyLib/src/LocaleService.cpp:23-45
        char MULTI_LOCALE_PATH[256] = "locale/en";      // the default
        ...
        if (fgets(line, ...)) {
            sscanf(line, "%d %s", &code, name);         // "1254 tr"
            MULTI_LOCALE_CODE = code;
            sprintf(MULTI_LOCALE_PATH, "locale/%s", name);
        }

fopen("locale.cfg") is relative, the client's working directory is `/`, and
there is no /locale.cfg in the wasm filesystem. So the read fails, the compiled
default stands, and the client has spoken English for every player in every
country since the port began. Everything downstream follows that one string:
app.GetLocalePath() feeds localeinfo.py, uiscriptlocale.py, the minimap atlas,
the GM mark, item and mob names — all of it already parameterised, all of it
already correct, all of it pointed at locale/en.

═══════════════════════════════════════════════════════════════════════════════
SO THIS WRITES THAT FILE, AND NOTHING ELSE CHANGES
═══════════════════════════════════════════════════════════════════════════════

webfs.js's install() already writes the one other file the client needs in
MEMFS before main() — /pack/index.dev — from inside Module.preRun, which is the
only moment that is both after FS exists and before Application's constructor
runs. locale.cfg has exactly the same contract and exactly the same deadline, so
it is written on the line below rather than given a page script of its own that
would have to be added to the shell, the CMake staging list and the packaging
list to arrive at the same place one line later.

── WHERE THE LANGUAGE COMES FROM, IN ORDER, AND WHY THAT ORDER ────────────────

  1. ?lang=xx          an explicit choice. Wins over everything and is REMEMBERED,
                       so the next visit — from a bookmark, from the panel, from
                       nowhere — keeps it.
  2. localStorage      what was remembered.
  3. ?deflang=xx       what the SERVER speaks. The panel appends this to the play
                       link from its own m2-lang status, so a Turkish server
                       greets a first-time player in Turkish.

     >>> A DEFAULT, NOT AN OVERRIDE, AND THAT IS THE WHOLE REASON THERE ARE TWO
     >>> PARAMETERS. <<< The panel puts its value on EVERY play link. Were it
     one parameter, a player who chose Greek would have it silently undone by
     the next click of the button they arrived through, forever, and the
     language picker would look broken rather than overruled.
  4. navigator.language the browser's own answer, for somebody who opened the
                       client directly with no panel and no history.
  5. en                what the client did before this existed.

── THE CODES ARE NOT ISO CODES, AND THREE OF THEM DIFFER ──────────────────────

The pack names come from the server files (Languages.txt), not from BCP 47:

    cs -> cz        da -> dk        el -> gr

A browser reporting `cs-CZ' mapped straight through would ask for locale/cs,
which does not exist; LocaleService would set the path anyway and every string
lookup would fail on a client that had already drawn its login screen. Hence
the map, and hence the guard: an unknown code falls back to en rather than
being passed through, because "wrong language" is a complaint and "no text at
all" is a broken client.

── THE CODEPAGE MATTERS AS MUCH AS THE PATH ───────────────────────────────────

SetDefaultCodePage is fed from the same line, and the locale files are stored in
their own ANSI codepage rather than in UTF-8:

    1250  cz hu pl ro     1252  en de fr es it nl pt dk
    1251  ru              1253  gr            1254  tr

Send Turkish text through 1252 and the client renders mojibake for every ı, ş
and ğ — text that is there, drawn, and wrong, which is harder to diagnose than
text that is missing. The table is written out per language rather than
defaulted to 1252, so adding a sixteenth locale is a line here and not a bug
report from one country.

── WHAT THIS DOES NOT DO ──────────────────────────────────────────────────────

The language is read ONCE, in Application's constructor, before Python starts.
There is no way to change it in a running client and nothing here pretends
otherwise: a change takes effect on the next load, which is why the choice is
stored rather than applied.

And it does not touch the SERVER's language. Item and mob names come from the
client's own locale/<xx>/item_proto and mob_proto, so those follow this; quest
text, notices and anything the game core sends as a string follow m2-lang in
the game container. A player reading Greek menus against an English quest line
is expected until the operator switches the server too, and that is the
operator's decision, not the player's.

Idempotent. Point it at the client tree with M2WASM; a second run reports
`already patched'.
"""
import io
import os
import sys

ROOT = os.environ.get("M2WASM", "/opt/m2wasm")
SRC = os.path.join(ROOT, "tools/wasm/webfs.js")

MARKER = "__m2Locale"

ANCHOR = """    Module.FS.writeFile('/pack/index.dev', fs.indexBytes);
    status('');"""

ADDITION = """    Module.FS.writeFile('/pack/index.dev', fs.indexBytes);
    writeLocaleCfg();
    status('');"""

# Inserted just above install(), so the reader meets the table before the call.
HELPER_ANCHOR = """  function install() {"""

HELPER = """  // ── WHICH OF THE FIFTEEN LANGUAGES THIS CLIENT SPEAKS ───────────────────────
  //
  // All fifteen locale packs are in the corpus already — 139 MB of them, listed
  // in bin/pack/index.dev and fetched a chunk at a time only when something
  // reads them, so the fourteen nobody picks cost nothing at all. What was
  // missing was the twenty-byte file that says which one:
  //
  //     Application.cpp:249   LocaleService_LoadConfig("locale.cfg")
  //     LocaleService.cpp:43  sscanf(line, "%d %s", &code, name)
  //                           -> MULTI_LOCALE_PATH = "locale/<name>"
  //
  // fopen() is relative, the working directory is /, and there was no
  // /locale.cfg — so the compiled default stood and every player in every
  // country got English. It is written HERE because this is the one moment that
  // is both after FS exists and before Application's constructor reads it, and
  // because /pack/index.dev on the line below has the identical contract.
  //
  // The codepage is not decoration: the locale files are stored in their own
  // ANSI codepage, and Turkish read as 1252 draws mojibake for every ı, ş and ğ
  // — text that is present and wrong, which is worse to diagnose than text that
  // is absent.
  var LOCALES = {
    en: 1252, de: 1252, fr: 1252, es: 1252, it: 1252, nl: 1252, pt: 1252, dk: 1252,
    cz: 1250, hu: 1250, pl: 1250, ro: 1250,
    ru: 1251, gr: 1253, tr: 1254
  };
  // The pack names come from the server files, not from BCP 47. Three of them
  // differ, and a browser reporting cs-CZ mapped straight through would ask for
  // locale/cs — a path that does not exist, on a client that has already drawn
  // its login screen and then finds no strings at all.
  var FROM_BROWSER = { cs: 'cz', da: 'dk', el: 'gr' };

  function wantedLocale() {
    var p, v;
    try { p = new URLSearchParams(location.search); } catch (e) { p = null; }
    var pick = function (s) {
      s = String(s || '').toLowerCase().replace('_', '-');
      if (!s) return '';
      var two = s.split('-')[0];
      if (FROM_BROWSER[two]) two = FROM_BROWSER[two];
      // the full tag first: pt-br and pt both land on pt here, but a future
      // locale pack named for a region would be found before its base language
      if (LOCALES[s]) return s;
      return LOCALES[two] ? two : '';
    };
    // 1. an explicit choice, which is also REMEMBERED — see the header for why
    //    this is a different parameter from the server's default.
    v = p && pick(p.get('lang'));
    if (v) { try { localStorage.setItem('m2/lang', v); } catch (e) {} return v; }
    // 2. what was remembered
    try { v = pick(localStorage.getItem('m2/lang')); if (v) return v; } catch (e) {}
    // 3. what the server speaks, offered by the panel on every play link
    v = p && pick(p.get('deflang'));
    if (v) return v;
    // 4. the browser's own answer, for a client opened directly
    try {
      var langs = navigator.languages || [navigator.language];
      for (var i = 0; i < langs.length; i++) { v = pick(langs[i]); if (v) return v; }
    } catch (e) {}
    return 'en';                                  // 5. what it did before this
  }

  function writeLocaleCfg() {
    var code = wantedLocale();
    // "%d %s" — codepage first, then the name LocaleService turns into
    // locale/<name>. The trailing newline is not required by sscanf and is
    // there so the file reads correctly if anyone ever cats it.
    try {
      Module.FS.writeFile('/locale.cfg', LOCALES[code] + ' ' + code + '\\n');
    } catch (e) {
      console.warn('[webfs] could not write locale.cfg (' + e.message +
                   '); the client falls back to English');
      return;
    }
    if (typeof window !== 'undefined') window.__m2Locale = code;
    console.log('[webfs] language: ' + code + ' (codepage ' + LOCALES[code] + ')');
  }

  function install() {"""


def main():
    if not os.path.isfile(SRC):
        sys.stderr.write("not found: %s\n(set M2WASM to the client tree)\n" % SRC)
        return 1

    with io.open(SRC, "r", encoding="utf-8") as f:
        src = f.read()

    if MARKER in src:
        print("already patched: %s" % SRC)
        return 0

    for name, anchor in (("the index.dev write", ANCHOR),
                         ("install()", HELPER_ANCHOR)):
        if src.count(anchor) != 1:
            sys.stderr.write("anchor %s not found exactly once in %s (%d matches). "
                             "Nothing was changed.\n" % (name, SRC, src.count(anchor)))
            return 1

    src = src.replace(HELPER_ANCHOR, HELPER, 1)
    src = src.replace(ANCHOR, ADDITION, 1)

    with io.open(SRC, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("patched: %s" % SRC)
    print("   the panel appends &deflang=<server language> to the play link;")
    print("   ?lang=xx is the player's own choice and is remembered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
