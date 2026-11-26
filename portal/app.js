"use strict";
// make3d portal front end. All URLs are relative so it works behind a path prefix (e.g. /make3d/).

const $ = (s, el = document) => el.querySelector(s);
const main = $("#main");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const outUrl = p => "out/" + p.split("/").map(encodeURIComponent).join("/");
let timers = [];
const clearTimers = () => { timers.forEach(clearInterval); timers = []; };

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  if (r.status === 401) { showLogin(); throw new Error("log in"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

const fmtTime = t => t ? new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
function fmtDur(s) {
  if (s == null) return "";
  s = Math.round(s);
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m`;
}
const fmtSize = n => n < 1024 ? `${n} B` : n < 1 << 20 ? `${(n / 1024).toFixed(0)} KB` : n < 1 << 30 ? `${(n / 1048576).toFixed(1)} MB` : `${(n / 1073741824).toFixed(1)} GB`;

// ------------------------------------------------------------------ forms

const F = {
  text: (k, label, o = {}) => ({ k, label, type: "text", ...o }),
  area: (k, label, o = {}) => ({ k, label, type: "area", ...o }),
  num: (k, label, o = {}) => ({ k, label, type: "num", ...o }),
  check: (k, label, o = {}) => ({ k, label, type: "check", ...o }),
  sel: (k, label, options, o = {}) => ({ k, label, type: "sel", options, ...o }),
  file: (k, label, o = {}) => ({ k, label, type: "file", ...o }),
  files: (k, label, o = {}) => ({ k, label, type: "files", ...o }),
};

function fieldHtml(f) {
  const help = f.help ? `<span class="help">${esc(f.help)}</span>` : "";
  const cls = f.full ? "full" : "";
  const ph = f.ph ? ` placeholder="${esc(f.ph)}"` : "";
  const val = f.value != null ? ` value="${esc(f.value)}"` : "";
  switch (f.type) {
    case "text": return `<label class="${cls}">${esc(f.label)}${help}<input type="text" name="${f.k}"${ph}${val}${f.req ? " required" : ""}></label>`;
    case "area": return `<label class="full">${esc(f.label)}${help}<textarea name="${f.k}"${ph}${f.req ? " required" : ""}>${esc(f.value ?? "")}</textarea></label>`;
    case "num": return `<label class="${cls}">${esc(f.label)}${help}<input type="number" name="${f.k}"${ph}${val} step="${f.step ?? "any"}"${f.min != null ? ` min="${f.min}"` : ""}${f.max != null ? ` max="${f.max}"` : ""}></label>`;
    case "check": return `<label class="check ${cls}"><input type="checkbox" name="${f.k}"${f.value ? " checked" : ""}><span>${esc(f.label)}${help}</span></label>`;
    case "sel": return `<label class="${cls}">${esc(f.label)}${help}<select name="${f.k}">${f.options.map(o => {
      const [v, t] = Array.isArray(o) ? o : [o, o];
      return `<option value="${esc(v)}"${v === f.value ? " selected" : ""}>${esc(t)}</option>`;
    }).join("")}</select></label>${f.note ? `<p class="notice small full" id="preset-note"></p>` : ""}`;
    case "file": return `<label class="${cls}"><span data-lbl>${esc(f.label)}</span>${help}<input type="file" name="${f.k}" accept="image/png,image/jpeg,image/webp"></label>`;
    case "files": return `<label class="full">${esc(f.label)}${help}<input type="file" name="${f.k}" multiple accept="image/png,image/jpeg,image/webp"></label>`;
  }
}

const readFile = file => new Promise((res, rej) => {
  const r = new FileReader();
  r.onload = () => res({ filename: file.name, data: r.result });
  r.onerror = () => rej(r.error);
  r.readAsDataURL(file);
});

async function formValues(form, fields) {
  const v = {};
  for (const f of fields) {
    const el = form.elements[f.k];
    if (!el) continue;
    if (f.type === "check") v[f.k] = el.checked;
    else if (f.type === "file") { if (el.files[0]) v[f.k] = await readFile(el.files[0]); }
    else if (f.type === "files") { v[f.k] = await Promise.all([...el.files].map(readFile)); }
    else if (el.value.trim() !== "") v[f.k] = el.value.trim();
  }
  return v;
}

function toolPage({ title, intro, tool, fields, extra = {}, note }) {
  main.innerHTML = `<div class="stack">
    <div><h2>${esc(title)}</h2><p class="muted">${intro}</p></div>
    ${note ? `<div class="notice small">${note}</div>` : ""}
    <form class="card stack" id="tool-form"><div class="grid">${fields.map(fieldHtml).join("")}</div>
      <div class="row"><button class="primary" type="submit">Start</button><span class="error" id="form-err"></span></div>
    </form></div>`;
  const form = $("#tool-form");
  form.onsubmit = async e => {
    e.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true; $("#form-err").textContent = "";
    try {
      const job = await api(`api/run/${tool}`, { ...extra, ...await formValues(form, fields) });
      location.hash = `#jobs/${job.id}`;
    } catch (err) { $("#form-err").textContent = err.message; btn.disabled = false; }
  };
}

