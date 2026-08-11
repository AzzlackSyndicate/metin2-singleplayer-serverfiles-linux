#!/usr/bin/env python3
"""Bodengegenstaende anklickbar machen -- ohne den Figurenpfad anzufassen.

BEFUND
------
CPythonItem::__Pick fragt zuerst das Modell (IModel::Intersect) und faellt dann
auf das Namensschild zurueck. Im bgfx-Backend kann der Modellzweig NIE etwas
treffen, aus DREI voneinander unabhaengigen Gruenden -- BgfxModel::Intersect
braucht alle drei, und keiner davon wird im laufenden Client je geliefert:

  1. m_boundPositions  -- der einzige Aufrufer von BuildBoundsFromLoadedGeometry()
     ist BgfxAreaRenderable.cpp:155. Fuer alles, was ueber LoadModelPartFromPath
     geladen wird, wird nie ein Huellzustand gebaut.
  2. m_rayValid        -- SetIntersectRay hat im ganzen Baum KEINEN Aufrufer.
  3. m_boundWorld      -- SetBoundWorldMatrix hat im ganzen Baum KEINEN Aufrufer,
     die Huellpunkte sind also Modellraum und der Strahl waere Weltraum.

Ergebnis: der Klick trifft nur, wenn er zufaellig das Namensschild trifft -- genau
das gemeldete "bei manchen klappt es, bei anderen nicht".

WARUM NICHT, WIE URSPRUENGLICH VORGESCHLAGEN, EINFACH IN LoadModelPartFromPath BAUEN
-----------------------------------------------------------------------------------
Der Vorschlag war falsch, und der Baum sagt selbst warum. BgfxModel.h notiert ueber
BuildBoundsFromLoadedGeometry ausdruecklich:

    "Deliberately NOT called from LoadModelFromPath. Bounds are [M4]'s and the
     loader is [M1]'s; a load that silently built a bound would make every model
     pay for a walk over its vertices that only placed objects need."

Dazu kommt ein Richtigkeitsproblem, das dort nicht steht: BuildBoundsFromLoadedGeometry
liest die Punkte aus dem AKTUELLEN [M1]-Puffer, laeuft dabei aber ueber ALLE Eintraege
in m_m3Parts. Jedes LoadGr2 ueberschreibt [M1] komplett. Bei einem mehrteiligen Modell
-- also bei jeder Figur mit Haar, Waffe und Ruestung -- wuerden die Vertexbereiche der
frueher geladenen Teile in den Puffer des zuletzt geladenen zeigen: plausible, falsche
Zahlen, und obendrein O(n^2) Arbeit bei jedem Ausruesten. Der einzige heutige Aufrufer
ist einteilig, und genau darum stimmt er.

Ein Bodengegenstand ist ebenfalls IMMER einteilig (Teil 0, das Fallmodell, sonst
nichts). Der Aufruf gehoert also an die Stelle, die das weiss -- CreateItem -- und
nicht in den Lader, der es nicht weiss.

AENDERUNG
---------
1. IModel  -- neues virtuelles BuildBoundsFromGeometry() mit `return false;` als
   Standard. Genau das Muster, das IModel.h:178-181 fuer LoadModelPartFromPath
   schon traegt: eter ueberschreibt es NICHT, weil dort Geometrie und Huelle
   dasselbe Granny-Objekt sind und es nichts zu verbinden gibt.
2. BgfxModel -- ueberschreibt es und leitet auf BuildBoundsFromLoadedGeometry.
3. CPythonItem::CreateItem -- ruft es einmal auf, direkt nach dem Fallmodell.
4. CPythonItem::__Pick -- testet zusaetzlich den Mausstrahl gegen die Huellkugel
   des Gegenstands im WELTRAUM. Der Strahl kommt aus IRenderer::GetPickingRay,
   also aus derselben Quelle, die PythonCharacterManager.cpp:304 fuer die Figuren
   benutzt. Der bisherige Intersect-Zweig bleibt VOR dem neuen stehen, damit sich
   am eter-Backend, wo er funktioniert, nichts aendert; das Namensschild bleibt
   der letzte Rueckfall, damit der heute funktionierende Klick weiter funktioniert.

Mehrfach ausfuehrbar: prueft auf die NEUE Form und tut dann nichts.
"""
import io
import sys

IMODEL = "/opt/m2wasm/src/EngineLib/include/EngineLib/backend/IModel.h"
BGFXH = "/opt/m2wasm/src/EngineLib/src/bgfx/models/BgfxModel.h"
BOUNDS = "/opt/m2wasm/src/EngineLib/src/bgfx/models/BgfxModelBounds.cpp"
ITEM = "/opt/m2wasm/src/PyLib/src/bindings/item/PythonItem.cpp"

