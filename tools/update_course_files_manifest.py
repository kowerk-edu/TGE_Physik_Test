#!/usr/bin/env python3
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
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"{len(manifest)} Dateien erfasst: {target}")


if __name__ == "__main__":
    main()