let presetData = null;
async function presetOptions(characterOnly) {
  presetData = presetData || await api("api/presets");
  return [["", "custom / default"], ...Object.entries(presetData.presets)
    .filter(([, p]) => !characterOnly || p.kind === "character").map(([k, p]) => [k, p.label])];
}

// Show the chosen preset's numbers as placeholders: empty fields use them, typed values override.
function presetHints(form) {
  const sel = form.elements.preset, note = $("#preset-note");
  const update = () => {
    const p = presetData.presets[sel.value], d = presetData.defaults;
    const set = (k, v) => { const el = form.elements[k]; if (!el) return;
      if (el.tagName === "SELECT") el.options[0].textContent = v ? `preset (${v})` : el.dataset.first;
      else el.placeholder = v ?? el.dataset.ph; };
    for (const k of ["tris", "tex", "resolution", "height"]) {
      const el = form.elements[k]; if (!el) continue;
      if (el.dataset.ph === undefined) el.dataset.ph = el.placeholder ?? "";
      if (el.tagName === "SELECT" && el.dataset.first === undefined) el.dataset.first = el.options[0].textContent;
      set(k, p ? p[k] : undefined);
    }
    if (form.elements.prop) { form.elements.prop.checked = p ? p.kind === "prop" : false; }
    const tpl = form.elements.template;
    if (tpl && form.elements.prop) {   // 3D form only; art3d picks its template from the art
      if (p?.template && !tpl.value) { tpl.value = p.template; tpl.dataset.byPreset = "1"; }
      else if (!p?.template && tpl.dataset.byPreset) { tpl.value = ""; delete tpl.dataset.byPreset; }
    }
    if (note) note.textContent = p ? `${p.note}. ${p.tris.toLocaleString()} triangles, ${p.tex}px textures` +
      (p.kind === "prop" ? ", no rig." : ", rigged.") + " Fill in a field to override it." : `Defaults: ${d.tris.toLocaleString()} triangles, ${d.tex}px textures.`;
  };
  sel.onchange = update; update();
}

let templateList = null;
async function templateOptions() {
  templateList = templateList || await api("api/templates");
  return [["", "automatic"], ...templateList];
}

// ------------------------------------------------------------------ pages