# ── 1. der Sitz an IModel ────────────────────────────────────────────────────
IMODEL_OLD = """    // ── Model registration ──────────────────────────────────────────
    virtual void RegisterModelThing(uint32_t partIndex, GraphicThingHandle modelThing) = 0;
"""

IMODEL_NEW = """    // ── Huellgeometrie fuer den Strahlentest ────────────────────────
    //
    // Baut die Huellpunkte -- und damit Quader und Kugel -- aus der Geometrie,
    // die bereits geladen ist. Backends, bei denen Geometrie und Huelle ohnehin
    // dasselbe Objekt sind, haben nichts zu tun.
    //
    // DEFAULT false, wie bei LoadModelPartFromPath darueber, und aus demselben
    // Grund: EterModel ueberschreibt es NICHT. Dort haelt CGrannyModelInstance
    // seinen eigenen Quader, GetBoundBox liest ihn direkt, und "false" heisst
    // hier "es gibt nichts zu verbinden", nicht "es fehlt etwas".
    //
    // ES IST BEWUSST DER AUFRUFER, DER FRAGT, UND NICHT DER LADER, DER ES VON
    // SELBST TUT. Der Durchlauf ueber alle Punkte kostet Zeit und Speicher, den
    // nur Modelle brauchen, die als Klickziel dienen; und er ist nur dann
    // richtig, wenn das Modell EINTEILIG ist -- bei mehreren Teilen zeigen die
    // Vertexbereiche der frueheren Teile in den Puffer des zuletzt geladenen.
    // Beides weiss die Aufrufstelle und der Lader nicht.
    virtual bool BuildBoundsFromGeometry() { return false; }

    // ── Model registration ──────────────────────────────────────────
    virtual void RegisterModelThing(uint32_t partIndex, GraphicThingHandle modelThing) = 0;
"""

# ── 2. die Deklaration im bgfx-Modell ────────────────────────────────────────
BGFXH_OLD = """    void BuildBoundingSphere() override;
    bool Intersect(float& ru, float& rv, float& rt) override;
"""

BGFXH_NEW = """    void BuildBoundingSphere() override;
    bool Intersect(float& ru, float& rv, float& rt) override;
    // Leitet auf BuildBoundsFromLoadedGeometry, den [M4]-Einstieg weiter unten.
    // Siehe IModel::BuildBoundsFromGeometry, warum das der Aufrufer anstoesst.
    bool BuildBoundsFromGeometry() override;
"""

# ── 3. die Umsetzung ─────────────────────────────────────────────────────────
BOUNDS_OLD = """void BgfxModel::BuildBoundingSphere()
{
    BuildBoundingSphereImpl();
}
"""

BOUNDS_NEW = """void BgfxModel::BuildBoundingSphere()
{
    BuildBoundingSphereImpl();
}

// IModel::BuildBoundsFromGeometry -- der backendneutrale Anschluss an [M4].
//
// BuildBoundsFromLoadedGeometry selbst ist absichtlich nicht auf IModel: es ist
// eine Eigenheit dieses Backends, dass Geometrie ([M1]) und Huelle ([M4]) zwei
// getrennte Speicher sind, die jemand verbinden muss. Diese eine Zeile ist diese
// Verbindung, und sie liegt hier statt im Lader -- die Begruendung steht an der
// Deklaration in IModel.h und an BuildBoundsFromLoadedGeometry im Kopf.
bool BgfxModel::BuildBoundsFromGeometry()
{
    return BuildBoundsFromLoadedGeometry();
}
"""

# ── 4a. der Aufruf beim Anlegen des Bodengegenstands ─────────────────────────
ITEM_LOAD_OLD = """	if (const char* dropModelPath = pItemData->GetDropModelFileName())
	{
		if (dropModelPath[0] != '\\0')
			pGroundItemInstance->ThingInstance->LoadModelPartFromPath(0, dropModelPath);
	}
	pGroundItemInstance->ThingInstance->SetModelInstance(0, 0, 0);
"""

