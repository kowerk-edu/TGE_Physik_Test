#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Verwendung: ./publish.sh https://github.com/BENUTZER/REPOSITORY.git"
  exit 1
fi

REMOTE_URL="$1"

# Lokale Ersatzliste aktualisieren; GitHub Pages erstellt zusätzlich bei jedem Build
# automatisch data/course-files-auto.json.
python3 tools/update_course_files_manifest.py .

if [[ ! -d .git ]]; then
  git init
fi

git add .
if git diff --cached --quiet; then
  echo "Keine neuen Änderungen zum Committen."
else
  git commit -m "Moodle-Kurs als GitHub Pages veröffentlichen"
fi

git branch -M main
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

git push -u origin main

echo
echo "Upload abgeschlossen. Aktiviere nun GitHub Pages unter:"
echo "Settings → Pages → Deploy from a branch → main → /(root)"