const pages = {
  async "3d"() {
    toolPage({
      title: "Text to 3D", tool: "make3d",
      intro: "Describe a character or object and pick what kind of asset it is. Flux draws a concept in an A-pose, TRELLIS builds the mesh, Blender cleans, textures and rigs it. Expect 10–20 minutes for a character.",
      fields: [
        F.area("description", "Description", { ph: "a dwarf blacksmith with a leather apron and a braided red beard", help: "Leave empty if you upload an image instead." }),
        F.sel("preset", "Asset type", await presetOptions(), { help: "Sets triangles, textures, detail and size", note: true }),
        F.text("name", "Name", { help: "Output folder (default: from the description)" }),
        F.check("prop", "Prop, not a character", { help: "No pose template, no rig" }),
        F.sel("template", "Pose template", await templateOptions(), { help: "Body shape and pose for characters" }),
        F.file("image", "Use this A-pose image", { help: "Skips the drawing step" }),
        F.num("options", "Concept options", { min: 1, max: 6, step: 1, ph: "1", help: "Draw several, pick the best automatically" }),
        F.sel("resolution", "TRELLIS resolution", [["", "default"], "512", "1024", "1536"], { help: "Default: 1024 characters, 512 props" }),
        F.num("height", "Height (m)", { min: 0.05, max: 50, ph: "1.8 / 1.0" }),
        F.num("tris", "Triangle budget", { min: 500, max: 500000, step: 100, ph: "30000" }),
        F.sel("tex", "Texture size", [["", "default (2048)"], "512", "1024", "2048", "4096"]),
        F.num("seed", "Seed", { min: 0, step: 1, ph: "42" }),
        F.check("no_rig", "Skip rigging"),
      ],
    });
    const form = $("#tool-form");
    presetHints(form);
    // The image field means "a ready-posed character" or "an object", depending on Prop
    const img = form.elements.image.closest("label");
    const syncImage = () => {
      const prop = form.elements.prop.checked;
      img.querySelector("[data-lbl]").textContent = prop ? "Use this object image" : "Use this A-pose image";
      img.querySelector(".help").innerHTML = prop
        ? "Skips the drawing step. One object, plain background."
        : `Skips the drawing step. Front view, A- or T-pose, plain background, empty hands with fingers spread. Other character art: use <a href="#art">Art to 3D</a>.`;
    };
    form.elements.prop.addEventListener("change", syncImage);
    form.elements.preset.addEventListener("change", syncImage);
    syncImage();
  },

  async art() {
    toolPage({
      title: "Your art to 3D", tool: "art3d",
      intro: "Upload your own character art. The vision model describes it, Flux redraws it in an A- or T-pose in your style, the best candidate goes on to 3D.",
      fields: [
        F.file("art", "Character art"),
        F.text("name", "Name", { help: "Default: from the file name" }),
        F.sel("pose", "Pose", [["a", "A-pose (recommended)"], ["t", "T-pose"]]),
        F.sel("preset", "Character type", await presetOptions(true), { help: "Sets triangles, textures and detail", note: true }),
        F.sel("template", "Pose template", await templateOptions(), { help: "Default: picked from the art (build)" }),
        F.text("hint", "Hint", { full: true, help: "Correct anything the vision model gets wrong, e.g. 'she carries a bow on her back'" }),
        F.num("tris", "Triangle budget", { min: 500, max: 500000, step: 100, ph: "30000" }),
        F.sel("tex", "Texture size", [["", "default (2048)"], "512", "1024", "2048", "4096"]),
        F.num("options", "Candidates", { min: 1, max: 6, step: 1, ph: "3" }),
        F.num("seed", "Seed", { min: 0, step: 1, ph: "42" }),
        F.check("stop", "Stop after the pose candidates", { help: "Check them before spending time on 3D" }),
      ],
    });
    presetHints($("#tool-form"));
  },

  async video() {
    toolPage({
      title: "Video with sound", tool: "makevideo",
      intro: "LTX-2.3 makes video and matching audio from a prompt, optionally starting from an image (and ending on another).",
      note: "Video always renders on this machine's GPU, even when Lemonade is elsewhere. About 6 minutes per 5 seconds at full size; drafts take 1–2 minutes.",
      fields: [
        F.area("prompt", "Prompt", { req: true, ph: "A knight warms his hands at a campfire at night, sparks rising. Sound: crackling fire, wind in the trees." , help: "Describe the motion and the sounds you want." }),
        F.text("name", "Name"),
        F.num("seconds", "Seconds", { min: 1, max: 20, ph: "5" }),
        F.text("size", "Size", { ph: "768x512", help: "WxH, multiples of 32" }),
        F.file("image", "Start image"),
        F.file("end_image", "End image", { help: "Needs a start image" }),
        F.num("seed", "Seed", { min: 0, step: 1, ph: "42" }),
        F.check("draft", "Draft", { help: "512x320, 2 seconds" }),
        F.check("no_audio", "No audio"),
      ],
    });
  },

  async sound() {
    toolPage({
      title: "Sound effects and music", tool: "makesound",
      intro: "Sound effects with MOSS-SoundEffect (about 3 minutes each), music and songs with ACE-Step (seconds).",
      fields: [
        F.area("prompt", "Sound, or music style", { req: true, ph: "a heavy wooden door creaking open in a stone hall", help: "For music: genre, mood, tempo, instruments, voice." }),
        F.text("name", "Name"),
        F.check("music", "Music"),
        F.num("seconds", "Seconds", { min: 1, max: 240, ph: "5 / 30" }),
        F.num("takes", "Takes", { min: 1, max: 5, step: 1, ph: "1" }),
        F.text("language", "Lyrics language", { ph: "en" }),
        F.num("seed", "Seed", { min: 0, step: 1, ph: "42" }),
        F.area("lyrics", "Lyrics", { help: "Music only. Use [verse], [chorus] lines to structure it." }),
      ],
    });
  },

  async story(name) {
    if (name) return storyPage(name);
    const lib = await api("api/library");
    const stories = lib.filter(x => x.kind === "story");
    const fields = [
      F.text("name", "Name", { req: true, ph: "knight-vs-dragon" }),
      F.area("idea", "What happens", { req: true, ph: "a <knight> fights a <dragon> in a ruined castle courtyard at dusk", help: "Write <name> for a character that has a reference image below." }),
      F.files("refs", "Character reference images", { help: "Optional. Name each file after its placeholder: knight.png, dragon.png" }),
      F.num("seconds", "Length (s)", { min: 5, max: 120, ph: "30" }),
      F.num("every", "Keyframe every (s)", { min: 2, max: 10, ph: "5" }),
      F.text("style", "Style", { ph: "hand-painted fantasy art, soft light" }),
      F.num("tries", "Redraws per keyframe", { min: 1, max: 5, step: 1, ph: "3" }),
      F.num("seed", "Seed", { min: 0, step: 1, ph: "42" }),
      F.check("force", "Replace if it exists"),
    ];
    main.innerHTML = `<div class="stack">
      <div><h2>Storyboard</h2><p class="muted">Plan a long video as keyframes every few seconds, check and edit them, then render the whole thing with matching sound.</p></div>
      ${stories.length ? `<div class="tiles">${stories.map(tile).join("")}</div>` : ""}
      <form class="card stack" id="tool-form"><h3>New storyboard</h3><div class="grid">${fields.map(fieldHtml).join("")}</div>
        <div class="row"><button class="primary" type="submit">Plan and draw keyframes</button><span class="error" id="form-err"></span></div>
      </form></div>`;
    stories.forEach(s => { const a = main.querySelector(`[data-path="${CSS.escape(s.path)}"]`); if (a) a.href = `#story/${s.name}`; });
    const form = $("#tool-form");
    form.onsubmit = async e => {
      e.preventDefault();
      $("#form-err").textContent = "";
      try {
        const job = await api("api/run/storyboard", { step: "new", ...await formValues(form, fields) });
        location.hash = `#jobs/${job.id}`;
      } catch (err) { $("#form-err").textContent = err.message; }
    };
  },

  async jobs(id) {
    if (id) return jobPage(id);
    const draw = async () => {
      const jobs = await api("api/jobs");
      const body = jobs.length ? jobs.map(j => `<tr class="link" data-id="${esc(j.id)}">
          <td><span class="status ${j.status}">${j.status}</span></td>
          <td class="title">${esc(j.title)}</td>
          <td class="muted small">${fmtTime(j.created)}</td>
          <td class="muted small">${fmtDur((j.ended || (j.started && Date.now() / 1000)) - j.started)}</td></tr>`).join("")
        : `<tr><td colspan="4" class="muted">Nothing yet.</td></tr>`;
      main.innerHTML = `<h2>Jobs</h2><p class="muted">One job runs at a time, the rest wait their turn. Jobs carry on if you close the page.</p>
        <div class="card"><table><thead><tr><th>Status</th><th>Job</th><th>Started</th><th>Took</th></tr></thead><tbody>${body}</tbody></table></div>`;
      main.querySelectorAll("tr.link").forEach(tr => tr.onclick = () => location.hash = `#jobs/${tr.dataset.id}`);
      return jobs.some(j => j.status === "running" || j.status === "queued");
    };
    if (await draw()) timers.push(setInterval(draw, 4000));
  },

  async library(path) {
    if (path) return folderPage(path);
    const lib = await api("api/library");
    const kinds = [["", "All"], ["model", "3D"], ["video", "Video"], ["sound", "Sound"], ["story", "Storyboards"]];
    main.innerHTML = `<div class="row spread"><h2>Library</h2><select id="kind" style="width:auto">${kinds.map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select></div>
      <div class="tiles" id="tiles"></div>`;
    const draw = () => {
      const k = $("#kind").value;
      const items = lib.filter(x => !k || x.kind === k);
      $("#tiles").innerHTML = items.length ? items.map(tile).join("") : `<p class="muted">Nothing here yet.</p>`;
    };
    $("#kind").onchange = draw; draw();
    bindItemButtons($("#tiles"), () => pages.library());
  },

  async settings() { settingsPage(); },
};

