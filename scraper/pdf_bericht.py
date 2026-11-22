"""
Liest einen offiziellen Spielbericht als PDF (Format von handball.net / DHB / HBL)
und liefert dieselbe Struktur wie ein nuLiga-Spielbericht.

Einzeln testen:  python scraper/pdf_bericht.py pfad/zum/bericht.pdf
"""
import json
import re
import sys

import pdfplumber

ZEIT = re.compile(r"^\d{1,2}:\d{2}$")


def schoen(name):
    """'SC DHFK LEIPZIG' -> 'SC DHFK Leipzig' (Abkuerzungen ohne Vokal bleiben gross)."""
    def wort(w):
        if not w.isupper() or len(w) <= 2 or not re.search(r"[AEIOUÄÖÜ]", w):
            return w
        return "/".join(t[:1] + t[1:].lower() for t in w.split("/"))
    return " ".join(wort(w) for w in (name or "").split())


def _zeilen(woerter, toleranz=3):
    """Woerter zu Zeilen gruppieren (gleiche Hoehe)."""
    zeilen = []
    for w in sorted(woerter, key=lambda w: (round(w["top"]), w["x0"])):
        if zeilen and abs(zeilen[-1][0]["top"] - w["top"]) <= toleranz:
            zeilen[-1].append(w)
        else:
            zeilen.append([w])
    return [sorted(z, key=lambda w: w["x0"]) for z in zeilen]


def _kopf(text):
    def such(muster, gruppe=1):
        m = re.search(muster, text, re.M)
        return m.group(gruppe).strip() if m else ""
    erg = re.search(r"Endstand\s+(\d+):(\d+)\s*\((\d+)-(\d+)\)", text)
    zeilen = [z.strip() for z in text.splitlines() if z.strip()]
    return {
        "wettbewerb": zeilen[0].title() if zeilen else "",
        "runde": zeilen[1] if len(zeilen) > 1 else "",
        "spielnummer": such(r"Spielnummer\s+(\S+)"),
        "datum": such(r"Datum\s+(\d{2}\.\d{2}\.\d{4})"),
        "zeit": such(r"Spielbeginn\s+(\d{1,2}:\d{2})"),
        "heim": schoen(such(r"Heimmannschaft\s+(.+)$")),
        "gast": schoen(such(r"Gastmannschaft\s+(.+)$")),
        "ort": schoen(such(r"Spielort\s+(.+?)\s+Gastmannschaft")),
        "ergebnis": f"{erg.group(1)}:{erg.group(2)}" if erg else "",
        "halbzeit": f"{erg.group(3)}:{erg.group(4)}" if erg else "",
    }


def _kader(seite):
    """Spielertabellen ('Heim: …' / 'Gast: …') einer Seite."""
    woerter = seite.extract_words()
    zeilen = _zeilen(woerter)
    teams = []
    i = 0
    while i < len(zeilen):
        z = zeilen[i]
        if z[0]["text"] in ("Heim:", "Gast:"):
            name = " ".join(w["text"] for w in z[1:])
            # Spaltenkoepfe in den naechsten Zeilen suchen
            kopf = {}
            letzte = i
            for j in range(i + 1, min(i + 5, len(zeilen))):
                for w in zeilen[j]:
                    if w["text"] in ("Nr.", "Name", "Tore", "7m/Tore", "Verw.", "Hinausstellungen", "Disq.", "Ber.",
                                     "(ges)", "Strafe", "Zus."):
                        kopf.setdefault(w["text"], w)
                        letzte = j
            j = letzte + 1          # erste Spielerzeile direkt nach dem Tabellenkopf
            if not {"Nr.", "Tore", "7m/Tore", "Verw.", "Hinausstellungen", "Disq."} <= kopf.keys():
                i += 1
                continue
            tore_x = kopf["Tore"]["x0"] - 10
            sm_x = kopf["7m/Tore"]["x0"] - 6
            verw_x = kopf["Verw."]["x0"] - 10
            hin_x = kopf["Verw."]["x1"] + 5   # Zeitstrafen beginnen links vom (zentrierten) Spaltenkopf
            disq_x = kopf["Disq."]["x0"] - 6
            ber_x = kopf["Ber."]["x0"] - 3 if "Ber." in kopf else 9999
            spieler = []
            k = j
            while k < len(zeilen) and zeilen[k][0]["text"] not in ("Heim:", "Gast:", "Seite"):
                zz = zeilen[k]
                k += 1
                nr = zz[0]["text"]
                if not nr.isdigit() or zz[0]["x0"] > kopf["Nr."]["x1"] + 15:
                    continue                      # Offizielle (A–E) und Kopfzeilen
                p = {"nr": int(nr), "name": "", "tore": 0, "7m_tore": 0, "7m_versuche": 0,
                     "gelb": 0, "zeitstrafen": 0, "rot": 0}
                namen = []
                for w in zz[1:]:
                    t, x = w["text"], w["x0"]
                    if x < tore_x:
                        namen.append(t)
                    elif x < sm_x and t.isdigit():
                        p["tore"] = int(t)
                    elif "/" in t and re.match(r"^\d+/\d+$", t):
                        a, b = t.split("/")
                        p["7m_tore"], p["7m_versuche"] = int(a), int(b)
                    elif ZEIT.match(t):
                        if verw_x <= x < hin_x:
                            p["gelb"] = 1
                        elif hin_x <= x < disq_x:
                            p["zeitstrafen"] += 1
                        elif disq_x <= x < ber_x:
                            p["rot"] = 1
                p["name"] = " ".join(namen)
                spieler.append(p)
            teams.append({"seite": "Heim" if z[0]["text"] == "Heim:" else "Gast", "name": name, "spieler": spieler})
            i = k
        else:
            i += 1
    return teams


