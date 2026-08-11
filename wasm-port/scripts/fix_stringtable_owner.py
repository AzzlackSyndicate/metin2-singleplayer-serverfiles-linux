#!/usr/bin/env python3
"""Besitzername am Bodengegenstand: das "[2]" ist die Kennnummer des Textes selbst.

BEFUND -- WEDER EIN DOPPELEINTRAG NOCH EINE DURCHNUMMERIERUNG
------------------------------------------------------------
Es gibt im ganzen Client KEINE Stelle, die gleichnamige Eintraege durchnummeriert;
die Suche nach einem angehaengten Index in eckigen Klammern findet im Spielcode
nichts. Die Spielfigur wird auch nicht doppelt gefuehrt. Die Kette ist diese:

  PythonTextTail.cpp:779
      static const std::string & strOwnership =
          ApplicationStringTable_GetString(IDS_POSSESSIVE_MORPHENE) == "" ? "'s"
        : ApplicationStringTable_GetString(IDS_POSSESSIVE_MORPHENE);
      strName = c_szName + strOwnership;

  ResourceIDs.h:6   #define IDS_POSSESSIVE_MORPHENE 2

  ApplicationStringTable.cpp   s_stringTable wird NIE beschrieben -- die Tabelle ist
      deklariert, zweimal gelesen und hat baumweit keinen Schreiber. Jede Abfrage
      faellt deshalb auf den Platzhalter durch:
          snprintf(buf, sizeof(buf), "[%lu]", dwID);

Also liefert GetString(2) die Zeichenkette "[2]", und der Besitzer heisst
"xSyndicate58" + "[2]". Das "[2]" ist so sauber strukturiert, weil es kein Muell
ist, sondern eine Diagnoseausgabe: die Kennnummer des fehlenden Textes.

Die Abfrage in PythonTextTail prueft auf LEER und sollte damit den Ersatz "'s"
waehlen. Der Platzhalter ist aber nicht leer, also greift der Ersatz nie.

ApplicationStringTable_Initialize(hInstance) speichert nur das Modulhandle;
WinTypes.h:109 fuehrt es selbst als "stored, never read". Auch der Windows-Bau
liest die STRINGTABLE aus der .rc nie aus -- LoadString wird nirgends aufgerufen --,
der Fehler ist also nicht linuxspezifisch, er faellt hier nur auf.

AENDERUNG
---------
1. Die Tabelle wird in Initialize mit genau den Werten gefuellt, die
   UserInterface.rc:166-175 traegt. Damit liefert GetString(2) wieder "'s", und
   jeder kuenftige Leser bekommt seinen Text statt seiner Nummer.
2. Der Platzhalter fuer eine WIRKLICH unbekannte Nummer bleibt -- er ist eine
   nuetzliche Diagnose --, wird aber in die Tabelle eingetragen, statt aus einem
   gemeinsamen static zurueckgegeben zu werden. Bisher gaben beide Abfragen einen
   Zeiger bzw. eine Referenz auf EINEN geteilten Puffer zurueck, den der naechste
   Aufruf mit einer anderen Nummer ueberschreibt. Genau an dieser einen
   Aufrufstelle wird das Ergebnis an ein `static const std::string &` gebunden,
   also dauerhaft festgehalten: der angezeigte Text haette sich spaeter unter der
   Referenz noch aendern koennen. Ein Eintrag in der Tabelle ist stabil, solange
   der Vorgang laeuft, und macht die zurueckgegebene Referenz ehrlich.

Der Umfang ist klein und vollstaendig belegbar: PythonTextTail.cpp:779 ist die
EINZIGE Nutzung der Tabelle im ganzen Client.

Mehrfach ausfuehrbar: prueft auf die NEUE Form und tut dann nichts.
"""
import io
import sys

TABLE = "/opt/m2wasm/src/PyLib/src/launcher/ApplicationStringTable.cpp"

