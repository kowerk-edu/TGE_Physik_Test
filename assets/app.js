(async () => {
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