ITEM_LOAD_NEW = """	if (const char* dropModelPath = pItemData->GetDropModelFileName())
	{
		if (dropModelPath[0] != '\\0')
			pGroundItemInstance->ThingInstance->LoadModelPartFromPath(0, dropModelPath);
	}

	// ── UND JETZT DIE HUELLE ZU DIESER GEOMETRIE, WEIL DAS NIEMAND SONST TUT ──
	//
	// Zweiter No-op auf eter, zweite ganze Loesung auf bgfx, gleiche Bauart wie die
	// Zeile darueber. Dort sind Geometrie und Huelle zwei getrennte Speicher, und
	// verbunden hat sie bisher nur BgfxAreaRenderable fuer Kartenobjekte. Ohne
	// diesen Aufruf ist BgfxModel::GetBoundBox mangels Huellpunkten immer eine
	// Absage -- weshalb die Mitte des Quaders gleich unten 0 bleibt statt die
	// wirkliche Mitte des Netzes zu sein -- und BgfxModel::Intersect trifft nie.
	//
	// HIER UND NICHT IM LADER, und das ist kein Geschmacksurteil: der Durchlauf
	// liest die Punkte aus dem aktuellen [M1]-Puffer, laeuft aber ueber ALLE Teile
	// des Modells. Bei einem mehrteiligen Modell -- jede Figur mit Haar und Waffe --
	// zeigen die Bereiche der frueheren Teile dann in den Puffer des zuletzt
	// geladenen. Ein Bodengegenstand hat genau ein Teil, das Fallmodell, und ist
	// damit der Fall, fuer den der Durchlauf nachweislich stimmt.
	pGroundItemInstance->ThingInstance->BuildBoundsFromGeometry();

	pGroundItemInstance->ThingInstance->SetModelInstance(0, 0, 0);
"""

# ── 4b. der Strahlentest ─────────────────────────────────────────────────────
ITEM_PICK_OLD = """DWORD CPythonItem::__Pick(const POINT& c_rkPtMouse)
{
	float fu = 0.0f, fv = 0.0f, ft = 0.0f;

	TGroundItemInstanceMap::iterator itor = m_GroundItemInstanceMap.begin();
	for (; itor != m_GroundItemInstanceMap.end(); ++itor)
	{
		TGroundItemInstance * pInstance = itor->second;

		if (pInstance->ThingInstance->Intersect(fu, fv, ft))
		{
			return itor->first;
		}
	}

	CPythonTextTail& rkTextTailMgr=CPythonTextTail::Instance();
	return rkTextTailMgr.Pick(c_rkPtMouse.x, c_rkPtMouse.y);
}
"""

