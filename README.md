# Moodle-Kurs als öffentliche GitHub-Pages-Website

Dieses Repository ist eine statische, direkt aufrufbare Kursansicht. Schülerinnen und Schüler öffnen nur die veröffentlichte GitHub-Pages-Adresse; sie müssen keine Repository-Ordner durchsuchen und nichts lokal ausführen.

## Neue Dateien automatisch anzeigen

Dateien, die später in einen Unterordner von `course-files` hochgeladen und committet werden, werden bei der nächsten GitHub-Pages-Erstellung automatisch erkannt.

Beispiel:

```text
course-files/20561-Ort-Zeit-Diagramm/Ort_Test_Datei.pdf
```

Die Datei erscheint danach im vorhandenen Kursbereich **Ort-Zeit-Diagramm** unter ihrem vollständigen Dateinamen. Dateien in einem bereits bekannten Ordner werden diesem Material zugeordnet. Dateien in einem ganz neuen Ordner erscheinen im zusätzlichen Bereich **Weitere hochgeladene Dateien**.

Die automatische Liste wird durch GitHub Pages/Jekyll aus `data/course-files-auto.json` erzeugt. Deshalb darf im Repository keine Datei namens `.nojekyll` angelegt werden. Nach einem Commit kann die Aktualisierung von GitHub Pages einige Minuten benötigen.

## Veröffentlichen über GitHub Pages

### Möglichkeit A: direkt im GitHub-Browser

1. Auf GitHub ein **öffentliches Repository** anlegen, zum Beispiel `tgm11-physik`.
2. **Add file → Upload files** wählen.
3. Den Inhalt dieses Ordners hochladen; `index.html` muss im Hauptverzeichnis des Repositorys liegen.
4. Änderungen mit **Commit changes** speichern.

Bei späteren Ergänzungen genügt es, die neue Datei in den passenden Unterordner von `course-files` hochzuladen und zu committen.

### Möglichkeit B: mit WSL und Git

```bash
./publish.sh https://github.com/DEIN-BENUTZERNAME/tgm11-physik.git
```

### GitHub Pages einschalten

1. Im Repository **Settings → Pages** öffnen.
2. Unter **Build and deployment** als Quelle **Deploy from a branch** wählen.
3. Branch **main**, Ordner **/(root)** auswählen und speichern.
4. Die Kursadresse lautet danach normalerweise:

```text
https://DEIN-BENUTZERNAME.github.io/tgm11-physik/
```

## Lokal testen

Für die lokale Vorschau wird zuerst die statische Ersatzliste aktualisiert:

```bash
python3 tools/update_course_files_manifest.py .
python3 -m http.server 8000
```

Danach im Browser `http://localhost:8000` öffnen.

## Moodle-Sicherung erneut umwandeln

Das Konverterskript befindet sich im Ordner `tools`:

```bash
python3 tools/mbz_to_github_pages.py /pfad/zum/kurs.mbz neue-webseite
```

## Grenzen einer statischen Website

GitHub Pages führt nur HTML, CSS und JavaScript aus. Moodle-Login, Foren, Bewertungen, Abgaben, Noten und serverseitige Lernfortschrittsdaten sind deshalb nicht enthalten. Kursseiten, PDFs, Bilder, Downloads und selbstständige HTML-Übungen sind verfügbar.

## Datenschutz und Rechte

Alles in einem öffentlichen Repository und auf der GitHub-Pages-Seite ist öffentlich abrufbar. Vor dem Hochladen sollten personenbezogene Daten sowie Materialien ohne Veröffentlichungsrecht entfernt werden.
