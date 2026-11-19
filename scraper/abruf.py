"""
Holt fuer alle Mannschaften eines Vereins Tabelle, Spiele und Spielberichte
von nuLiga und schreibt sie fuer die Web-App nach docs/data/.

Aufruf (lokal oder in GitHub Actions):  python scraper/abruf.py
Die Mannschaften stehen in config.json im Hauptordner.
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # fuer pdf_bericht.py
DATA = ROOT / "docs" / "data"
BERICHTE = DATA / "berichte"
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

BASE = CFG["verband_url"].rstrip("/") + "/cgi-bin/WebObjects/nuLigaHBDE.woa/wa/"
PAUSE = float(CFG.get("pause_zwischen_abrufen_sekunden", 3))

session = requests.Session()
session.headers["User-Agent"] = "Vereins-Statistik (privates Fan-Projekt, Abruf nach Spieltagen)"


# ---------------------------------------------------------------- Hilfen
def hole(url):
    """Seite abrufen, mit Pause und bis zu 3 Versuchen."""
    for versuch in range(3):
        try:
            time.sleep(PAUSE)
            r = session.get(url, timeout=30)
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            return BeautifulSoup(r.text, "html.parser")
        except requests.RequestException as e:
            print(f"  Fehler bei Versuch {versuch + 1}: {e}")
    raise RuntimeError(f"Seite nicht erreichbar: {url}")


def text(el):
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def zahl(s):
    m = re.search(r"-?\d+", s or "")
    return int(m.group()) if m else 0


def norm(name):
    return " ".join((name or "").lower().split())


def uhrzeit(s):
    """'18:00 v' oder '12:15 t/h' -> '18:00' bzw. '12:15'"""
    m = re.search(r"\d{1,2}:\d{2}", s or "")
    return m.group().zfill(5) if m else ""


def tabellen_mit(soup, pflicht):
    """(Spaltenindex, Datenzeilen) fuer jede Tabelle mit diesen Spaltennamen."""
    treffer = []
    for tab in soup.find_all("table"):
        zeilen = tab.find_all("tr")
        for i, z in enumerate(zeilen):
            namen = [text(c) for c in z.find_all(["th", "td"])]
            if all(p in namen for p in pflicht):
                idx = {}
                for n, name in enumerate(namen):
                    if name and name not in idx:
                        idx[name] = n
                treffer.append((tab, idx, zeilen[i + 1:]))
                break
    return treffer


def zellen(zeile):
    return zeile.find_all(["td", "th"])


def wert(c, idx, k):
    return text(c[idx[k]]) if k in idx and idx[k] < len(c) else ""


def url_param(team):
    return f"championship={quote_plus(team['saison'])}&group={team['staffel']}"


# ---------------------------------------------------------------- Liga
def lade_tabelle(team):
    soup = hole(f"{BASE}groupPage?{url_param(team)}")
    t = tabellen_mit(soup, ["Rang", "Mannschaft", "Punkte"])
    if not t:
        return [], ""
    _, idx, zeilen = t[0]
    tabelle = []
    for z in zeilen:
        c = zellen(z)
        name = wert(c, idx, "Mannschaft")
        if not name or not wert(c, idx, "Rang"):
            continue
        tabelle.append({
            "rang": zahl(wert(c, idx, "Rang")), "mannschaft": name,
            "spiele": zahl(wert(c, idx, "Begegnungen")),
            "s": zahl(wert(c, idx, "S")), "u": zahl(wert(c, idx, "U")), "n": zahl(wert(c, idx, "N")),
            "tore": wert(c, idx, "Tore"), "diff": wert(c, idx, "+/-"), "punkte": wert(c, idx, "Punkte"),
        })
    titel = text(soup.find("h1"))
    return tabelle, titel


def lade_spiele(team, eigener_name):
    url = f"{BASE}groupPage?displayTyp=vorrunde&displayDetail=meetings&{url_param(team)}"
    soup = hole(url)
    t = tabellen_mit(soup, ["Datum", "Heimmannschaft", "Gastmannschaft"])
    if not t:
        return []
    _, idx, zeilen = t[0]
    spiele, datum, tag = [], "", ""
    for z in zeilen:
        c = zellen(z)
        if len(c) <= idx["Gastmannschaft"]:
            continue
        datum = wert(c, idx, "Datum") or datum
        tag = wert(c, idx, "Tag") or tag
        heim, gast = wert(c, idx, "Heimmannschaft"), wert(c, idx, "Gastmannschaft")
        if norm(eigener_name) not in (norm(heim), norm(gast)):
            continue
        link = z.find("a", href=re.compile("groupMeetingReport"))
        meeting = ergebnis = None
        if link:
            m = re.search(r"meeting=(\d+)", link["href"])
            meeting = m.group(1) if m else None
            if re.match(r"^\d+:\d+$", text(link)):
                ergebnis = text(link)
        spiele.append({
            "tag": tag, "datum": datum, "zeit": uhrzeit(wert(c, idx, "Zeit")),
            "nr": wert(c, idx, "Nr."), "heim": heim, "gast": gast,
            "ergebnis": ergebnis, "meeting": meeting,
            "genehmigt": bool(z.find("img", title=re.compile("genehmigt", re.I))),
        })
    return spiele


# ---------------------------------------------------------------- Spielbericht
def lies_bericht(team, meeting):
    soup = hole(f"{BASE}groupMeetingReport?meeting={meeting}&{url_param(team)}")
    bericht = {"meeting": meeting, "teams": [], "verlauf": [], "ergebnis": "", "halbzeit": "", "ort": ""}

    for tab, idx, zeilen in tabellen_mit(soup, ["Trikot", "Tore", "7M"]):
        kopf = tab.find_previous("h2")
        spieler = []
        for z in zeilen:
            c = zellen(z)
            trikot = wert(c, idx, "Trikot")
            if not trikot.isdigit():          # A-E sind Offizielle
                continue
            sm = re.match(r"(\d+)\s*/\s*(\d+)", wert(c, idx, "7M"))
            spieler.append({
                "nr": int(trikot),
                "name": wert(c, idx, "Name, Vorname") or wert(c, idx, "Name"),
                "tore": zahl(wert(c, idx, "Tore")),
                "7m_tore": int(sm.group(1)) if sm else 0,
                "7m_versuche": int(sm.group(2)) if sm else 0,
                "gelb": 1 if wert(c, idx, "g. Karte") else 0,
                "zeitstrafen": sum(1 for k in ("1.", "2.", "3.") if wert(c, idx, k)),
                "rot": 1 if (wert(c, idx, "o.B.") or wert(c, idx, "m.B.")) else 0,
            })
        bericht["teams"].append({"name": text(kopf), "spieler": spieler})

    for _, idx, zeilen in tabellen_mit(soup, ["Zeit", "Stand", "Ereignis"]):
        for z in zeilen:
            c = zellen(z)
            if wert(c, idx, "Zeit"):
                bericht["verlauf"].append({
                    "seite": wert(c, idx, "Team"), "zeit": wert(c, idx, "Zeit"),
                    "stand": wert(c, idx, "Stand"), "ereignis": wert(c, idx, "Ereignis"),
                    "person": wert(c, idx, "Person"),
                })

    for z in soup.find_all("tr"):
        c = [text(x) for x in zellen(z)]
        if len(c) >= 2 and c[0] == "Ergebnis":
            m = re.match(r"(\d+:\d+)\s*(?:\((\d+:\d+)\))?", c[1])
            if m:
                bericht["ergebnis"], bericht["halbzeit"] = m.group(1), m.group(2) or ""
        if "Spielort" in c:
            i = c.index("Spielort")
            if i + 1 < len(c):
                bericht["ort"] = c[i + 1]
    return bericht


def hole_bericht(team, sp):
    """Spielbericht aus dem Zwischenspeicher oder von nuLiga."""
    datei = BERICHTE / f"{sp['meeting']}.json"
    if datei.exists():
        b = json.loads(datei.read_text(encoding="utf-8"))
        if b.get("genehmigt"):            # genehmigte Berichte aendern sich nicht mehr
            return b
    print(f"  Spielbericht {sp['datum']} {sp['heim']} – {sp['gast']}")
    b = lies_bericht(team, sp["meeting"])
    b["genehmigt"] = sp["genehmigt"]
    datei.write_text(json.dumps(b, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return b


# ---------------------------------------------------------------- Statistik
def spielerstatistik(berichte, eigener_name):
    stat = {}
    for b in berichte:
        teams = b.get("teams", [])
        unser = next((t for t in teams if norm(t["name"]) == norm(eigener_name)), None)
        if unser is None:
            unser = next((t for t in teams if norm(eigener_name) in norm(t["name"])), None)
        if unser is None:
            continue
        for s in unser["spieler"]:
            p = stat.setdefault(s["name"], {
                "name": s["name"], "nr": s["nr"], "einsaetze": 0, "tore": 0,
                "7m_tore": 0, "7m_versuche": 0, "zeitstrafen": 0, "gelb": 0, "rot": 0,
            })
            p["einsaetze"] += 1
            p["nr"] = s["nr"]
            for k in ("tore", "7m_tore", "7m_versuche", "zeitstrafen", "gelb", "rot"):
                p[k] += s[k]
    liste = sorted(stat.values(), key=lambda p: (-p["tore"], p["name"]))
    for p in liste:
        p["tore_pro_spiel"] = round(p["tore"] / p["einsaetze"], 2) if p["einsaetze"] else 0
    return liste


# ---------------------------------------------------------------- Mannschaften finden
def kategorie_von(label):
    l = label.lower()
    if "weibl" in l:
        return "Weibliche Jugend"
    if "männl" in l or "maennl" in l:
        return "Männliche Jugend"
    if "jugend" in l or "gemischt" in l:
        return "Jugend"
    return "Aktive"


def anzeigename(label):
    """'männliche Jugend B II' -> 'männliche B-Jugend II'"""
    m = re.match(r"(männliche|weibliche|gemischte)\s+Jugend\s+([A-F])(\s+.*)?$", label, re.I)
    if m:
        return f"{m.group(1)} {m.group(2).upper()}-Jugend{m.group(3) or ''}"
    return label


def finde_mannschaften():
    """Alle Mannschaften des Vereins von der nuLiga-Vereinsseite (clubTeams)."""
    soup = hole(f"{BASE}clubTeams?club={CFG['verein_id']}")
    verein = CFG["verein"]
    gefunden = []
    for z in soup.find_all("tr"):
        a = z.find("a", href=re.compile(r"group=\d+"))
        c = zellen(z)
        if not a or len(c) < 2:
            continue
        m = re.search(r"championship=([^&]+)&(?:amp;)?group=(\d+)", a["href"])
        if not m:
            continue
        saison = requests.utils.unquote(m.group(1).replace("+", " "))
        label, liga = text(c[0]), text(c[1])
        # Freundschaftsspiele, Turniere, Qualifikation und Meldelisten ohne Spielbetrieb ueberspringen
        if re.search(r"\b(FS|FrSp|Quali)\b", saison) or re.search(r"Meldeliste|Turnier|Freundschaft", liga):
            continue
        zusatz = re.search(r"\s(II|III|IV|V|VI)$", label)
        gefunden.append({
            "id": f"t{m.group(2)}", "name": anzeigename(label), "kategorie": kategorie_von(label),
            "saison": saison, "staffel": m.group(2),
            "name_in_tabelle": verein + (zusatz.group(0) if zusatz else ""),
        })
    print(f"{len(gefunden)} Mannschaften auf der Vereinsseite gefunden.")
    return gefunden


# ---------------------------------------------------------------- Ablauf
def verarbeite(team):
    tabelle, titel = lade_tabelle(team)
    eigener_name = team.get("name_in_tabelle") or ""
    namen = [r["mannschaft"] for r in tabelle]
    if norm(eigener_name) not in map(norm, namen):
        passend = [n for n in namen if norm(CFG["verein"].split()[-1]) in norm(n)]
        if len(passend) == 1:
            eigener_name = passend[0]
    spiele = lade_spiele(team, eigener_name)
    berichte = []
    for sp in spiele:
        if sp["meeting"] and sp["ergebnis"]:
            b = hole_bericht(team, sp)
            sp["halbzeit"], sp["ort"] = b.get("halbzeit", ""), b.get("ort", "")
            berichte.append(b)
    liga = re.sub(r"^\S+\s+\d{4}/\d{4}\s+", "", titel)
    liga = re.sub(r"\s*Tabelle und Spielplan.*$", "", liga) or team["name"]
    return {
        "id": team["id"], "name": team["name"], "kategorie": team["kategorie"],
        "liga": liga, "eigener_name": eigener_name,
        "quelle": f"{BASE}groupPage?{url_param(team)}",
        "ticker": f"https://hbde-live.liga.nu/nuScoreLive/#/groups/{team['staffel']}",
        "tabelle": tabelle, "spiele": spiele,
        "spieler": spielerstatistik(berichte, eigener_name),
    }


def verarbeite_pdf(team):
    """Mannschaft aus hochgeladenen PDF-Spielberichten (z. B. Bundesliga, Jugendbundesliga)."""
    from pdf_bericht import lies_pdf
    suchwort = norm(team.get("eigener_name") or CFG["verein"].split()[-1])
    berichte, spiele, eigener_name = [], [], ""
    for pfad in sorted((ROOT / team["pdf_ordner"]).glob("*.pdf")):
        print(f"  PDF {pfad.name}")
        try:
            b = lies_pdf(pfad)
        except Exception as e:
            print(f"  FEHLER beim Lesen von {pfad.name}: {e}")
            continue
        namen = [b["heim"], b["gast"]]
        unser = next((n for n in namen if norm(n) == suchwort), None) or next((n for n in namen if suchwort in norm(n)), None)
        if not unser:
            print(f"  übersprungen: {pfad.name} enthält kein Spiel von '{team.get('eigener_name')}'")
            continue
        eigener_name = unser
        (BERICHTE / f"{b['meeting']}.json").write_text(json.dumps(b, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        d, m, y = b["datum"].split(".")
        spiele.append({
            "tag": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][datetime(int(y), int(m), int(d)).weekday()],
            "datum": b["datum"], "zeit": b["zeit"], "nr": b["spielnummer"], "heim": b["heim"], "gast": b["gast"],
            "ergebnis": b["ergebnis"] or None, "meeting": b["meeting"], "genehmigt": True,
            "halbzeit": b["halbzeit"], "ort": b["ort"],
        })
        berichte.append(b)
    if not spiele:
        return None
    spiele.sort(key=lambda s: tuple(reversed(s["datum"].split("."))))
    links = team.get("links", [])
    return {
        "id": team["id"], "name": team["name"], "kategorie": team["kategorie"], "liga": team.get("liga", ""),
        "eigener_name": eigener_name, "extern": True, "hinweis": team.get("hinweis", ""), "links": links,
        "quelle": links[0]["url"] if links else "", "ticker": "",
        "tabelle": [], "spiele": spiele, "spieler": spielerstatistik(berichte, eigener_name),
    }


def main():
    BERICHTE.mkdir(parents=True, exist_ok=True)
    teams, fehler = [], []
    liste = CFG.get("mannschaften") or finde_mannschaften()
    for team in liste:
        print(f"{team['name']} …")
        try:
            teams.append(verarbeite(team))
        except Exception as e:        # eine Mannschaft darf die anderen nicht blockieren
            print(f"  FEHLER bei {team['name']}: {e}")
            fehler.append(team["name"])

    extern = []
    for team in CFG.get("extern", []):
        t = None
        if team.get("pdf_ordner"):
            print(f"{team['name']} (PDF-Spielberichte) …")
            t = verarbeite_pdf(team)
        if t:
            teams.append(t)
        else:
            extern.append(team)
    if not teams:
        raise RuntimeError("Keine Mannschaft konnte geladen werden.")

    daten = {
        "aktualisiert": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "verein": CFG["verein"], "titel": CFG.get("titel", CFG["verein"]),
        "leitsatz": CFG.get("leitsatz", ""), "sprueche": CFG.get("sprueche", []),
        "teams": teams, "extern": extern,
    }
    (DATA / "verein.json").write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")
    spiele = sum(len(t["spiele"]) for t in teams)
    print(f"Fertig: {len(teams)} Mannschaften, {spiele} Spiele.")
    if fehler:
        print("Nicht geladen:", ", ".join(fehler))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("FEHLER:", e)
        sys.exit(1)