ITEM_PICK_NEW = """// Strahl gegen Kugel, der naechste Treffer vor dem Auge. Gibt die Entfernung
// zurueck, oder -1, wenn der Strahl die Kugel verfehlt oder sie hinter ihm liegt.
// Die Richtung muss nicht normiert sein; das Ergebnis ist dann in Vielfachen
// ihrer Laenge, was fuer den blossen Vergleich zweier Treffer genuegt.
static float __RaySphereDistance(const D3DXVECTOR3& c_rv3Origin,
								 const D3DXVECTOR3& c_rv3Direction,
								 const D3DXVECTOR3& c_rv3Center,
								 float fRadius)
{
	const D3DXVECTOR3 v3ToCenter = c_rv3Center - c_rv3Origin;

	const float fDirLenSq = c_rv3Direction.x * c_rv3Direction.x
						  + c_rv3Direction.y * c_rv3Direction.y
						  + c_rv3Direction.z * c_rv3Direction.z;
	if (fDirLenSq <= 0.0f)
		return -1.0f;

	// Projektion des Mittelpunkts auf den Strahl, in Vielfachen der Richtung.
	const float fProjection = (v3ToCenter.x * c_rv3Direction.x
							 + v3ToCenter.y * c_rv3Direction.y
							 + v3ToCenter.z * c_rv3Direction.z) / fDirLenSq;
	if (fProjection < 0.0f)
		return -1.0f;   // die Kugel liegt hinter dem Auge

	const D3DXVECTOR3 v3Closest = c_rv3Origin + c_rv3Direction * fProjection;
	const D3DXVECTOR3 v3Delta = c_rv3Center - v3Closest;
	const float fDistanceSq = v3Delta.x * v3Delta.x
							+ v3Delta.y * v3Delta.y
							+ v3Delta.z * v3Delta.z;

	if (fDistanceSq > fRadius * fRadius)
		return -1.0f;

	return fProjection;
}

// Ein Radius um den URSPRUNG des Modells, der das Netz in JEDER Drehung umschliesst:
// der weiteste der acht Quaderecken. Bodengegenstaende werden beim Fallen frei
// gedreht, und die Drehung steckt in der Weltmatrix des Modells, an die hier -- ueber
// IModel -- niemand herankommt. Ein drehungsunabhaengiger Radius umgeht das, statt
// eine Matrix zu erraten. Er faellt etwas grosszuegig aus; das ist bei einem Klickziel
// die richtige Richtung, und CharacterRenderable::kCullRadius argumentiert fuer seinen
// festen Radius genauso.
static float __EnclosingRadius(const D3DXVECTOR3& c_rv3Min, const D3DXVECTOR3& c_rv3Max)
{
	float fMaxSq = 0.0f;
	for (int i = 0; i < 8; ++i)
	{
		const float x = (i & 1) ? c_rv3Max.x : c_rv3Min.x;
		const float y = (i & 2) ? c_rv3Max.y : c_rv3Min.y;
		const float z = (i & 4) ? c_rv3Max.z : c_rv3Min.z;
		const float fSq = x * x + y * y + z * z;
		if (fSq > fMaxSq)
			fMaxSq = fSq;
	}
	return sqrtf(fMaxSq);
}

DWORD CPythonItem::__Pick(const POINT& c_rkPtMouse)
{
	float fu = 0.0f, fv = 0.0f, ft = 0.0f;

	TGroundItemInstanceMap::iterator itor = m_GroundItemInstanceMap.begin();
	for (; itor != m_GroundItemInstanceMap.end(); ++itor)
	{
		TGroundItemInstance * pInstance = itor->second;

		if (pInstance->ThingInstance->Intersect(fu, fv, ft))
		{
			return itor->first;
		}
	}

	// ── DER STRAHLENTEST, DEN Intersect() AUF DIESEM BACKEND NICHT LEISTEN KANN ──
	//
	// BgfxModel::Intersect braucht drei Dinge, und im laufenden Client wird KEINES
	// davon je geliefert: die Huellpunkte (erst seit CreateItem sie anfordert),
	// einen Strahl (SetIntersectRay hat baumweit keinen Aufrufer) und die
	// Weltmatrix der Huelle (SetBoundWorldMatrix ebenso). Die beiden letzten sind
	// gemeinsame Zustaende ALLER Modelle, auch der Figuren; sie hier zu verdrahten
	// hiesse, den Figurenpfad mitzuveraendern, und der gehoert jemand anderem.
	//
	// Deshalb wird der Test hier gefuehrt, mit dem Strahl aus dem Renderer -- genau
	// der Quelle, aus der PythonCharacterManager.cpp:304 ihn fuer die Figuren holt.
	// Er steht NACH dem Intersect-Zweig, damit auf eter, wo dieser echt funktioniert
	// und dreiecksgenau ist, weiterhin er die Antwort gibt.
	{
		float ox = 0.0f, oy = 0.0f, oz = 0.0f;
		float dx = 0.0f, dy = 0.0f, dz = 0.0f, range = 0.0f;
		Engine::Instance().GetRenderer().GetPickingRay(ox, oy, oz, dx, dy, dz, range);

		const D3DXVECTOR3 v3Origin(ox, oy, oz);
		const D3DXVECTOR3 v3Direction(dx, dy, dz);

		DWORD dwNearestID = INVALID_ID;
		float fNearest = 0.0f;

		for (itor = m_GroundItemInstanceMap.begin(); itor != m_GroundItemInstanceMap.end(); ++itor)
		{
			TGroundItemInstance * pInstance = itor->second;

			// Vorbelegt und der Rueckgabewert geprueft, aus demselben Grund wie in
			// CreateItem: eine Absage laesst die Ausgabeparameter unberuehrt.
			D3DXVECTOR3 vMin(0.0f, 0.0f, 0.0f), vMax(0.0f, 0.0f, 0.0f);
			if (!pInstance->ThingInstance->GetBoundBox(uint32_t(-1),
					vMin.x, vMin.y, vMin.z, vMax.x, vMax.y, vMax.z))
				continue;

			const float fRadius = __EnclosingRadius(vMin, vMax);
			if (fRadius <= 0.0f)
				continue;

			const Vector3 kPosition = pInstance->ThingInstance->GetPosition();
			const D3DXVECTOR3 v3Center(kPosition.x, kPosition.y, kPosition.z);

			const float fDistance = __RaySphereDistance(v3Origin, v3Direction, v3Center, fRadius);
			if (fDistance < 0.0f)
				continue;

			if (INVALID_ID == dwNearestID || fDistance < fNearest)
			{
				dwNearestID = itor->first;
				fNearest = fDistance;
			}
		}

		if (INVALID_ID != dwNearestID)
			return dwNearestID;
	}

	CPythonTextTail& rkTextTailMgr=CPythonTextTail::Instance();
	return rkTextTailMgr.Pick(c_rkPtMouse.x, c_rkPtMouse.y);
}
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
ok &= patch(IMODEL, IMODEL_OLD, IMODEL_NEW, "IModel.h: BuildBoundsFromGeometry()")
ok &= patch(BGFXH, BGFXH_OLD, BGFXH_NEW, "BgfxModel.h: Deklaration")
ok &= patch(BOUNDS, BOUNDS_OLD, BOUNDS_NEW, "BgfxModelBounds.cpp: Umsetzung")
ok &= patch(ITEM, ITEM_LOAD_OLD, ITEM_LOAD_NEW, "PythonItem.cpp: Huelle anfordern")
ok &= patch(ITEM, ITEM_PICK_OLD, ITEM_PICK_NEW, "PythonItem.cpp: Strahlentest im __Pick")
sys.exit(0 if ok else 1)