def _verlauf(seite):
    """Spielverlauf: zwei Spalten pro Seite, Namen koennen umbrechen."""
    woerter = seite.extract_words()
    koepfe = [w for w in woerter if w["text"] == "Team"]
    if not koepfe:
        return []
    kopfzeile = [w for w in woerter if abs(w["top"] - koepfe[0]["top"]) < 3]
    haelften = []
    for team_w in sorted(koepfe, key=lambda w: w["x0"]):
        spalten = {"Team": team_w}
        for w in sorted(kopfzeile, key=lambda w: w["x0"]):
            if w["x0"] > team_w["x0"] and w["text"] in ("Zeit", "Stand", "Ereignis", "Person") and w["text"] not in spalten:
                spalten[w["text"]] = w
        haelften.append(spalten)
    ereignisse = []
    for n, s in enumerate(haelften):
        links = s["Team"]["x0"] - 10
        rechts = haelften[n + 1]["Team"]["x0"] - 10 if n + 1 < len(haelften) else seite.width
        bereich = [w for w in woerter if links <= w["x0"] < rechts and w["top"] > s["Team"]["top"] + 5]
        anker = [w for w in bereich if ZEIT.match(w["text"])
                 and s["Zeit"]["x0"] - 8 <= w["x0"] <= s["Zeit"]["x1"] + 4]
        for a in anker:
            mitte = (a["top"] + a["bottom"]) / 2
            zelle = [w for w in bereich if abs((w["top"] + w["bottom"]) / 2 - mitte) <= 12 and w is not a]
            def spalte(von, bis):
                return " ".join(w["text"] for w in sorted(zelle, key=lambda w: (round(w["top"]), w["x0"])) if von <= w["x0"] < bis)
            ereignisse.append({
                "seite": spalte(links, s["Zeit"]["x0"] - 8),
                "zeit": a["text"].zfill(5),
                "stand": spalte(s["Stand"]["x0"] - 6, s["Ereignis"]["x0"] - 4),
                "ereignis": spalte(s["Ereignis"]["x0"] - 4, s["Person"]["x0"] - 3),
                "person": spalte(s["Person"]["x0"] - 3, rechts),
            })
    return ereignisse


def _vereinheitlichen(e):
    """Ereignisnamen an die nuLiga-Schreibweise angleichen."""
    t = e["ereignis"]
    t = {"Siebenmeter Tor": "7m mit Tor", "Siebenmeter Fehlwurf": "7m ohne Tor",
         "Zwei Minuten": "2 Minuten"}.get(t, t)
    if t == "Auszeit" and e["seite"]:
        t = f"Auszeit {e['seite']}"
    e["ereignis"] = t
    return e


def _ergaenze_stand(kopf, teams, ev):
    """Manche Berichte haben keine Zeile 'Endstand'. Dann Ergebnis aus den Toren der Spieler
    und Halbzeitstand aus dem Spielverlauf berechnen."""
    if not kopf["ergebnis"] and len(teams) >= 2:
        heim = next((t for t in teams if t["seite"] == "Heim"), teams[0])
        gast = next((t for t in teams if t["seite"] == "Gast"), teams[1])
        h, g = (sum(p["tore"] for p in t["spieler"]) for t in (heim, gast))
        letzter = next((e["stand"] for e in reversed(ev) if re.match(r"^\d+:\d+$", e.get("stand", ""))), "")
        if h + g > 0:
            kopf["ergebnis"] = f"{h}:{g}"
            if letzter and letzter != kopf["ergebnis"]:
                print(f"  Hinweis: Tore der Spieler ergeben {h}:{g}, letzter Stand im Verlauf ist {letzter}")
        elif letzter:
            kopf["ergebnis"] = letzter
    if not kopf["halbzeit"]:
        pause = next((i for i, e in enumerate(ev) if "halbzeit" in e["ereignis"].lower() and e["zeit"] != "00:00"), None)
        if pause is not None:
            kopf["halbzeit"] = next((e["stand"] for e in reversed(ev[:pause]) if re.match(r"^\d+:\d+$", e.get("stand", ""))), "")


def lies_pdf(pfad):
    with pdfplumber.open(pfad) as pdf:
        kopf = _kopf(pdf.pages[0].extract_text() or "")
        teams, verlauf = [], []
        for seite in pdf.pages:
            teams += _kader(seite)
            verlauf += _verlauf(seite)
    # Doppelte Ereignisse (Kopf-/Fusszeilen) entfernen und nach Spielzeit ordnen
    gesehen, ev = set(), []
    for e in verlauf:
        k = (e["zeit"], e["seite"], e["stand"], e["ereignis"], e["person"])
        if k not in gesehen and e["ereignis"]:
            gesehen.add(k)
            ev.append(_vereinheitlichen(e))
    ev.sort(key=lambda e: (int(e["zeit"].split(":")[0]), int(e["zeit"].split(":")[1])))
    _ergaenze_stand(kopf, teams, ev)
    return {
        "meeting": "pdf-" + (kopf["spielnummer"] or "unbekannt"),
        **kopf,
        "teams": [{"name": schoen(t["name"]), "spieler": t["spieler"]} for t in teams],
        "verlauf": ev,
        "genehmigt": True,
        "quelle": "PDF-Spielbericht",
    }


if __name__ == "__main__":
    print(json.dumps(lies_pdf(sys.argv[1]), ensure_ascii=False, indent=1))
