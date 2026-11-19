# Handball in Hüttenberg – Fan-App

Inoffizielle Web-App für Fans des TV Hüttenberg: Startseite mit Leitsatz, Sprüchen und Bildergalerie,
Spielplan aller Mannschaften, Tabellen, Spielberichte und Spielerstatistik.
Die Daten kommen von nuLiga (Hessischer Handball-Verband) und werden von GitHub Actions
automatisch nach jedem Spieltag aktualisiert.

| Datei / Ordner | Zweck |
| --- | --- |
| `config.json` | Verein, Leitsatz, Sprüche und Mannschaften. Ist die Liste `mannschaften` leer, sucht der Abruf alle Mannschaften über die `verein_id` selbst. |
| `scraper/abruf.py` | Holt Tabellen, Spiele und Spielberichte von nuLiga. |
| `docs/` | Die Web-App, veröffentlicht über GitHub Pages. |
| `docs/bilder/` | Platz für Logo und Fotos (siehe LIESMICH.txt). |
| `github-workflow/update.yml` | Zeitplan; muss nach `.github/workflows/update.yml`. |