const ICON = { model: "◆", video: "▶", sound: "♪", story: "▦" };
const KIND = { model: "3D", video: "Video", sound: "Sound", story: "Storyboard" };
function tile(x) {
  const href = x.kind === "story" ? `#story/${encodeURIComponent(x.name)}` : `#library/${x.path}`;
  const bg = x.thumb ? ` style="background-image:url('${outUrl(x.thumb)}')"` : "";
  return `<div class="tile"><a href="${href}"><div class="thumb"${bg}>${x.thumb ? "" : ICON[x.kind]}</div></a>
    <div class="cap"><a href="${href}"><b>${esc(x.name)}</b><span class="kind">${KIND[x.kind]} · ${fmtTime(x.mtime)}</span></a>
    ${itemButtons(x.path, x.name)}</div></div>`;
}

// Rename / delete for a library item (a top-level output folder)
function itemButtons(path, name) {
  return `<span class="item-actions"><button class="ghost small" data-act="rename" data-path="${esc(path)}" data-name="${esc(name)}" title="Rename">Rename</button>` +
    `<button class="ghost small danger" data-act="delete" data-path="${esc(path)}" data-name="${esc(name)}" title="Move to the trash">Delete</button></span>`;
}

// One handler for every rename/delete button on the page. onDone(newPath|null) after a change.
function bindItemButtons(root, onDone) {
  root.addEventListener("click", async e => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    e.preventDefault();
    const { act, path, name } = b.dataset;
    try {
      if (act === "rename") {
        const n = prompt(`New name for "${name}" (letters, numbers, dashes):`, name);
        if (!n || n.trim() === name) return;
        const r = await api("api/library/rename", { path, name: n });
        onDone(r.path, r.name);
      } else {
        if (!confirm(`Move "${name}" and all its files to the trash?\nYou can restore it from your file manager's Trash on the portal machine.`)) return;
        await api("api/library/delete", { path });
        onDone(null);
      }
    } catch (err) { alert(err.message); }
  });
}

