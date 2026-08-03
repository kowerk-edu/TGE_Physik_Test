#!/usr/bin/env python3
"""Convert a Moodle .mbz course backup into a static GitHub Pages site.

The generated site preserves the Moodle section/subsection order, copies course
files, embeds Moodle pages, and opens PDFs/HTML exercises in an in-page viewer.
Server-side Moodle features (forums, grades, completion tracking, logins) cannot
be reproduced on GitHub Pages and are shown as informational entries.

Usage (WSL/Linux/macOS):
    python3 mbz_to_github_pages.py course.mbz output-folder
"""
from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import re
import shutil
import tarfile
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

NULL = "$@NULL@$"


def text(node: ET.Element | None, tag: str, default: str = "") -> str:
    if node is None:
        return default
    value = node.findtext(tag)
    if value in (None, NULL):
        return default
    return value


def safe_extract(archive: Path, target: Path) -> None:
    with tarfile.open(archive, "r:*") as tf:
        target_abs = target.resolve()
        for member in tf.getmembers():
            destination = (target / member.name).resolve()
            if destination != target_abs and target_abs not in destination.parents:
                raise RuntimeError(f"Unsafe archive path: {member.name}")
        tf.extractall(target, filter="data")


def slugify(value: str, fallback: str = "file") -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_value).strip("-._")
    return ascii_value or fallback


def friendly_tile_title(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"_[a-z0-9]{3,5}$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_Logo(?:_\d+)?$", "", stem, flags=re.IGNORECASE)
    stem = stem.replace("_", " ").strip()
    replacements = {
        "Themen Termine": "Themen & Termine",
        "SI Einheiten": "SI-Einheiten",
        "Impuls Kraft": "Impuls & Kraft",
        "Energie": "Energie & Leistung",
    }
    return replacements.get(stem, stem)


