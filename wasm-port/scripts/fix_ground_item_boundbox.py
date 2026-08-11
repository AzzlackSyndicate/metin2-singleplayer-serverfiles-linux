#!/usr/bin/env python3
"""Bodengegenstaende: die Mitte des Huellquaders wird aus uninitialisiertem Speicher gelesen.

BEFUND
------
CPythonItem::CreateItem fragt den Huellquader des gerade geladenen Modells ab:

    D3DXVECTOR3 vMin, vMax;                       // D3DXVECTOR3() = default -> UNBESTIMMT
    ThingInstance->GetBoundBox(uint32_t(-1), vMin.x, ... , vMax.z);
    v3Center = (vMin + vMax) * 0.5f;

Der Index uint32_t(-1) ist die vereinbarte Kodierung fuer "kein Index, gib den
GESAMTEN Quader" -- EterModel::GetBoundBox (EterModel.cpp:449-452) behandelt ihn
ausdruecklich so. BgfxModel::GetBoundBox lehnt jeden Index ausser 0 ab
(BgfxModelBounds.cpp:504) und laesst die Ausgabeparameter dabei -- laut eigenem
Kommentar absichtlich -- UNBERUEHRT. Der Rueckgabewert wird an der Aufrufstelle
nicht geprueft, also ist v3Center im bgfx-Backend Stackmuell.

v3Center geht anschliessend in JEDEN Bildschritt ein: TGroundItemInstance::Update
bildet daraus qAdjust und addiert das auf die Position. Der Gegenstand landet
folglich an einer beliebigen Stelle (oder auf NaN) statt auf dem Boden -- er ist
unsichtbar, sein Namensschild sitzt woanders, und GetGroundItemPosition liefert
denselben Unsinn an die Aufhebe-Logik, weshalb auch der Mausklick nichts bewirkt.

Der Originalclient hat den Fehler nicht: CGraphicThingInstance::GetBoundBox
(ThingInstance.cpp:596-601) SCHREIBT immer -- erst min=+100000/max=-100000, dann
der Durchlauf ueber die Modellinstanzen. Ein Modell ohne Geometrie ergibt dort
Mitte 0, nicht Stackmuell.

AENDERUNG
---------
1. PythonItem.cpp -- vMin/vMax auf 0 vorbelegen und den Rueckgabewert auswerten.
   Bei "kein Quader" ist die Mitte 0, also genau das, was der Originalclient in
   demselben Fall liefert.
2. BgfxModelBounds.cpp -- uint32_t(-1) als "der gesamte Quader" annehmen, wie es
   EterModel tut. Heute antwortet die Funktion trotzdem mit false, weil fuer
   ueber LoadModelPartFromPath geladene Modelle nie ein Huellzustand gebaut wird;
   die Vertragsverletzung verschwindet damit aber, und sobald der Huellzustand da
   ist, antwortet sie von selbst richtig.

Das Skript ist mehrfach ausfuehrbar: es prueft auf die NEUE Form und tut dann nichts.
"""
import io
import sys

ITEM = "/opt/m2wasm/src/PyLib/src/bindings/item/PythonItem.cpp"
BOUNDS = "/opt/m2wasm/src/EngineLib/src/bgfx/models/BgfxModelBounds.cpp"

# ── 1. die Aufrufstelle ──────────────────────────────────────────────────────
ITEM_OLD = """		D3DXVECTOR3 vMin, vMax;
		pGroundItemInstance->ThingInstance->GetBoundBox(uint32_t(-1), vMin.x, vMin.y, vMin.z, vMax.x, vMax.y, vMax.z);
		pGroundItemInstance->v3Center = (vMin + vMax) * 0.5f;
"""

ITEM_NEW = """		// ── VORBELEGT, UND DAS IST DER GANZE FEHLER GEWESEN ──
		//
		// D3DXVECTOR3 hat `= default` als Standardkonstruktor (D3DXMath.h:232), also
		// waren diese beiden hier UNBESTIMMT, und GetBoundBox laesst seine
		// Ausgabeparameter bei einer Absage ausdruecklich unberuehrt
		// (BgfxModelBounds.cpp, Kommentar ueber der Funktion). Im bgfx-Backend sagt es
		// immer ab -- fuer ein ueber LoadModelPartFromPath geladenes Modell wird nie
		// ein Huellzustand gebaut -- und v3Center war damit Stackmuell.
		//
		// v3Center ist keine Nebensache: TGroundItemInstance::Update baut daraus
		// qAdjust und addiert es in JEDEM Bildschritt auf die Position. Mit Muell darin
		// liegt der Gegenstand irgendwo im Nichts statt auf dem Boden -- unsichtbar,
		// mit dem Namensschild an derselben falschen Stelle, und
		// GetGroundItemPosition reicht denselben Wert an die Aufhebe-Logik weiter.
		// Genau so sieht "es liegt nichts da und man kann nichts aufheben" aus.
		//
		// Der Originalclient kommt ohne diese Zeile aus, weil sein
		// CGraphicThingInstance::GetBoundBox (ThingInstance.cpp:596-601) IMMER
		// schreibt: min=+100000/max=-100000 und dann der Durchlauf. Ein Modell ohne
		// Modellinstanz ergibt dort (100000 + -100000)/2 = 0. Diese Null wird hier
		// reproduziert, statt sie dem Zufall zu ueberlassen.
		D3DXVECTOR3 vMin(0.0f, 0.0f, 0.0f), vMax(0.0f, 0.0f, 0.0f);
		if (!pGroundItemInstance->ThingInstance->GetBoundBox(uint32_t(-1),
				vMin.x, vMin.y, vMin.z, vMax.x, vMax.y, vMax.z))
		{
			vMin = D3DXVECTOR3(0.0f, 0.0f, 0.0f);
			vMax = D3DXVECTOR3(0.0f, 0.0f, 0.0f);
		}
		pGroundItemInstance->v3Center = (vMin + vMax) * 0.5f;
"""

# ── 2. der Vertrag im bgfx-Backend ───────────────────────────────────────────
BOUNDS_OLD = """    if (modelIndex != 0)
        return false;
"""

BOUNDS_NEW = """    // uint32_t(-1) IST "KEIN INDEX, GIB DEN GESAMTEN QUADER", NICHT EIN UNGUELTIGER INDEX.
    //
    // Das ist die Kodierung, die IModel::GetBoundBox traegt und die
    // EterModel::GetBoundBox (EterModel.cpp:449-452) ausdruecklich abfangt -- dort
    // fuehrt sie auf CGraphicThingInstance::GetBoundBox(min,max), den Durchlauf ueber
    // alle Modellinstanzen. Hier fiel sie unter "Index != 0" und wurde abgelehnt, und
    // weil eine Absage die Ausgabeparameter unberuehrt laesst, bekam der Aufrufer
    // seine eigenen uninitialisierten Werte zurueck statt einer Antwort.
    // CPythonItem::CreateItem ist genau so an seiner Mitte gescheitert.
    //
    // Es gibt hier nur einen Geometriesatz, also sind "der gesamte Quader" und
    // "Quader 0" dieselbe Antwort; unterschieden werden muss nur zwischen den beiden
    // und einem ECHT ungueltigen Index.
    if (modelIndex != 0 && modelIndex != uint32_t(-1))
        return false;
"""


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


ok = True
ok &= patch(ITEM, ITEM_OLD, ITEM_NEW, "PythonItem.cpp: Huellquader-Mitte")
ok &= patch(BOUNDS, BOUNDS_OLD, BOUNDS_NEW, "BgfxModelBounds.cpp: Index -1 annehmen")
sys.exit(0 if ok else 1)