// ------------------------------------------------------------------ outputs

let mvLoaded = false;
function loadModelViewer() {
  if (mvLoaded) return; mvLoaded = true;
  const s = document.createElement("script");
  s.type = "module";
  s.src = "https://cdn.jsdelivr.net/npm/@google/model-viewer@3.5.0/dist/model-viewer.min.js";
  document.head.appendChild(s);
}

function mediaHtml(rel, name, size) {
  const url = outUrl(rel), dl = `<a href="${url}?dl=1" download>download</a>`;
  const ext = name.toLowerCase().split(".").pop();
  const cap = `<div class="cap"><span>${esc(name)}</span><span class="muted">${size != null ? fmtSize(size) + " · " : ""}${dl}</span></div>`;
  if (["png", "jpg", "jpeg", "webp"].includes(ext)) return `<div class="media"><a href="${url}" target="_blank"><img loading="lazy" src="${url}" alt="${esc(name)}"></a>${cap}</div>`;
  if (["webm", "mp4", "ogv"].includes(ext)) return `<div class="media wide"><video controls preload="metadata" src="${url}"></video>${cap}</div>`;
  if (["wav", "ogg", "mp3"].includes(ext)) return `<div class="media"><audio controls preload="none" src="${url}"></audio>${cap}</div>`;
  if (ext === "glb") { loadModelViewer(); return `<div class="media wide"><model-viewer src="${url}" camera-controls auto-rotate shadow-intensity="1" alt="${esc(name)}"></model-viewer>${cap}</div>`; }
  return null;
}

function rankFiles(files) {
  // Final results first: rigged GLB, finished videos/sounds, then images
  const score = f => {
    const n = f.name.toLowerCase();
    if (n.endsWith(".glb") && n !== "raw.glb") return 0;
    if (n.endsWith(".webm") || n.endsWith(".mp4")) return 1;
    if (n.endsWith(".ogg") || n.endsWith(".wav")) return 2;
    if (n === "preview.png" || n === "storyboard.png" || n === "pose-options.png") return 3;
    return 5;
  };
  return [...files].sort((a, b) => score(a) - score(b) || a.name.localeCompare(b.name));
}

async function outputsHtml(rel) {
  let files;
  try { files = await api(outUrl(rel) + "/"); } catch { return `<p class="muted">No output yet.</p>`; }
  files = rankFiles(files);
  // .ogv duplicates the .webm in browsers; .mkv/.avi are intermediates
  const shown = files.filter(f => !f.dir && !/\.(ogv|mkv|avi|key)$/i.test(f.name));
  const previews = [], others = [];
  let glbShown = false;
  for (const f of shown) {
    const isRawGlb = f.name.toLowerCase() === "raw.glb";
    const h = (isRawGlb && glbShown) ? null : mediaHtml(`${rel}/${f.name}`, f.name, f.size);
    if (h) { previews.push(h); if (f.name.endsWith(".glb")) glbShown = true; } else others.push(f);
  }
  const rest = [...others, ...files.filter(f => f.dir || /\.(ogv|mkv|avi)$/i.test(f.name))];
  return `${previews.length ? `<div class="media-grid">${previews.join("")}</div>` : ""}
    ${rest.length ? `<h3 style="margin-top:18px">Other files</h3><div class="files">${rest.map(f => f.dir
      ? `<a href="#library/${rel}/${encodeURIComponent(f.name)}">${esc(f.name)}/</a>`
      : `<a href="${outUrl(rel + "/" + f.name)}?dl=1" download>${esc(f.name)} <span class="muted small">${fmtSize(f.size)}</span></a>`).join("")}</div>` : ""}`;
}

async function folderPage(path) {
  const parts = path.split("/");
  const crumbs = parts.map((p, i) => i < parts.length - 1 ? `<a href="#library/${parts.slice(0, i + 1).join("/")}">${esc(p)}</a>` : `<b>${esc(p)}</b>`).join(" / ");
  const lib = parts.length <= 2 ? await api("api/library") : [];
  const item = lib.find(x => x.path === path);
  main.innerHTML = `<div class="row spread"><p class="small" style="margin:0"><a href="#library">Library</a> / ${crumbs}</p>
    ${item ? itemButtons(item.path, item.name) : ""}</div><div id="out" style="margin-top:12px">Loading…</div>`;
  if (item) bindItemButtons(main, p => { location.hash = p ? `#library/${p}` : "#library"; });
  $("#out").innerHTML = await outputsHtml(path);
}

// ------------------------------------------------------------------ jobs