OLD = '''// Application string table — locale-dependent string lookups.
// These are declared in PyLib/PyLibCompat.h. The full implementation
// loads strings from locale packs; this deferred version returns defaults.
#include "PyLib/PyLibCompat.h"
#include <string>
#include <map>
#include "EterBase/Platform/WinTypes.h"

static std::map<DWORD, std::string> s_stringTable;
static HINSTANCE s_appInstance = nullptr;

void ApplicationStringTable_Initialize(HINSTANCE hInstance)
{
    s_appInstance = hInstance;
}

const char* ApplicationStringTable_GetStringz(DWORD dwID, LPCSTR szKey)
{
    (void)szKey;
    auto it = s_stringTable.find(dwID);
    if (it != s_stringTable.end()) return it->second.c_str();
    static char buf[64];
    snprintf(buf, sizeof(buf), "[%lu]", dwID);
    return buf;
}

const char* ApplicationStringTable_GetStringz(DWORD dwID)
{
    return ApplicationStringTable_GetStringz(dwID, "");
}

const std::string& ApplicationStringTable_GetString(DWORD dwID, LPCSTR szKey)
{
    (void)szKey;
    auto it = s_stringTable.find(dwID);
    if (it != s_stringTable.end()) return it->second;
    static std::string fallback;
    char buf[64];
    snprintf(buf, sizeof(buf), "[%lu]", dwID);
    fallback = buf;
    return fallback;
}

const std::string& ApplicationStringTable_GetString(DWORD dwID)
{
    return ApplicationStringTable_GetString(dwID, "");
}
'''