def xml_root(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


@dataclass
class FileRecord:
    file_id: str
    contenthash: str
    contextid: str
    component: str
    filearea: str
    itemid: str
    filepath: str
    filename: str
    mimetype: str
    filesize: int


class MoodleConverter:
    def __init__(self, extracted: Path, output: Path):
        self.root = extracted
        self.output = output
        self.files_out = output / "course-files"
        self.data_out = output / "data"
        self.assets_out = output / "assets"
        self.file_records: list[FileRecord] = []
        self.files_by_context: dict[str, list[FileRecord]] = {}
        self.files_by_section: dict[str, list[FileRecord]] = {}
        self.activity_meta: dict[str, dict[str, str]] = {}
        self.sections: dict[str, ET.Element] = {}
        self.subsection_child: dict[str, str] = {}
        self.copied_paths: dict[str, str] = {}

    def run(self) -> dict[str, Any]:
        self.output.mkdir(parents=True, exist_ok=True)
        self.files_out.mkdir(parents=True, exist_ok=True)
        self.data_out.mkdir(parents=True, exist_ok=True)
        self.assets_out.mkdir(parents=True, exist_ok=True)

        self._load_files()
        self._load_activities()
        self._load_sections()
        data = self._build_course_data()
        self._write_site(data)
        return data

    def _load_files(self) -> None:
        files_root = xml_root(self.root / "files.xml")
        for file_node in files_root.findall("file"):
            filename = text(file_node, "filename")
            if not filename or filename == ".":
                continue
            record = FileRecord(
                file_id=file_node.attrib.get("id", ""),
                contenthash=text(file_node, "contenthash"),
                contextid=text(file_node, "contextid"),
                component=text(file_node, "component"),
                filearea=text(file_node, "filearea"),
                itemid=text(file_node, "itemid"),
                filepath=text(file_node, "filepath", "/"),
                filename=filename,
                mimetype=text(file_node, "mimetype") or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                filesize=int(text(file_node, "filesize", "0") or 0),
            )
            self.file_records.append(record)
            self.files_by_context.setdefault(record.contextid, []).append(record)
            if record.component == "format_tiles" and record.filearea == "tilephoto":
                self.files_by_section.setdefault(record.itemid, []).append(record)

    def _load_activities(self) -> None:
        backup = xml_root(self.root / "moodle_backup.xml")
        for node in backup.findall(".//contents/activities/activity"):
            module_id = text(node, "moduleid")
            if not module_id:
                continue
            meta = {child.tag: (child.text or "") for child in node}
            activity_dir = self.root / meta["directory"]
            activity_xml = activity_dir / f"{meta['modulename']}.xml"
            context_id = ""
            activity_instance_id = ""
            if activity_xml.exists():
                act_root = xml_root(activity_xml)
                context_id = act_root.attrib.get("contextid", "")
                activity_instance_id = act_root.attrib.get("id", "")
            meta["contextid"] = context_id
            meta["instanceid"] = activity_instance_id
            meta["path"] = str(activity_dir)
            self.activity_meta[module_id] = meta

            if meta.get("modulename") == "subsection" and activity_instance_id:
                # The matching child section uses component=mod_subsection and itemid=activity instance id.
                self.subsection_child[module_id] = activity_instance_id

    def _load_sections(self) -> None:
        for section_xml in self.root.glob("sections/section_*/section.xml"):
            node = xml_root(section_xml)
            section_id = node.attrib.get("id", "")
            if section_id:
                self.sections[section_id] = node

    def _copy_record(self, record: FileRecord, category: str) -> str:
        cache_key = record.file_id or record.contenthash
        if cache_key in self.copied_paths:
            return self.copied_paths[cache_key]

        source = self.root / "files" / record.contenthash[:2] / record.contenthash
        if not source.exists():
            raise FileNotFoundError(f"Missing Moodle content blob for {record.filename}: {source}")

        folder = self.files_out / slugify(category, "misc")
        folder.mkdir(parents=True, exist_ok=True)
        filename = slugify(record.filename, "file")
        destination = folder / filename
        counter = 2
        while destination.exists() and destination.read_bytes() != source.read_bytes():
            destination = folder / f"{Path(filename).stem}-{counter}{Path(filename).suffix}"
            counter += 1
        if not destination.exists():
            shutil.copy2(source, destination)
        relative = destination.relative_to(self.output).as_posix()
        self.copied_paths[cache_key] = relative
        return relative

    def _file_payload(self, record: FileRecord, category: str) -> dict[str, Any]:
        path = self._copy_record(record, category)
        suffix = Path(record.filename).suffix.lower()
        if record.mimetype == "application/pdf" or suffix == ".pdf":
            kind = "pdf"
        elif record.mimetype == "text/html" or suffix in {".html", ".htm"}:
            kind = "html"
        elif record.mimetype.startswith("image/"):
            kind = "image"
        else:
            kind = "file"
        return {
            "title": Path(record.filename).stem.replace("_", " "),
            "filename": record.filename,
            "path": path,
            "mime": record.mimetype,
            "size": record.filesize,
            "kind": kind,
        }

    def _replace_pluginfile(self, raw_html: str, context_id: str, category: str) -> str:
        result = raw_html or ""
        for record in self.files_by_context.get(context_id, []):
            path = self._copy_record(record, category)
            encoded_names = {
                record.filename,
                record.filename.replace(" ", "%20"),
            }
            for name in encoded_names:
                result = result.replace(f"@@PLUGINFILE@@/{name}", path)
        result = result.replace("@@PLUGINFILE@@/", "")
        return result

    def _activity_payload(self, module_id: str, depth: int = 0) -> dict[str, Any]:
        meta = self.activity_meta.get(module_id)
        if not meta:
            return {"type": "notice", "title": f"Unbekannte Aktivität {module_id}", "message": "Aktivität konnte nicht gelesen werden."}
        activity_type = meta.get("modulename", "")
        title = meta.get("title") or "Ohne Titel"
        activity_dir = Path(meta["path"])
        context_id = meta.get("contextid", "")
        category = f"{module_id}-{title}"

        if activity_type == "subsection":
            instance_id = meta.get("instanceid", "")
            child = next((s for s in self.sections.values() if text(s, "component") == "mod_subsection" and text(s, "itemid") == instance_id), None)
            children: list[dict[str, Any]] = []
            summary = ""
            if child is not None:
                summary = text(child, "summary")
                children = [self._activity_payload(mid, depth + 1) for mid in self._sequence(child)]
            return {
                "type": "subsection",
                "title": title,
                "summary": summary,
                "items": children,
                "empty": not children and not summary.strip(),
            }

        if activity_type == "resource":
            resource_xml = activity_dir / "resource.xml"
            intro = ""
            if resource_xml.exists():
                resource_node = xml_root(resource_xml).find("resource")
                intro = text(resource_node, "intro")
            records = self.files_by_context.get(context_id, [])
            files = [self._file_payload(record, category) for record in records]
            if not files:
                return {"type": "notice", "title": title, "message": "Die Datei ist in der Sicherung nicht enthalten."}
            primary = files[0]
            return {
                "type": "resource",
                "title": title,
                "intro": intro,
                "resource": primary,
                "files": files,
            }

        if activity_type == "folder":
            records = self.files_by_context.get(context_id, [])
            return {
                "type": "folder",
                "title": title,
                "files": [self._file_payload(record, category) for record in records],
            }

        if activity_type == "page":
            page_xml = activity_dir / "page.xml"
            page_node = xml_root(page_xml).find("page") if page_xml.exists() else None
            content = text(page_node, "content")
            content = self._replace_pluginfile(content, context_id, category)
            return {
                "type": "page",
                "title": title,
                "html": content,
            }

        if activity_type == "forum":
            return {
                "type": "notice",
                "title": title,
                "message": "Dieses Moodle-Forum benötigt eine Server-Datenbank und ist in der statischen GitHub-Pages-Version nicht interaktiv.",
                "icon": "forum",
            }

        return {
            "type": "notice",
            "title": title,
            "message": f"Der Moodle-Aktivitätstyp „{activity_type}“ wird in einer statischen Website nicht ausgeführt.",
        }

    @staticmethod
    def _sequence(section: ET.Element) -> list[str]:
        raw = text(section, "sequence")
        return [part.strip() for part in raw.split(",") if part.strip()]

    def _section_title(self, section: ET.Element, tile_record: FileRecord | None) -> str:
        name = text(section, "name").strip()
        if name:
            return name
        if tile_record:
            return friendly_tile_title(tile_record.filename)
        number = text(section, "number")
        return f"Thema {number}" if number else "Thema"

    def _build_course_data(self) -> dict[str, Any]:
        course_node = xml_root(self.root / "course" / "course.xml")
        backup_node = xml_root(self.root / "moodle_backup.xml")
        backup_info = backup_node.find("information")

        top_sections = [
            section for section in self.sections.values()
            if text(section, "component") not in {"mod_subsection"}
        ]
        top_sections.sort(key=lambda s: int(text(s, "number", "9999")))

        section_payloads: list[dict[str, Any]] = []
        for section in top_sections:
            section_id = section.attrib.get("id", "")
            tile_records = self.files_by_section.get(section_id, [])
            tile_record = tile_records[0] if tile_records else None
            tile_path = self._copy_record(tile_record, f"section-{section_id}") if tile_record else ""
            title = self._section_title(section, tile_record)
            items = [self._activity_payload(mid) for mid in self._sequence(section)]
            section_payloads.append({
                "id": section_id,
                "number": int(text(section, "number", "0") or 0),
                "title": title,
                "summary": text(section, "summary"),
                "image": tile_path,
                "items": items,
                "empty": not items and not text(section, "summary").strip(),
            })

        return {
            "course": {
                "title": text(course_node, "fullname", "Moodle-Kurs"),
                "shortname": text(course_node, "shortname"),
                "category": text(course_node.find("category"), "name"),
                "summary": text(course_node, "summary"),
                "moodleRelease": text(backup_info, "moodle_release"),
                "backupDate": text(backup_info, "backup_date"),
            },
            "sections": section_payloads,
            "limitations": [
                "Keine Anmeldung oder Benutzerkonten",
                "Keine Noten, Abgaben oder Lernfortschritts-Synchronisierung",
                "Foren und Ankündigungen sind nur als Hinweis sichtbar",
            ],
        }

    def _write_site(self, data: dict[str, Any]) -> None:
        (self.data_out / "course-data.js").write_text(
            "window.COURSE_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
            encoding="utf-8",
        )
        (self.output / "index.html").write_text(INDEX_HTML, encoding="utf-8")
        (self.output / "404.html").write_text(REDIRECT_404, encoding="utf-8")
        (self.assets_out / "styles.css").write_text(STYLES_CSS, encoding="utf-8")
        (self.assets_out / "app.js").write_text(APP_JS, encoding="utf-8")

        manifest = []
        for path in sorted(p for p in self.files_out.rglob("*") if p.is_file()):
            mime, _ = mimetypes.guess_type(path.name)
            manifest.append({
                "path": path.relative_to(self.output).as_posix(),
                "filename": path.name,
                "size": path.stat().st_size,
                "mime": mime or "application/octet-stream",
            })
        (self.data_out / "course-files.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.data_out / "course-files-auto.json").write_text(COURSE_FILES_AUTO_JSON, encoding="utf-8")

        nojekyll = self.output / ".nojekyll"
        if nojekyll.exists():
            nojekyll.unlink()

        tools_out = self.output / "tools"
        tools_out.mkdir(exist_ok=True)
        updater = tools_out / "update_course_files_manifest.py"
        updater.write_text(UPDATE_MANIFEST_PY, encoding="utf-8")
        os.chmod(updater, 0o755)

        (self.output / "README.md").write_text(README_MD, encoding="utf-8")
        (self.output / "publish.sh").write_text(PUBLISH_SH, encoding="utf-8")
        os.chmod(self.output / "publish.sh", 0o755)


INDEX_HTML = r'''<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Öffentliche statische Kursansicht">
  <meta name="theme-color" content="#115ea3">
  <title>Kurs</title>
  <link rel="stylesheet" href="assets/styles.css">
  <script defer src="data/course-data.js"></script>
  <script defer src="assets/app.js"></script>
</head>
<body>
  <a class="skip-link" href="#main">Zum Inhalt springen</a>
  <header class="site-header">
    <div class="header-inner">
      <button class="menu-button" id="menuButton" type="button" aria-label="Navigation öffnen" aria-controls="sidebar" aria-expanded="false">☰</button>
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">φ</span>
        <div>
          <p class="eyebrow">Öffentlicher Kurs</p>
          <h1 id="courseTitle">Moodle-Kurs</h1>
        </div>
      </div>
      <div class="header-actions">
        <label class="search" aria-label="Kurs durchsuchen">
          <span aria-hidden="true">⌕</span>
          <input id="searchInput" type="search" placeholder="Kurs durchsuchen…" autocomplete="off">
        </label>
      </div>
    </div>
  </header>

  <div class="layout">
    <aside class="sidebar" id="sidebar" aria-label="Kursnavigation">
      <div class="course-meta">
        <strong id="courseShortname"></strong>
        <span id="courseCategory"></span>
      </div>
      <nav id="sectionNav"></nav>
      <div class="sidebar-note">
        <strong>Direkter Zugriff</strong>
        <span>Keine Anmeldung erforderlich.</span>
      </div>
    </aside>

    <main id="main" class="main-content" tabindex="-1">
      <section class="hero" id="hero">
        <div>
          <p class="eyebrow">TGM11 · Physik</p>
          <h2>Alle Materialien an einem Ort</h2>
          <p>Öffne Themen, PDFs und Übungen direkt im Browser. Es müssen keine GitHub-Ordner geöffnet und keine Dateien lokal ausgeführt werden.</p>
        </div>
        <div class="hero-symbol" aria-hidden="true">v = Δs/Δt</div>
      </section>

      <div id="searchStatus" class="search-status" role="status" aria-live="polite"></div>
      <section id="courseGrid" class="course-grid" aria-label="Kursbereiche"></section>
      <section id="emptySearch" class="empty-state" hidden>
        <div aria-hidden="true">⌕</div>
        <h2>Keine Treffer</h2>
        <p>Versuche einen anderen Suchbegriff.</p>
      </section>
    </main>
  </div>

  <div class="modal" id="viewerModal" hidden>
    <div class="modal-backdrop" data-close-modal></div>
    <section class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="viewerTitle">
      <header class="modal-header">
        <div>
          <p class="eyebrow" id="viewerType">Material</p>
          <h2 id="viewerTitle">Material</h2>
        </div>
        <div class="modal-actions">
          <a id="openNewTab" class="button secondary" href="#" target="_blank" rel="noopener" hidden>Neuer Tab</a>
          <a id="downloadFile" class="button secondary" href="#" download hidden>Download</a>
          <button class="icon-button" type="button" data-close-modal aria-label="Schließen">×</button>
        </div>
      </header>
      <div class="modal-body" id="viewerBody"></div>
    </section>
  </div>

  <template id="sectionTemplate">
    <article class="section-card">
      <button class="section-cover" type="button" aria-expanded="false">
        <span class="cover-image" aria-hidden="true"></span>
        <span class="cover-overlay"></span>
        <span class="cover-text">
          <span class="section-number"></span>
          <strong class="section-title"></strong>
          <small class="section-count"></small>
        </span>
        <span class="chevron" aria-hidden="true">⌄</span>
      </button>
      <div class="section-content" hidden>
        <div class="section-summary prose"></div>
        <div class="section-items"></div>
      </div>
    </article>
  </template>

  <template id="subsectionTemplate">
    <section class="subsection">
      <button class="subsection-toggle" type="button" aria-expanded="false">
        <span><span class="item-icon" aria-hidden="true">▦</span><strong class="subsection-title"></strong></span>
        <span class="subsection-meta"></span>
        <span class="chevron" aria-hidden="true">⌄</span>
      </button>
      <div class="subsection-body" hidden>
        <div class="subsection-summary prose"></div>
        <div class="subsection-items"></div>
      </div>
    </section>
  </template>
</body>
</html>
'''

REDIRECT_404 = r'''<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Weiterleitung</title><script>const parts=location.pathname.split('/').filter(Boolean);const base=location.hostname.endsWith('github.io')&&parts.length?`/${parts[0]}/`:'/';location.replace(base);</script></head><body><a href="./">Zur Kursstartseite</a></body></html>'''

STYLES_CSS = r''':root {
  --blue: #115ea3;
  --blue-dark: #0a467d;
  --blue-soft: #eaf3fb;
  --ink: #172033;
  --muted: #667085;
  --surface: #ffffff;
  --surface-alt: #f5f7fa;
  --border: #dbe2ea;
  --success: #087a55;
  --shadow: 0 10px 30px rgba(17, 38, 64, 0.10);
  --radius: 18px;
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  color: var(--ink);
  background: var(--surface-alt);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.55;
}
button, input { font: inherit; }
a { color: var(--blue); }
[hidden] { display: none !important; }

.skip-link {
  position: fixed;
  top: 8px;
  left: 8px;
  z-index: 100;
  padding: 10px 14px;
  border-radius: 10px;
  color: white;
  background: var(--blue-dark);
  transform: translateY(-150%);
}
.skip-link:focus { transform: translateY(0); }

.site-header {
  position: sticky;
  top: 0;
  z-index: 30;
  border-bottom: 1px solid rgba(255,255,255,.16);
  color: white;
  background: linear-gradient(120deg, var(--blue-dark), var(--blue));
  box-shadow: 0 5px 20px rgba(0, 38, 74, .18);
}
.header-inner {
  min-height: 76px;
  padding: 12px 24px;
  display: flex;
  align-items: center;
  gap: 16px;
}
.brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
.brand-mark {
  width: 44px;
  height: 44px;
  border-radius: 14px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  font: 700 1.5rem Georgia, serif;
  color: var(--blue-dark);
  background: white;
}
.brand h1 { margin: 0; font-size: clamp(1rem, 2vw, 1.35rem); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.eyebrow { margin: 0 0 2px; font-size: .72rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; opacity: .78; }
.header-actions { margin-left: auto; }
.search {
  width: min(360px, 36vw);
  min-height: 44px;
  padding: 0 14px;
  display: flex;
  align-items: center;
  gap: 9px;
  border: 1px solid rgba(255,255,255,.35);
  border-radius: 13px;
  background: rgba(255,255,255,.14);
  backdrop-filter: blur(8px);
}
.search input { width: 100%; border: 0; outline: 0; color: white; background: transparent; }
.search input::placeholder { color: rgba(255,255,255,.78); }
.menu-button { display: none; border: 0; color: white; background: transparent; font-size: 1.5rem; cursor: pointer; }

.layout { display: grid; grid-template-columns: 272px minmax(0, 1fr); min-height: calc(100vh - 76px); }
.sidebar {
  position: sticky;
  top: 76px;
  align-self: start;
  height: calc(100vh - 76px);
  padding: 22px 16px;
  overflow-y: auto;
  border-right: 1px solid var(--border);
  background: var(--surface);
}
.course-meta { padding: 0 10px 16px; display: grid; }
.course-meta strong { color: var(--blue-dark); }
.course-meta span { color: var(--muted); font-size: .86rem; }
#sectionNav { display: grid; gap: 4px; }
.nav-link {
  width: 100%;
  padding: 9px 10px;
  display: flex;
  align-items: center;
  gap: 10px;
  border: 0;
  border-radius: 10px;
  text-align: left;
  color: var(--ink);
  background: transparent;
  cursor: pointer;
}
.nav-link:hover, .nav-link:focus-visible { color: var(--blue-dark); background: var(--blue-soft); }
.nav-number { width: 24px; height: 24px; display: grid; place-items: center; border-radius: 7px; color: var(--blue-dark); background: var(--blue-soft); font-size: .75rem; font-weight: 800; }
.nav-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .9rem; }
.sidebar-note { margin-top: 22px; padding: 14px; display: grid; border-radius: 13px; color: var(--success); background: #eaf8f3; font-size: .82rem; }

.main-content { width: min(1260px, 100%); margin: 0 auto; padding: 28px clamp(18px, 3vw, 42px) 64px; }
.hero {
  min-height: 210px;
  padding: clamp(24px, 4vw, 48px);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 28px;
  overflow: hidden;
  border-radius: 24px;
  color: white;
  background: radial-gradient(circle at 90% 10%, rgba(255,255,255,.24), transparent 26%), linear-gradient(130deg, #0b4c86, #1670cc);
  box-shadow: var(--shadow);
}
.hero h2 { margin: 0 0 10px; font-size: clamp(1.8rem, 4vw, 3.2rem); line-height: 1.05; }
.hero p:not(.eyebrow) { max-width: 700px; margin: 0; opacity: .92; }
.hero-symbol { flex: 0 0 auto; padding: 28px; border: 1px solid rgba(255,255,255,.28); border-radius: 22px; font: italic 700 clamp(1.3rem, 3vw, 2.4rem) Georgia, serif; background: rgba(255,255,255,.1); transform: rotate(-3deg); }
.search-status { min-height: 24px; margin: 20px 2px 10px; color: var(--muted); font-size: .9rem; }
.course-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }

.section-card { overflow: hidden; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); box-shadow: 0 7px 22px rgba(17,38,64,.07); transition: transform .18s ease, box-shadow .18s ease; }
.section-card:hover { transform: translateY(-2px); box-shadow: var(--shadow); }
.section-card.open { grid-column: 1 / -1; }
.section-cover {
  position: relative;
  width: 100%;
  min-height: 210px;
  padding: 0;
  overflow: hidden;
  border: 0;
  display: block;
  text-align: left;
  color: white;
  background: linear-gradient(135deg, #205b8e, #163e67);
  cursor: pointer;
}
.cover-image { position: absolute; inset: 0; background-position: center; background-size: cover; transform: scale(1.02); transition: transform .3s ease; }
.section-cover:hover .cover-image { transform: scale(1.06); }
.cover-overlay { position: absolute; inset: 0; background: linear-gradient(to top, rgba(7,25,46,.92), rgba(7,25,46,.15) 70%); }
.cover-text { position: absolute; inset: auto 22px 20px; display: grid; gap: 2px; }
.section-number { font-size: .72rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; opacity: .82; }
.section-title { font-size: clamp(1.25rem, 2.3vw, 1.75rem); line-height: 1.15; }
.section-count { opacity: .78; }
.chevron { transition: transform .2s ease; }
.section-cover > .chevron { position: absolute; right: 19px; bottom: 20px; width: 34px; height: 34px; display: grid; place-items: center; border-radius: 50%; background: rgba(255,255,255,.18); font-size: 1.3rem; }
.section-card.open .section-cover > .chevron, [aria-expanded="true"] > .chevron { transform: rotate(180deg); }
.section-content { padding: 22px; }
.section-summary:not(:empty) { margin-bottom: 18px; padding: 16px; border-radius: 13px; background: var(--blue-soft); }
.section-items { display: grid; gap: 12px; }

.subsection { overflow: hidden; border: 1px solid var(--border); border-radius: 14px; background: #fbfcfe; }
.subsection-toggle { width: 100%; min-height: 58px; padding: 12px 15px; display: grid; grid-template-columns: minmax(0,1fr) auto auto; align-items: center; gap: 10px; border: 0; text-align: left; color: var(--ink); background: transparent; cursor: pointer; }
.subsection-toggle:hover { background: var(--blue-soft); }
.subsection-toggle > span:first-child { display: flex; align-items: center; gap: 10px; min-width: 0; }
.subsection-title { overflow-wrap: anywhere; }
.subsection-meta { color: var(--muted); font-size: .82rem; }
.subsection-body { padding: 0 14px 14px; }
.subsection-items { display: grid; gap: 9px; }
.subsection-summary:not(:empty) { margin-bottom: 10px; }

.material {
  width: 100%;
  min-height: 62px;
  padding: 11px 13px;
  display: grid;
  grid-template-columns: 40px minmax(0,1fr) auto;
  align-items: center;
  gap: 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
  text-align: left;
  color: var(--ink);
  background: white;
}
button.material { cursor: pointer; }
button.material:hover, button.material:focus-visible { border-color: #8cb8dd; background: #f7fbff; }
.item-icon { width: 36px; height: 36px; display: inline-grid; place-items: center; border-radius: 10px; color: var(--blue-dark); background: var(--blue-soft); font-weight: 900; }
.material-copy { min-width: 0; display: grid; }
.material-title { font-weight: 750; overflow-wrap: anywhere; }
.material-meta { color: var(--muted); font-size: .8rem; }
.material-action { color: var(--blue); font-size: .82rem; font-weight: 750; }
.notice { background: #fffaf0; border-color: #ead8ad; }
.notice .item-icon { color: #7c5b16; background: #f7eac8; }
.empty-material { color: var(--muted); background: var(--surface-alt); }
.folder-list { padding: 0; margin: 8px 0 0; display: grid; gap: 8px; list-style: none; }

.prose { overflow-wrap: anywhere; }
.prose > :first-child { margin-top: 0; }
.prose > :last-child { margin-bottom: 0; }
.prose img { max-width: 100%; height: auto; }
.prose table { width: 100% !important; border-collapse: collapse; }
.prose td, .prose th { padding: 8px; border: 1px solid var(--border); vertical-align: top; }

.empty-state { padding: 60px 20px; text-align: center; color: var(--muted); }
.empty-state div { font-size: 3rem; }
.empty-state h2 { margin-bottom: 2px; color: var(--ink); }

.modal { position: fixed; inset: 0; z-index: 60; }
.modal-backdrop { position: absolute; inset: 0; background: rgba(8,20,36,.72); backdrop-filter: blur(4px); }
.modal-panel { position: absolute; inset: 24px; display: grid; grid-template-rows: auto minmax(0,1fr); overflow: hidden; border-radius: 20px; background: white; box-shadow: 0 24px 80px rgba(0,0,0,.35); }
.modal-header { min-height: 76px; padding: 13px 18px; display: flex; align-items: center; gap: 16px; border-bottom: 1px solid var(--border); }
.modal-header h2 { margin: 0; font-size: clamp(1rem, 2vw, 1.35rem); }
.modal-actions { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.button { min-height: 40px; padding: 8px 12px; display: inline-flex; align-items: center; justify-content: center; border-radius: 10px; text-decoration: none; font-weight: 750; }
.button.secondary { color: var(--blue-dark); background: var(--blue-soft); }
.icon-button { width: 42px; height: 42px; border: 0; border-radius: 12px; color: var(--ink); background: var(--surface-alt); font-size: 1.7rem; cursor: pointer; }
.modal-body { min-height: 0; overflow: auto; background: #eef1f5; }
.viewer-frame { width: 100%; height: 100%; min-height: 520px; border: 0; background: white; }
.page-view { width: min(900px, calc(100% - 30px)); margin: 20px auto; padding: clamp(18px, 4vw, 42px); border-radius: 16px; background: white; box-shadow: var(--shadow); }
body.modal-open { overflow: hidden; }

@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .menu-button { display: block; }
  .sidebar { position: fixed; top: 76px; bottom: 0; left: 0; z-index: 40; width: min(300px, 86vw); height: auto; transform: translateX(-105%); transition: transform .2s ease; box-shadow: var(--shadow); }
  .sidebar.open { transform: translateX(0); }
  .course-grid { grid-template-columns: 1fr; }
  .section-card.open { grid-column: auto; }
  .hero-symbol { display: none; }
  .search { width: min(44vw, 320px); }
}

@media (max-width: 620px) {
  .header-inner { padding: 10px 12px; }
  .brand-mark { display: none; }
  .brand .eyebrow { display: none; }
  .search { width: 42px; padding: 0 12px; transition: width .2s ease; }
  .search:focus-within { position: absolute; left: 58px; right: 10px; width: auto; background: var(--blue-dark); }
  .search input { min-width: 0; }
  .main-content { padding: 18px 12px 44px; }
  .hero { min-height: 180px; border-radius: 18px; }
  .section-cover { min-height: 175px; }
  .subsection-toggle { grid-template-columns: minmax(0,1fr) auto; }
  .subsection-meta { display: none; }
  .material { grid-template-columns: 36px minmax(0,1fr); }
  .material-action { display: none; }
  .modal-panel { inset: 0; border-radius: 0; }
  .modal-actions .button { display: none; }
  .modal-header { padding: 10px 12px; }
}
'''

APP_JS = r'''(async () => {
  "use strict";

  const data = window.COURSE_DATA;
  if (!data) {
    document.body.innerHTML = '<main style="padding:2rem;font-family:sans-serif"><h1>Kursdaten fehlen</h1><p>Die Datei data/course-data.js konnte nicht geladen werden.</p></main>';
    return;
  }

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const grid = $("#courseGrid");
  const nav = $("#sectionNav");
  const modal = $("#viewerModal");
  const viewerBody = $("#viewerBody");
  const viewerTitle = $("#viewerTitle");
  const viewerType = $("#viewerType");
  const openNewTab = $("#openNewTab");
  const downloadFile = $("#downloadFile");
  let lastFocused = null;

  document.title = `${data.course.title} · Kurs`;
  $("#courseTitle").textContent = data.course.title;
  $("#courseShortname").textContent = data.course.shortname || "Kurs";
  $("#courseCategory").textContent = data.course.category || "";

  const escapeText = value => String(value ?? "");
  const countMaterials = items => items.reduce((sum, item) => sum + (item.type === "subsection" ? countMaterials(item.items || []) : 1), 0);
  const formatBytes = bytes => {
    if (!bytes) return "";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
    return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
  };

  function iconFor(kind) {
    return ({ pdf: "PDF", html: "▶", image: "▧", file: "↓", page: "≡", folder: "▤", notice: "i" })[kind] || "•";
  }

  function createMaterial(item) {
    if (item.type === "subsection") return createSubsection(item);

    if (item.type === "notice") {
      const div = document.createElement("div");
      div.className = "material notice";
      div.dataset.search = `${item.title} ${item.message}`.toLowerCase();
      div.innerHTML = `<span class="item-icon" aria-hidden="true">${iconFor("notice")}</span><span class="material-copy"><span class="material-title"></span><span class="material-meta"></span></span>`;
      $(".material-title", div).textContent = item.title;
      $(".material-meta", div).textContent = item.message;
      return div;
    }

    if (item.type === "folder") {
      const section = document.createElement("section");
      section.className = "subsection";
      section.dataset.search = `${item.title} ${(item.files || []).map(f => f.filename).join(" ")}`.toLowerCase();
      const header = document.createElement("div");
      header.className = "material";
      header.innerHTML = `<span class="item-icon" aria-hidden="true">${iconFor("folder")}</span><span class="material-copy"><span class="material-title"></span><span class="material-meta"></span></span>`;
      $(".material-title", header).textContent = item.title;
      $(".material-meta", header).textContent = `${(item.files || []).length} Datei(en)`;
      section.append(header);
      const list = document.createElement("ul");
      list.className = "folder-list";
      (item.files || []).forEach(file => {
        const li = document.createElement("li");
        li.append(createFileButton(file.title || file.filename, file));
        list.append(li);
      });
      section.append(list);
      return section;
    }

    if (item.type === "page") {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "material";
      button.dataset.search = `${item.title} ${stripHtml(item.html)}`.toLowerCase();
      button.innerHTML = `<span class="item-icon" aria-hidden="true">${iconFor("page")}</span><span class="material-copy"><span class="material-title"></span><span class="material-meta">Kursseite</span></span><span class="material-action">Öffnen</span>`;
      $(".material-title", button).textContent = item.title;
      button.addEventListener("click", () => openPage(item));
      return button;
    }

    if (item.type === "resource") {
      const file = item.resource;
      return createFileButton(item.title, file, item.intro);
    }

    const unknown = document.createElement("div");
    unknown.className = "material empty-material";
    unknown.textContent = item.title || "Unbekanntes Material";
    return unknown;
  }

  function createFileButton(title, file, intro = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "material";
    button.dataset.search = `${title} ${file.filename || ""} ${stripHtml(intro)}`.toLowerCase();
    const meta = [file.kind === "html" ? "Interaktive Übung" : file.mime, formatBytes(file.size)].filter(Boolean).join(" · ");
    button.innerHTML = `<span class="item-icon" aria-hidden="true"></span><span class="material-copy"><span class="material-title"></span><span class="material-meta"></span></span><span class="material-action">Öffnen</span>`;
    $(".item-icon", button).textContent = iconFor(file.kind);
    $(".material-title", button).textContent = title;
    $(".material-meta", button).textContent = meta;
    button.addEventListener("click", () => openFile(title, file));
    return button;
  }

  function createSubsection(item) {
    const fragment = $("#subsectionTemplate").content.cloneNode(true);
    const section = $(".subsection", fragment);
    const toggle = $(".subsection-toggle", fragment);
    const body = $(".subsection-body", fragment);
    const title = $(".subsection-title", fragment);
    const meta = $(".subsection-meta", fragment);
    const summary = $(".subsection-summary", fragment);
    const items = $(".subsection-items", fragment);
    title.textContent = item.title;
    const count = countMaterials(item.items || []);
    meta.textContent = count ? `${count} Material${count === 1 ? "" : "ien"}` : "Noch ohne Inhalte";
    summary.innerHTML = item.summary || "";
    if (!item.summary) summary.remove();
    (item.items || []).forEach(child => items.append(createMaterial(child)));
    if (!(item.items || []).length) {
      const empty = document.createElement("div");
      empty.className = "material empty-material";
      empty.innerHTML = `<span class="item-icon" aria-hidden="true">·</span><span class="material-copy"><span class="material-title">Noch keine Materialien</span><span class="material-meta">Dieser Bereich ist im Moodle-Kurs leer.</span></span>`;
      items.append(empty);
    }
    section.dataset.search = `${item.title} ${stripHtml(item.summary)} ${collectSearch(item.items || [])}`.toLowerCase();
    toggle.addEventListener("click", () => setExpanded(toggle, body, toggle.getAttribute("aria-expanded") !== "true"));
    return fragment;
  }

  function renderSection(sectionData) {
    const fragment = $("#sectionTemplate").content.cloneNode(true);
    const article = $(".section-card", fragment);
    const cover = $(".section-cover", fragment);
    const content = $(".section-content", fragment);
    const image = $(".cover-image", fragment);
    const summary = $(".section-summary", fragment);
    const items = $(".section-items", fragment);
    const count = countMaterials(sectionData.items || []);

    article.id = `section-${sectionData.id}`;
    article.dataset.search = `${sectionData.title} ${stripHtml(sectionData.summary)} ${collectSearch(sectionData.items || [])}`.toLowerCase();
    $(".section-number", fragment).textContent = sectionData.number === 0 ? "Start" : `Thema ${sectionData.number}`;
    $(".section-title", fragment).textContent = sectionData.title;
    $(".section-count", fragment).textContent = count ? `${count} Material${count === 1 ? "" : "ien"}` : "Noch ohne Materialien";
    if (sectionData.image) image.style.backgroundImage = `url(${JSON.stringify(sectionData.image).slice(1, -1)})`;
    else image.style.backgroundImage = "radial-gradient(circle at 75% 20%, rgba(255,255,255,.2), transparent 28%), linear-gradient(130deg,#246aa6,#123e65)";
    summary.innerHTML = sectionData.summary || "";
    if (!sectionData.summary) summary.remove();
    (sectionData.items || []).forEach(item => items.append(createMaterial(item)));
    if (!(sectionData.items || []).length) {
      const empty = document.createElement("div");
      empty.className = "material empty-material";
      empty.innerHTML = `<span class="item-icon" aria-hidden="true">·</span><span class="material-copy"><span class="material-title">Noch keine Materialien</span><span class="material-meta">Dieser Themenbereich ist vorbereitet, aber noch leer.</span></span>`;
      items.append(empty);
    }
    cover.addEventListener("click", () => {
      const open = cover.getAttribute("aria-expanded") !== "true";
      setExpanded(cover, content, open);
      article.classList.toggle("open", open);
      if (open) history.replaceState(null, "", `#${article.id}`);
    });
    grid.append(fragment);

    const navButton = document.createElement("button");
    navButton.type = "button";
    navButton.className = "nav-link";
    navButton.innerHTML = `<span class="nav-number"></span><span class="nav-title"></span>`;
    $(".nav-number", navButton).textContent = sectionData.number === 0 ? "S" : sectionData.number;
    $(".nav-title", navButton).textContent = sectionData.title;
    navButton.addEventListener("click", () => {
      article.scrollIntoView({ behavior: "smooth", block: "start" });
      if (cover.getAttribute("aria-expanded") !== "true") cover.click();
      closeSidebar();
    });
    nav.append(navButton);
  }

  function setExpanded(button, panel, expanded) {
    button.setAttribute("aria-expanded", String(expanded));
    panel.hidden = !expanded;
  }

  function collectSearch(items) {
    return items.map(item => {
      if (item.type === "subsection") return `${item.title} ${collectSearch(item.items || [])}`;
      if (item.type === "resource") return `${item.title} ${item.resource?.filename || ""}`;
      if (item.type === "folder") return `${item.title} ${(item.files || []).map(f => f.filename).join(" ")}`;
      return `${item.title || ""} ${item.message || ""} ${stripHtml(item.html || "")}`;
    }).join(" ");
  }

  function stripHtml(value) {
    const temp = document.createElement("div");
    temp.innerHTML = value || "";
    return temp.textContent || "";
  }

  function openFile(title, file) {
    lastFocused = document.activeElement;
    viewerTitle.textContent = title;
    viewerType.textContent = file.kind === "pdf" ? "PDF-Dokument" : file.kind === "html" ? "Interaktive Übung" : "Kursdatei";
    const url = browserPath(file.path);
    openNewTab.href = url;
    openNewTab.hidden = false;
    downloadFile.href = url;
    downloadFile.download = file.filename || "";
    downloadFile.hidden = false;
    viewerBody.replaceChildren();

    if (["pdf", "html", "image"].includes(file.kind)) {
      const frame = document.createElement("iframe");
      frame.className = "viewer-frame";
      frame.title = title;
      frame.src = url;
      if (file.kind === "html") frame.setAttribute("sandbox", "allow-scripts allow-forms allow-pointer-lock allow-popups allow-downloads");
      viewerBody.append(frame);
    } else {
      const page = document.createElement("div");
      page.className = "page-view prose";
      page.innerHTML = `<h2>Datei herunterladen</h2><p>Dieser Dateityp wird nicht direkt im Browser angezeigt.</p><p><a class="button secondary" href="${url}" download>Download starten</a></p>`;
      viewerBody.append(page);
    }
    showModal();
    history.replaceState(null, "", `#material=${encodeURIComponent(file.path)}`);
  }

  function openPage(item) {
    lastFocused = document.activeElement;
    viewerTitle.textContent = item.title;
    viewerType.textContent = "Kursseite";
    openNewTab.hidden = true;
    downloadFile.hidden = true;
    viewerBody.replaceChildren();
    const page = document.createElement("article");
    page.className = "page-view prose";
    page.innerHTML = item.html || "<p>Diese Seite enthält keinen Inhalt.</p>";
    viewerBody.append(page);
    showModal();
  }

  function showModal() {
    modal.hidden = false;
    document.body.classList.add("modal-open");
    $("[data-close-modal]", modal).focus();
  }

  function closeModal() {
    if (modal.hidden) return;
    modal.hidden = true;
    viewerBody.replaceChildren();
    document.body.classList.remove("modal-open");
    if (location.hash.startsWith("#material=")) history.replaceState(null, "", location.pathname + location.search);
    lastFocused?.focus();
  }

  $$('[data-close-modal]').forEach(el => el.addEventListener("click", closeModal));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      closeModal();
      closeSidebar();
    }
  });


  function normalizeCoursePath(value) {
    return String(value || "")
      .replace(/\\/g, "/")
      .replace(/^\.\//, "")
      .replace(/^\//, "")
      .replace(/\/{2,}/g, "/");
  }

  function directoryName(path) {
    const normalized = normalizeCoursePath(path);
    const slash = normalized.lastIndexOf("/");
    return slash === -1 ? "" : normalized.slice(0, slash);
  }

  function browserPath(path) {
    return normalizeCoursePath(path)
      .split("/")
      .map(segment => encodeURIComponent(segment))
      .join("/");
  }

  function fileKind(filename, mime = "") {
    const extension = filename.toLowerCase().split(".").pop();
    if (mime === "application/pdf" || extension === "pdf") return "pdf";
    if (mime === "text/html" || ["html", "htm"].includes(extension)) return "html";
    if (mime.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "svg", "avif"].includes(extension)) return "image";
    return "file";
  }

  function inferredMime(filename) {
    const extension = filename.toLowerCase().split(".").pop();
    return ({
      pdf: "application/pdf",
      html: "text/html",
      htm: "text/html",
      png: "image/png",
      jpg: "image/jpeg",
      jpeg: "image/jpeg",
      gif: "image/gif",
      webp: "image/webp",
      svg: "image/svg+xml",
      avif: "image/avif",
      txt: "text/plain",
      csv: "text/csv",
      zip: "application/zip"
    })[extension] || "application/octet-stream";
  }

  function manifestFile(entry) {
    const path = normalizeCoursePath(entry.path);
    const filename = String(entry.filename || path.split("/").pop() || "Datei");
    const mime = String(entry.mime || inferredMime(filename));
    return {
      title: filename,
      filename,
      path,
      mime,
      size: Number(entry.size) || 0,
      kind: fileKind(filename, mime)
    };
  }

  async function fetchFileManifest(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    const manifest = await response.json();
    if (!Array.isArray(manifest)) throw new Error(`${url}: ungültiges Format`);
    return manifest;
  }

  async function loadCourseFileManifest() {
    // GitHub Pages verarbeitet die erste Datei bei jedem Commit mit Jekyll neu.
    // Die zweite Datei ist eine lokale Momentaufnahme für python -m http.server.
    for (const url of ["data/course-files-auto.json", "data/course-files.json"]) {
      try {
        const manifest = await fetchFileManifest(url);
        if (manifest.length) return manifest;
      } catch (error) {
        console.info(`Dateiliste ${url} konnte nicht verwendet werden.`, error);
      }
    }
    return [];
  }

  function indexMaterialFolders(items, directoryTargets, knownPaths) {
    (items || []).forEach(item => {
      if (item.type === "subsection") {
        indexMaterialFolders(item.items || [], directoryTargets, knownPaths);
        return;
      }

      if (item.type === "page") {
        const attributePattern = /(?:src|href)\s*=\s*["']([^"']+)["']/gi;
        let match;
        while ((match = attributePattern.exec(item.html || "")) !== null) {
          const rawPath = match[1].split(/[?#]/, 1)[0];
          if (!rawPath.includes("course-files/")) continue;
          let decodedPath = rawPath;
          try { decodedPath = decodeURI(rawPath); } catch (_) { /* Pfad unverändert verwenden. */ }
          const coursePath = decodedPath.slice(decodedPath.indexOf("course-files/"));
          knownPaths.add(normalizeCoursePath(coursePath).toLowerCase());
        }
        return;
      }

      if (item.type === "folder") {
        (item.files || []).forEach(file => {
          const path = normalizeCoursePath(file.path);
          if (!path) return;
          knownPaths.add(path.toLowerCase());
          const directory = directoryName(path);
          if (directory && !directoryTargets.has(directory)) {
            directoryTargets.set(directory, { type: "folder", item, directory });
          }
        });
        return;
      }

      if (item.type === "resource") {
        const files = item.files?.length ? item.files : [item.resource].filter(Boolean);
        files.forEach(file => {
          const path = normalizeCoursePath(file.path);
          if (!path) return;
          knownPaths.add(path.toLowerCase());
          const directory = directoryName(path);
          if (directory && !directoryTargets.has(directory)) {
            directoryTargets.set(directory, { type: "resources", items, anchor: item, directory });
          }
        });
      }
    });
  }

  function nearestDirectoryTarget(directory, directoryTargets) {
    let current = directory;
    while (current.startsWith("course-files/")) {
      if (directoryTargets.has(current)) return directoryTargets.get(current);
      const parent = directoryName(current);
      if (!parent || parent === current) break;
      current = parent;
    }
    return null;
  }

  function appendDiscoveredResource(target, file) {
    if (target.type === "folder") {
      target.item.files ||= [];
      target.item.files.push(file);
      return;
    }

    const resource = {
      type: "resource",
      title: file.filename,
      intro: "",
      resource: file,
      files: [file],
      autoDiscovered: true,
      autoDirectory: target.directory
    };
    let insertionIndex = target.items.indexOf(target.anchor) + 1;
    while (
      insertionIndex < target.items.length &&
      target.items[insertionIndex].autoDiscovered &&
      target.items[insertionIndex].autoDirectory === target.directory
    ) {
      insertionIndex += 1;
    }
    target.items.splice(insertionIndex, 0, resource);
  }

  function addUnassignedFiles(groups) {
    if (!groups.size) return;
    const nextNumber = Math.max(0, ...data.sections.map(section => Number(section.number) || 0)) + 1;
    const folders = [...groups.entries()].map(([directory, files]) => ({
      type: "folder",
      title: directory.replace(/^course-files\//, ""),
      files
    }));
    data.sections.push({
      id: "auto-uploaded-files",
      number: nextNumber,
      title: "Weitere hochgeladene Dateien",
      summary: "Dateien aus neuen course-files-Ordnern, die noch keinem vorhandenen Kursbereich zugeordnet sind.",
      image: "",
      items: folders,
      empty: false
    });
  }

  function mergeDiscoveredCourseFiles(manifest) {
    const directoryTargets = new Map();
    const knownPaths = new Set();
    data.sections.forEach(section => indexMaterialFolders(section.items || [], directoryTargets, knownPaths));

    const unassigned = new Map();
    const entries = manifest
      .map(manifestFile)
      .filter(file => file.path.startsWith("course-files/"))
      .sort((a, b) => a.path.localeCompare(b.path, "de"));

    entries.forEach(file => {
      const pathKey = file.path.toLowerCase();
      if (knownPaths.has(pathKey)) return;
      if (file.filename.startsWith(".") || ["thumbs.db", "desktop.ini"].includes(file.filename.toLowerCase())) return;

      const directory = directoryName(file.path);
      // section-* enthält die Kachelbilder der Themen und keine Unterrichtsdateien.
      if (/^course-files\/section-/i.test(directory)) return;

      const target = nearestDirectoryTarget(directory, directoryTargets);
      if (target) {
        appendDiscoveredResource(target, file);
      } else {
        if (!unassigned.has(directory)) unassigned.set(directory, []);
        unassigned.get(directory).push(file);
      }
      knownPaths.add(pathKey);
    });

    addUnassignedFiles(unassigned);
  }

  try {
    mergeDiscoveredCourseFiles(await loadCourseFileManifest());
  } catch (error) {
    console.warn("Nachträglich hochgeladene Dateien konnten nicht ergänzt werden.", error);
  }

  data.sections.forEach(renderSection);

  const searchInput = $("#searchInput");
  searchInput.addEventListener("input", () => {
    const term = searchInput.value.trim().toLowerCase();
    let visible = 0;
    $$(".section-card", grid).forEach(card => {
      const match = !term || card.dataset.search.includes(term);
      card.hidden = !match;
      if (match) visible += 1;
    });
    $("#searchStatus").textContent = term ? `${visible} von ${data.sections.length} Bereichen gefunden` : "";
    $("#emptySearch").hidden = visible !== 0;
  });

  const menuButton = $("#menuButton");
  const sidebar = $("#sidebar");
  menuButton.addEventListener("click", () => {
    const open = !sidebar.classList.contains("open");
    sidebar.classList.toggle("open", open);
    menuButton.setAttribute("aria-expanded", String(open));
  });
  function closeSidebar() {
    sidebar.classList.remove("open");
    menuButton.setAttribute("aria-expanded", "false");
  }

  const initialHash = location.hash;
  if (initialHash.startsWith("#section-")) {
    const target = document.querySelector(initialHash);
    if (target) {
      const cover = $(".section-cover", target);
      const content = $(".section-content", target);
      setExpanded(cover, content, true);
      target.classList.add("open");
      setTimeout(() => target.scrollIntoView({ block: "start" }), 0);
    }
  }
})().catch(error => {
  console.error("Die Kursseite konnte nicht initialisiert werden.", error);
});
'''

README_MD = r'''# Moodle-Kurs als öffentliche GitHub-Pages-Website

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
'''

PUBLISH_SH = r'''#!/usr/bin/env bash
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
'''

COURSE_FILES_AUTO_JSON = r'''---
layout: null
---
[
{% assign course_files = site.static_files | where_exp: "file", "file.path contains '/course-files/'" | sort: "path" %}
{% for file in course_files %}
  {"path": {{ file.path | remove_first: "/" | jsonify }}, "filename": {{ file.name | jsonify }}}{% unless forloop.last %},{% endunless %}
{% endfor %}
]
'''

UPDATE_MANIFEST_PY = r'''#!/usr/bin/env python3
"""Create a local fallback manifest for files below course-files/.

GitHub Pages creates the live manifest from data/course-files-auto.json via
Jekyll. This script is only needed for local testing with a plain HTTP server
or before publishing with publish.sh.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from pathlib import Path


def build_manifest(root: Path) -> list[dict[str, object]]:
    course_files = root / "course-files"
    if not course_files.is_dir():
        raise SystemExit(f"Ordner nicht gefunden: {course_files}")

    manifest: list[dict[str, object]] = []
    for path in sorted(p for p in course_files.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        mime, _ = mimetypes.guess_type(path.name)
        manifest.append(
            {
                "path": relative,
                "filename": path.name,
                "size": path.stat().st_size,
                "mime": mime or "application/octet-stream",
            }
        )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Erzeugt data/course-files.json für die lokale Vorschau."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=Path(__file__).resolve().parents[1],
        type=Path,
        help="Wurzelordner der Website (Standard: Repository-Wurzel)",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    target = root / "data" / "course-files.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(root)
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "
",
        encoding="utf-8",
    )
    print(f"{len(manifest)} Dateien erfasst: {target}")


if __name__ == "__main__":
    main()
'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Moodle-MBZ in eine statische GitHub-Pages-Website umwandeln")
    parser.add_argument("mbz", type=Path, help="Pfad zur Moodle-.mbz-Datei")
    parser.add_argument("output", type=Path, help="Zielordner für die Website")
    args = parser.parse_args()

    mbz = args.mbz.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not mbz.exists():
        parser.error(f"Datei nicht gefunden: {mbz}")
    if output.exists():
        shutil.rmtree(output)

    with tempfile.TemporaryDirectory(prefix="moodle-mbz-") as temp:
        extracted = Path(temp)
        safe_extract(mbz, extracted)
        converter = MoodleConverter(extracted, output)
        data = converter.run()

    tools = output / "tools"
    tools.mkdir(exist_ok=True)
    shutil.copy2(Path(__file__).resolve(), tools / "mbz_to_github_pages.py")
    print(f"Fertig: {output}")
    print(f"Kurs: {data['course']['title']}")
    print(f"Bereiche: {len(data['sections'])}")


if __name__ == "__main__":
    main()