async function jobPage(id) {
  let since = 0, logText = "", last = null;
  main.innerHTML = `<p class="small"><a href="#jobs">Jobs</a></p><div id="job-head"></div>
    <div class="card stack"><div class="row spread"><h3 style="margin:0">Log</h3><label class="check" style="padding:0"><input type="checkbox" id="follow" checked><span class="small">Follow</span></label></div><pre class="log" id="log"></pre></div>
    <div class="card stack"><h3>Output</h3><div id="out" class="muted">Appears here as the job produces files.</div></div>`;
  const refreshOut = async j => { $("#out").innerHTML = await outputsHtml(j.outdir); };
  const tick = async () => {
    const j = await api(`api/jobs/${id}?since=${since}`);
    if (j.log_start < since) logText = "";
    logText += j.log; since = j.log_size;
    const pre = $("#log");
    if (!pre) return;
    pre.textContent = logText.replace(/\r(?!\n)/g, "\n");
    if ($("#follow").checked) pre.scrollTop = pre.scrollHeight;
    const active = j.status === "running" || j.status === "queued";
    const took = j.started ? fmtDur((j.ended || Date.now() / 1000) - j.started) : "";
    $("#job-head").innerHTML = `<div class="row spread"><h2 style="margin:0">${esc(j.title)}</h2>
        <div class="row"><span class="status ${j.status}">${j.status}${j.status === "failed" ? ` (exit ${j.code})` : ""}</span>
        <span class="muted small">${took}</span>
        ${active ? `<button class="danger small" id="cancel">Cancel</button>` : `<button class="small" id="again">Run again</button>`}</div></div>
      <p class="muted small" style="margin-top:8px">${esc(j.script)} ${esc(j.args.map(a => /\s/.test(a) ? `"${a}"` : a).join(" "))}</p>
      ${j.outdir.startsWith("story/") ? `<p><a href="#story/${esc(j.outdir.slice(6))}">Open the storyboard →</a></p>` : ""}`;
    const c = $("#cancel");
    if (c) c.onclick = async () => { if (confirm("Stop this job?")) { await api(`api/jobs/${id}/cancel`, {}); tick(); } };
    const again = $("#again");
    if (again) again.onclick = () => rerun(j);
    if (last !== j.status || (active && Date.now() % 20000 < 2600)) refreshOut(j);
    last = j.status;
    if (!active) clearTimers();
  };
  await tick();
  timers.push(setInterval(tick, 2500));
}

function rerun(j) {
  const hasUpload = /--(image|end-image|refs)\b/.test(j.args.join(" ")) || j.tool === "art3d";
  if (hasUpload) { alert("This job used uploaded images. Start it again from its page and pick the files."); return; }
  api(`api/run/${j.tool}`, j.form).then(nj => location.hash = `#jobs/${nj.id}`).catch(e => alert(e.message));
}

async function updateBadge() {
  try {
    const jobs = await api("api/jobs");
    const n = jobs.filter(j => j.status === "running" || j.status === "queued").length;
    const b = $("#jobs-badge"); b.hidden = !n; b.textContent = n;
  } catch { }
}

// ------------------------------------------------------------------ storyboard detail