NEW = '''// Application string table — locale-dependent string lookups.
// These are declared in PyLib/PyLibCompat.h.
//
// ── DIE TABELLE WAR LEER, UND MAN HAT ES AM BODEN GESEHEN ───────────────────
//
// s_stringTable hatte baumweit KEINEN Schreiber: deklariert, zweimal gelesen,
// nie gefuellt. Jede Abfrage fiel also auf den Platzhalter "[<Nummer>]" durch.
// Der einzige Leser im ganzen Client ist PythonTextTail.cpp:779, das Schildchen
// mit dem Besitzer eines liegenden Gegenstands:
//
//     strName = <Name> + ApplicationStringTable_GetString(IDS_POSSESSIVE_MORPHENE)
//
// IDS_POSSESSIVE_MORPHENE ist 2 (ResourceIDs.h:6), also stand am Boden
// "xSyndicate58[2]". Das sah nach einer Durchnummerierung gleichnamiger Spieler
// aus und war keine: es war die Kennnummer des fehlenden Textes. Die Abfrage
// dort prueft auf LEER und haette sonst "'s" gewaehlt -- ein Platzhalter ist
// aber nicht leer, also griff der Ersatz nie.
//
// NICHT LINUXSPEZIFISCH. ApplicationStringTable_Initialize bekommt auf Windows
// ein echtes HINSTANCE und hat es nur weggelegt (WinTypes.h:109 fuehrt es selbst
// als "stored, never read"); LoadString wird nirgends aufgerufen. Der Windows-Bau
// zeigt dasselbe "[2]", die .rc-STRINGTABLE wird dort nie gelesen. Deshalb wird
// die Tabelle hier fuer ALLE Ziele gefuellt, statt einen Windows-Sonderweg ueber
// LoadString einzuziehen: eine Quelle, vier Ziele, dieselben Texte.
#include "PyLib/PyLibCompat.h"
#include "PyLib/ResourceIDs.h"
#include <string>
#include <map>
#include "EterBase/Platform/WinTypes.h"

static std::map<DWORD, std::string> s_stringTable;
static HINSTANCE s_appInstance = nullptr;

void ApplicationStringTable_Initialize(HINSTANCE hInstance)
{
    s_appInstance = hInstance;

    // WORTGLEICH MIT UserInterface.rc:166-175, der STRINGTABLE des Windows-Baus.
    // IDS_WARN_BAD_DRIVER und IDS_WARN_NO_TNL tragen dort wirklich ihren eigenen
    // Namen als Text; das ist so uebernommen und nicht verschoenert, damit die
    // beiden Baeume dieselbe Zeichenkette zeigen.
    s_stringTable[IDS_APP_NAME]            = "Metin 2";
    s_stringTable[IDS_POSSESSIVE_MORPHENE] = "'s";
    s_stringTable[IDS_WARN_BAD_DRIVER]     = "IDS_WARN_BAD_DRIVER";
    s_stringTable[IDS_WARN_NO_TNL]         = "IDS_WARN_NO_TNL";
    s_stringTable[IDS_ERR_CANNOT_READ_FILE] = "Cannot read %s file";
    s_stringTable[IDS_ERR_NOT_LATEST_FILE] =
        "File '%s' is not latest version. Please launch patcher.";
    s_stringTable[IDS_ERR_MUST_LAUNCH_FROM_PATCHER] = "Please run patcher.";
}

// ── DER PLATZHALTER BLEIBT, WANDERT ABER IN DIE TABELLE ─────────────────────
//
// "[<Nummer>]" fuer einen wirklich unbekannten Text ist eine brauchbare Diagnose
// und wird nicht abgeschafft. Was sich aendert, ist WO er liegt.
//
// Beide Abfragen gaben ihn bisher aus EINEM geteilten static zurueck -- `static
// char buf[64]` bzw. `static std::string fallback` --, den der naechste Aufruf
// mit einer anderen Nummer ueberschreibt. Die Rueckgabetypen sind aber `const
// char*` und `const std::string&`, versprechen also etwas, das den naechsten
// Aufruf ueberlebt. Der einzige Leser bindet sein Ergebnis an ein
// `static const std::string &` und haelt es damit fuer immer fest: der
// angezeigte Text haette sich spaeter unter der Referenz noch aendern koennen.
//
// Ein Eintrag in der Tabelle wird nie geloescht und nie verschoben (std::map
// haelt seine Knoten stabil), also ist die zurueckgegebene Referenz so
// langlebig, wie ihr Typ behauptet.
static const std::string& __InternUnknown(DWORD dwID)
{
    auto it = s_stringTable.find(dwID);
    if (it != s_stringTable.end())
        return it->second;

    char buf[64];
    snprintf(buf, sizeof(buf), "[%lu]", static_cast<unsigned long>(dwID));
    return s_stringTable.emplace(dwID, buf).first->second;
}

const char* ApplicationStringTable_GetStringz(DWORD dwID, LPCSTR szKey)
{
    (void)szKey;
    return __InternUnknown(dwID).c_str();
}

const char* ApplicationStringTable_GetStringz(DWORD dwID)
{
    return ApplicationStringTable_GetStringz(dwID, "");
}

const std::string& ApplicationStringTable_GetString(DWORD dwID, LPCSTR szKey)
{
    (void)szKey;
    return __InternUnknown(dwID);
}

const std::string& ApplicationStringTable_GetString(DWORD dwID)
{
    return ApplicationStringTable_GetString(dwID, "");
}
'''


def patch(path, old, new, name):
    text = io.open(path, encoding="utf-8", errors="surrogateescape").read()
    if new in text:
        print("  [schon drin] %s" % name)
        return True
    n = text.count(old)
    if n != 1:
        print("  [FEHLER] %s: Anker %d mal gefunden, erwartet genau 1" % (name, n))
        return False
    io.open(path, "w", encoding="utf-8", errors="surrogateescape").write(
        text.replace(old, new, 1))
    print("  [geaendert] %s" % name)
    return True


ok = patch(TABLE, OLD, NEW, "ApplicationStringTable.cpp: Tabelle fuellen")
sys.exit(0 if ok else 1)