async function storyPage(name) {
  const info = await api(`api/story/${encodeURIComponent(name)}`);
  const sb = info.storyboard, checks = sb.checks || {};
  const v = f => `?v=${Math.round(info.mtimes[f] || 0)}`;
  const kfs = sb.keyframes.map((k, i) => {
    const file = `key-${String(i).padStart(2, "0")}.png`;
    const c = checks[i] || checks[String(i)];
    const flag = c ? `<span class="flag ${c.ok ? "ok" : "weak"}">${c.ok ? "✓" : "WEAK"} ${esc(c.note || "")}</span>` : "";
    const img = info.keyframes.includes(file) ? `<a href="${outUrl(`story/${name}/${file}`)}" target="_blank"><img src="${outUrl(`story/${name}/${file}`)}${v(file)}" alt="keyframe ${i}"></a>` : `<div class="thumb" style="aspect-ratio:16/10;background:var(--panel-2)"></div>`;
    return `<div class="kf">${img}<div class="body stack">
      <div class="row spread"><b>Keyframe ${i} · ${k.t}s</b><button class="small redraw" data-i="${i}">Redraw</button></div>${flag}
      <textarea data-kf="${i}" aria-label="keyframe ${i} description">${esc(k.image)}</textarea></div></div>`;
  }).join("");
  const segs = sb.segments.map((s, i) => `<div class="seg stack"><b>Segment ${i} · ${s.from}–${s.to}s</b>
      <label>Action<textarea data-seg="${i}" data-f="action">${esc(s.action)}</textarea></label>
      <label>Sound<textarea data-seg="${i}" data-f="sound">${esc(s.sound || "")}</textarea></label></div>`).join("");
  const vids = rankFiles(info.videos.filter(f => !f.endsWith(".ogv")).map(n => ({ name: n })))
    .map(f => mediaHtml(`story/${name}/${f.name}`, f.name)).join("");
  main.innerHTML = `<div class="stack">
    <div class="row spread"><p class="small" style="margin:0"><a href="#story">Storyboards</a> / <b>${esc(name)}</b> · <a href="#library/story/${encodeURIComponent(name)}">all files</a></p>
      ${itemButtons(`story/${name}`, name)}</div>
    <div><h2>${esc(sb.title || name)}</h2><p class="muted">${esc(sb.idea || "")}</p>
      <p class="small muted">Characters: ${esc(sb.characters)}<br>Setting: ${esc(sb.setting)}<br>Style: ${esc(sb.style)}</p></div>
    ${vids ? `<div class="media-grid">${vids}</div>` : ""}
    <form class="card stack" id="render-form"><h3>Render</h3>
      <div class="grid">
        ${fieldHtml(F.check("draft", "Draft", { value: true, help: "Low resolution, ~4× faster" }))}
        ${fieldHtml(F.num("end_strength", "Keyframe strength", { min: 0, max: 1, step: 0.05, ph: "0.4", help: "How hard each segment is pulled to its next keyframe (higher rushes then holds still)" }))}
        ${fieldHtml(F.text("only", "Re-render segments", { ph: "e.g. 2", help: "New seed for these; later ones follow" }))}
        ${fieldHtml(F.text("music", "Music bed", { ph: "epic orchestral battle music", help: "Optional, ACE-Step" }))}
        ${fieldHtml(F.check("classic", "Classic segments", { help: "Pin both ends of each segment instead of continuing" }))}
      </div>
      <div class="row"><button class="primary" type="submit">Render</button><span class="error" id="render-err"></span></div></form>
    <div class="row spread"><h3 style="margin:0">Keyframes</h3><span class="row"><button class="small" id="redraw-all">Redraw all</button></span></div>
    <div class="kf-grid">${kfs}</div>
    <div class="card stack"><h3>Segments</h3>${segs}</div>
    <div class="row"><button class="primary" id="save">Save text changes</button><span class="muted small">Then redraw the keyframes you changed. Segments re-render automatically when their text changes.</span><span class="error" id="save-err"></span></div>
  </div>`;
  bindItemButtons(main.querySelector(".item-actions"), (p, n) => { location.hash = p ? `#story/${encodeURIComponent(n)}` : "#story"; });

  const run = async body => {
    const job = await api("api/run/storyboard", { name, ...body });
    location.hash = `#jobs/${job.id}`;
  };
  main.querySelectorAll(".redraw").forEach(b => b.onclick = () => run({ step: "frames", only: b.dataset.i }).catch(e => alert(e.message)));
  $("#redraw-all").onclick = () => confirm("Redraw every keyframe?") && run({ step: "frames" }).catch(e => alert(e.message));
  const rf = $("#render-form");
  const rfields = [F.check("draft"), F.num("end_strength"), F.text("only"), F.text("music"), F.check("classic")];
  rf.onsubmit = async e => {
    e.preventDefault(); $("#render-err").textContent = "";
    try { await run({ step: "render", ...await formValues(rf, rfields) }); } catch (err) { $("#render-err").textContent = err.message; }
  };
  $("#save").onclick = async () => {
    $("#save-err").textContent = "";
    const next = JSON.parse(JSON.stringify(sb));
    main.querySelectorAll("[data-kf]").forEach(t => next.keyframes[+t.dataset.kf].image = t.value.trim());
    main.querySelectorAll("[data-seg]").forEach(t => next.segments[+t.dataset.seg][t.dataset.f] = t.value.trim());
    try { await api(`api/story/${encodeURIComponent(name)}`, { storyboard: next }); $("#save").textContent = "Saved"; setTimeout(() => $("#save") && ($("#save").textContent = "Save text changes"), 1500); }
    catch (err) { $("#save-err").textContent = err.message; }
  };
}

// ------------------------------------------------------------------ settings

async function settingsPage() {
  const s = await api("api/settings");
  const src = { portal: "a key saved here", env: "the portal's LEMONADE_API_KEY environment variable", file: `the key file on this machine (${s.key_file})`, none: "no key" }[s.key_source];
  main.innerHTML = `<div class="stack"><h2>Settings</h2>
    <form class="card stack" id="lemon-form"><h3>Lemonade server</h3>
      <p class="muted small">Images, 3D, vision, music and sound effects come from Lemonade. Point this at another machine to use its models. Video rendering stays on this machine.</p>
      <label>Address<span class="help">Leave empty for this machine (${esc(s.default_url)})</span>
        <input type="url" name="lemonade_url" value="${esc(s.lemonade_url)}" placeholder="http://other-machine:13305/api/v1"></label>
      <label>API key<span class="help">Currently using ${esc(src)}. Type a new one to replace it; it's stored on this machine only and never shown again.</span>
        <input type="password" name="lemonade_key" autocomplete="off" placeholder="unchanged"></label>
      ${s.key_source === "portal" ? `<label class="check" style="padding:0"><input type="checkbox" name="clear_key"><span>Forget the saved key</span></label>` : ""}
      <div class="row"><button type="button" id="test">Test connection</button><button class="primary" type="submit">Save</button><span id="lemon-msg" class="small"></span></div>
      <div id="test-out"></div>
    </form>
    <div class="card stack"><h3>This machine</h3>
      <p class="muted small">Video, storyboards and the Blender steps of 3D always run here, whichever Lemonade you use, so their parts are checked here rather than in the Lemonade test.</p>
      <div id="local-out" class="muted">Checking…</div></div></div>`;
  api("api/local").then(r => {
    const gpu = r.gpu_free_gb == null ? "" : `<p class="small">GPU memory free now: <b>${r.gpu_free_gb} GB</b> <span class="muted">(video needs about ${r.gpu_needed_gb} GB; when Lemonade runs here, video unloads its models to make room)</span></p>`;
    $("#local-out").className = "";
    $("#local-out").innerHTML = `<table class="models"><thead><tr><th>Part</th><th>Used for</th><th></th></tr></thead><tbody>${r.items.map(m =>
      `<tr><td>${esc(m.name)}${m.detail ? `<div class="muted small" style="font-family:inherit">${esc(m.detail)}</div>` : ""}</td><td class="muted">${esc(m.use)}</td><td>${m.ok ? `<span class="ok-text">installed</span>` : `<span class="bad-text">missing</span> <span class="muted small">${esc(m.fix)}</span>`}</td></tr>`).join("")}</tbody></table>${gpu}`;
  }).catch(e => { $("#local-out").innerHTML = `<p class="error">${esc(e.message)}</p>`; });
  const form = $("#lemon-form");
  const vals = () => ({ lemonade_url: form.lemonade_url.value.trim(), lemonade_key: form.lemonade_key.value.trim(), clear_key: !!(form.clear_key && form.clear_key.checked) });
  $("#test").onclick = async () => {
    $("#test-out").innerHTML = `<p class="muted">Connecting…</p>`;
    try { showTest(await api("api/settings/test", vals())); } catch (e) { $("#test-out").innerHTML = `<p class="error">${esc(e.message)}</p>`; }
  };
  form.onsubmit = async e => {
    e.preventDefault();
    try {
      await api("api/settings", vals());
      $("#lemon-msg").innerHTML = `<span class="ok-text">Saved. New jobs use it.</span>`;
      form.lemonade_key.value = "";
      checkLemon();
    } catch (err) { $("#lemon-msg").innerHTML = `<span class="bad-text">${esc(err.message)}</span>`; }
  };
}

function showTest(r) {
  if (!r.ok) { $("#test-out").innerHTML = `<p class="error">${esc(r.error)}</p>`; return; }
  $("#test-out").innerHTML = `<p class="ok-text">Connected.</p><table class="models"><thead><tr><th>Model</th><th>Used for</th><th></th></tr></thead><tbody>${r.models.map(m =>
    `<tr><td>${esc(m.id)}</td><td class="muted">${esc(m.use)}</td><td>${m.present ? `<span class="ok-text">installed</span>` : `<span class="bad-text">missing</span> <span class="muted small">lemonade pull ${esc(m.id)}</span>`}</td></tr>`).join("")}</tbody></table>`;
}

async function checkLemon() {
  const d = $("#lemon-dot");
  try {
    const r = await api("api/settings/test", {});
    d.className = "dot " + (r.ok ? "ok" : "bad");
    d.title = r.ok ? "Lemonade connected" : `Lemonade: ${r.error}`;
  } catch { d.className = "dot bad"; }
}

// ------------------------------------------------------------------ shell

function showLogin() {
  clearTimers();
  $("#app").hidden = true; $("#login").hidden = false;
  $("#login-pw").focus();
}

$("#login-form").onsubmit = async e => {
  e.preventDefault();
  $("#login-err").textContent = "";
  try {
    await api("api/login", { password: $("#login-pw").value });
    $("#login-pw").value = "";
    start();
  } catch (err) { $("#login-err").textContent = err.message; }
};
$("#logout").onclick = async () => { await api("api/logout", {}); showLogin(); };

async function route() {
  clearTimers();
  const [page, ...rest] = (location.hash.slice(1) || "3d").split("/");
  const arg = rest.length ? decodeURIComponent(rest.join("/")) : undefined;
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.getAttribute("href") === "#" + page));
  try {
    await (pages[page] || pages["3d"])(arg);
  } catch (e) {
    if (e.message !== "log in") main.innerHTML = `<p class="error">${esc(e.message)}</p>`;
  }
  updateBadge();
}

let started = false;
function start() {
  $("#login").hidden = true; $("#app").hidden = false;
  route();
  checkLemon();
  if (!started) {
    started = true;
    window.addEventListener("hashchange", route);
    setInterval(updateBadge, 10000);
  }
}

(async () => {
  const me = await fetch("api/me").then(r => r.json()).catch(() => ({ ok: false }));
  me.ok ? start() : showLogin();
})();
