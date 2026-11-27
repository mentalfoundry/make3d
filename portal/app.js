"use strict";
// make3d portal front end. All URLs are relative so it works behind a path prefix (e.g. /make3d/).

const $ = (s, el = document) => el.querySelector(s);
const main = $("#main");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const outUrl = p => "out/" + p.split("/").map(encodeURIComponent).join("/");
let timers = [], pageSubs = [];
const clearTimers = () => { timers.forEach(clearInterval); timers = []; pageSubs.forEach(f => bus.delete(f)); pageSubs = []; };

// Server-sent events: the portal pushes job and storyboard changes, so pages don't poll.
// listen() subscribes for the current page only (cleared on navigation).
const bus = new Set();
let events = null;
function connectEvents() {
  if (events) return;
  let opened = false;
  events = new EventSource("api/events");
  events.onopen = () => { if (opened) bus.forEach(f => f({ type: "reconnect" })); opened = true; };
  events.onmessage = e => { const ev = JSON.parse(e.data); bus.forEach(f => f(ev)); };
  events.onerror = () => {   // the browser retries by itself unless the server refused (e.g. logged out)
    if (events.readyState === EventSource.CLOSED) { events = null; setTimeout(() => { if (!$("#app").hidden) connectEvents(); }, 5000); }
  };
}
const listen = f => { bus.add(f); pageSubs.push(f); };

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

function toolPage({ title, intro, tool, fields, extra = {}, note, top = "" }) {
  main.innerHTML = `<div class="stack">
    <div><h2>${esc(title)}</h2><p class="muted">${intro}</p></div>${top}
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
      intro: `Describe a character or object and pick what kind of asset it is. Flux draws a concept (characters in an A-pose), TRELLIS builds the mesh, Blender cleans, textures and rigs it. Expect 10–20 minutes for a character. To start from an image, use <a href="#art">Art to 3D</a>.`,
      fields: [
        F.area("description", "Description", { req: true, ph: "a dwarf blacksmith with a leather apron and a braided red beard" }),
        F.sel("preset", "Asset type", await presetOptions(), { help: "Sets triangles, textures, detail and size", note: true }),
        F.text("name", "Name", { help: "Output folder (default: from the description)" }),
        F.check("prop", "Prop, not a character", { help: "No pose template, no rig" }),
        F.sel("template", "Pose template", await templateOptions(), { help: "Body shape and pose for characters" }),
        F.num("options", "Concept options", { min: 1, max: 6, step: 1, ph: "1", help: "Draw several and stop, so you can pick one with 'Make 3D from this'" }),
        F.sel("resolution", "TRELLIS resolution", [["", "default"], "512", "1024", "1536"], { help: "Default: 1024 characters, 512 props" }),
        F.num("height", "Height (m)", { min: 0.05, max: 50, ph: "1.8 / 1.0" }),
        F.num("tris", "Triangle budget", { min: 500, max: 500000, step: 100, ph: "30000" }),
        F.sel("tex", "Texture size", [["", "default (2048)"], "512", "1024", "2048", "4096"]),
        F.num("seed", "Seed", { min: 0, step: 1, ph: "42" }),
        F.check("no_rig", "Skip rigging"),
      ],
    });
    presetHints($("#tool-form"));
  },

  async art(from) {
    const MODES = [["pose", "Character in any pose: redraw it in an A-pose"],
                   ["posed", "Character already in an A- or T-pose: use as-is"],
                   ["object", "Object or structure: use as-is"]];
    // From a concept/pose option in the library: it's already posed (or an object)
    let mode = "pose";
    if (from) {
      mode = "posed";
      const jobs = await api("api/jobs").catch(() => []);
      const j = jobs.find(j => j.outdir === from.split("/")[0] && j.tool === "make3d");
      if (j && (j.form.prop || presetData?.presets[j.form.preset]?.kind === "prop")) mode = "object";
    }
    await presetOptions();
    toolPage({
      title: "Art to 3D", tool: "art3d", extra: from ? { from } : {},
      intro: "Start from your own image. Character art in any pose is described by the vision model and redrawn in an A- or T-pose in your style, and the best candidate goes on to 3D. Art that's already posed, and objects, go straight to 3D.",
      note: from ? `Using <b>${esc(from)}</b> from the library. <a href="#art">Upload a different image instead</a><br><img src="${outUrl(from)}" alt="" style="max-height:220px;margin-top:8px;border-radius:6px">` : "",
      fields: [
        ...(from ? [] : [F.file("art", "Image")]),
        F.sel("mode", "What is it?", MODES, { value: mode, full: true }),
        F.text("name", "Name", { help: from ? `Default: ${from.split("/")[0]}` : "Default: from the file name" }),
        F.sel("preset", "Asset type", [], { help: "Sets triangles, textures and detail", note: true }),
        F.num("height", "Height (m)", { min: 0.05, max: 50, ph: "1.8 / 1.0" }),
        F.sel("pose", "Pose", [["a", "A-pose (recommended)"], ["t", "T-pose"]]),
        F.sel("template", "Pose template", await templateOptions(), { help: "Default: picked from the art (build)" }),
        F.text("hint", "Hint", { full: true, help: "Correct anything the vision model gets wrong, e.g. 'she carries a bow on her back'" }),
        F.num("tris", "Triangle budget", { min: 500, max: 500000, step: 100, ph: "30000" }),
        F.sel("tex", "Texture size", [["", "default (2048)"], "512", "1024", "2048", "4096"]),
        F.num("options", "Candidates", { min: 1, max: 6, step: 1, ph: "3" }),
        F.num("seed", "Seed", { min: 0, step: 1, ph: "42" }),
        F.check("face", "Match the face closely", { help: "Gives the redraw a close-up of the face and checks each candidate's likeness. About a minute slower. Only use for real people who have agreed to it." }),
        F.check("stop", "Stop after the pose candidates", { help: "Pick one yourself with 'Make 3D from this'" }),
      ],
    });
    const form = $("#tool-form");
    const sync = () => {
      const m = form.elements.mode.value;
      // Redraw-only fields
      for (const k of ["pose", "template", "hint", "options", "stop", "face"]) form.elements[k].closest("label").hidden = m !== "pose";
      // Asset types that fit: characters or objects
      const kind = m === "object" ? "prop" : "character", sel = form.elements.preset, keep = sel.value;
      sel.innerHTML = [["", "custom / default"], ...Object.entries(presetData.presets).filter(([, p]) => p.kind === kind).map(([k, p]) => [k, p.label])]
        .map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join("");
      sel.value = [...sel.options].some(o => o.value === keep) ? keep : "";
      sel.onchange?.();
    };
    form.elements.mode.addEventListener("change", sync);
    sync();
    presetHints(form);
  },

  async video(arg) {
    const switcher = which => `<div class="switch" role="tablist">
      <a href="#video" role="tab" class="${which === "prompt" ? "active" : ""}">From a prompt</a>
      <a href="#video/story" role="tab" class="${which === "story" ? "active" : ""}">From a storyboard</a></div>`;
    const gpuNote = "Video always renders on this machine's GPU, even when Lemonade is elsewhere.";
    if (arg === "story" || arg?.startsWith("story/")) return videoFromStoryboard(arg.slice(6), switcher("story"), gpuNote);
    toolPage({
      title: "Video with sound", tool: "makevideo", top: switcher("prompt"),
      intro: "LTX-2.3 makes one clip with matching audio from a prompt, optionally starting from an image (and ending on another). For a longer scene with several shots, make it from a storyboard.",
      note: gpuNote + " About 6 minutes per 5 seconds at full size; drafts take 1–2 minutes.",
      fields: [
        F.area("prompt", "Prompt", { req: true, ph: "The <knight> warms his hands at a campfire at night, sparks rising. Sound: crackling fire, wind in the trees." , help: "Describe the motion and the sounds you want. Type < to put a 3D model from your library in the shot: Flux draws the start frame with it, so say how the shot starts (\"sword lowered, he slowly raises it\")." }),
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
    const vf = $("#tool-form");
    attachRefPicker(vf.elements.prompt);
    const startHelp = () => { vf.elements.image.closest("label").querySelector("[data-lbl]").textContent =
      /<[\w-]+>/.test(vf.elements.prompt.value) ? "Start image (replaces the one drawn from <references>)" : "Start image"; };
    vf.elements.prompt.addEventListener("input", startHelp);
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
      F.area("idea", "What happens", { req: true, ph: "a <knight> fights a <goblin> in a ruined castle courtyard at dusk", help: "Type < to cast a 3D model from your library: its A-pose sheet keeps the character looking the same in every shot." }),
      F.files("refs", "Other reference images", { help: "Optional, for characters that aren't in the library. Name each file after its placeholder: dragon.png for <dragon>." }),
      F.num("seconds", "Length (s)", { min: 5, max: 120, ph: "30", help: "A target: each beat gets the time it needs, so the plan may come out shorter" }),
      F.check("continuous", "One continuous take", { help: "Old mode: no cuts, a keyframe every few seconds. Leave off for a shot list with cuts (much better results)" }),
      F.num("every", "Keyframe every (s)", { min: 2, max: 10, ph: "5", help: "One continuous take only" }),
      F.text("style", "Style", { ph: "hand-painted fantasy art, soft light" }),
      F.num("tries", "Redraws per keyframe", { min: 1, max: 5, step: 1, ph: "3" }),
      F.num("seed", "Seed", { min: 0, step: 1, ph: "42" }),
      F.check("force", "Replace if it exists"),
    ];
    main.innerHTML = `<div class="stack">
      <div><h2>Storyboard</h2><p class="muted">Plan a film as a shot list (wides, close-ups, inserts and reactions, 2–6 s each), check each shot's first frame and the timed animatic, then render every shot and cut them together with sound.</p></div>
      ${stories.length ? `<div class="tiles">${stories.map(tile).join("")}</div>` : ""}
      <form class="card stack" id="tool-form"><h3>New storyboard</h3><div class="grid">${fields.map(fieldHtml).join("")}</div>
        <div class="row"><button class="primary" type="submit">Plan and draw shots</button><span class="error" id="form-err"></span></div>
      </form></div>`;
    if (stories.length) bindItemButtons(main.querySelector(".tiles"), () => pages.story());
    const form = $("#tool-form");
    const uploads = () => [...form.elements.refs.files].map(f => f.name.replace(/\.[^.]+$/, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""));
    const refresh = await attachRefPicker(form.elements.idea, { uploads });
    form.elements.refs.addEventListener("change", refresh);
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
    await draw();
    listen(ev => { if (ev.type === "job" || ev.type === "reconnect") draw(); });
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
    listen(ev => { if (ev.type === "library" || (ev.type === "job" && ev.status !== "queued" && ev.status !== "running")) api("api/library").then(l => { lib.splice(0, lib.length, ...l); draw(); }); });
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

// ------------------------------------------------------------------ <name> reference picker

// Typing "<" in a prompt opens a list of 3D models in the library; picking one inserts <name>.
// Under the box, each <name> in the text shows the image that will be used for it.
let refList = null;
async function attachRefPicker(ta, { uploads } = {}) {
  refList = refList || await api("api/refs").catch(() => []);
  const label = ta.closest("label");
  label.style.position = "relative";
  const menu = document.createElement("div");
  menu.className = "refpick"; menu.hidden = true; menu.setAttribute("role", "listbox");
  const chips = document.createElement("div");
  chips.className = "refchips";
  label.after(chips);
  label.appendChild(menu);
  let items = [], sel = 0, start = -1;

  const query = () => {
    const before = ta.value.slice(0, ta.selectionStart);
    const m = before.match(/<([\w-]*)$/);
    return m ? { q: m[1].toLowerCase(), at: before.length - m[0].length } : null;
  };
  const draw = () => {
    menu.innerHTML = items.length ? items.map((r, i) =>
      `<div class="opt${i === sel ? " sel" : ""}" role="option" data-i="${i}"><img src="${outUrl(r.image)}" alt=""><span>${esc(r.name)}</span></div>`).join("")
      : `<div class="empty">No 3D model matches. Make one first, or rename one in the Library.</div>`;
  };
  const open = () => {
    const q = query();
    if (!q) { menu.hidden = true; return; }
    start = q.at;
    items = refList.filter(r => r.name.includes(q.q)).sort((a, b) => a.name.startsWith(q.q) ? -1 : b.name.startsWith(q.q) ? 1 : 0);
    sel = 0; draw(); menu.hidden = false;
  };
  const pick = r => {
    const end = ta.selectionStart, after = ta.value.slice(end);
    const ins = `<${r.name}>` + (/^[\s.,!?;:]/.test(after) ? "" : " ");
    ta.value = ta.value.slice(0, start) + ins + after;
    ta.selectionStart = ta.selectionEnd = start + ins.length;
    menu.hidden = true; ta.focus(); update();
  };
  const update = () => {
    const names = [...new Set([...ta.value.matchAll(/<([\w-]+)>/g)].map(m => m[1]))];
    const up = uploads ? uploads() : [];
    chips.innerHTML = names.map(n => {
      const r = refList.find(x => x.name === n.toLowerCase());
      if (up.includes(n.toLowerCase())) return `<span class="chip"><b>&lt;${esc(n)}&gt;</b> your uploaded image</span>`;
      return r ? `<span class="chip"><img src="${outUrl(r.image)}" alt=""><b>&lt;${esc(n)}&gt;</b> from the library</span>`
               : `<span class="chip bad"><b>&lt;${esc(n)}&gt;</b> no 3D model with this name${uploads ? ` (or upload ${esc(n)}.png below)` : ""}</span>`;
    }).join("");
  };
  ta.addEventListener("input", () => { open(); update(); });
  ta.addEventListener("click", open);
  ta.addEventListener("keydown", e => {
    if (menu.hidden) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + items.length) % Math.max(items.length, 1); draw();
    } else if ((e.key === "Enter" || e.key === "Tab") && items[sel]) { e.preventDefault(); pick(items[sel]); }
    else if (e.key === "Escape") { menu.hidden = true; }
  });
  ta.addEventListener("blur", () => setTimeout(() => { menu.hidden = true; }, 150));
  menu.addEventListener("mousedown", e => {
    const o = e.target.closest(".opt");
    if (o) { e.preventDefault(); pick(items[+o.dataset.i]); }
  });
  update();
  return update;
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
  const pick = /^(concept|pose)-option-\d+\.(png|jpe?g|webp)$/i.test(name) ? `<a class="pick" href="#art/${esc(rel)}">Make 3D from this</a>` : "";
  const cap = `<div class="cap"><span>${esc(name)}</span><span class="muted">${size != null ? fmtSize(size) + " · " : ""}${dl}</span></div>${pick}`;
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
    if (n.endsWith(".webm") || n.endsWith(".mp4")) return n.includes("-animatic.") ? 1.5 : 1;   // the animatic is stills
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

async function videoFromStoryboard(name, switcher, gpuNote) {
  const stories = (await api("api/library")).filter(x => x.kind === "story");
  const head = `<div class="stack"><div><h2>Video with sound</h2><p class="muted">Render a storyboard into one video: each shot is rendered from its first frame and the shots are cut together with sound.</p></div>${switcher}`;
  if (!stories.length) {
    main.innerHTML = head + `<div class="card"><p>No storyboards yet.</p><p><a href="#story">Make a storyboard</a> from an idea first: it plans the shots and draws a keyframe for each, which you can check and edit before rendering.</p></div></div>`;
    return;
  }
  name = name || stories[0].name;
  if (!stories.some(x => x.name === name)) name = stories[0].name;
  const info = await api(`api/story/${encodeURIComponent(name)}`);
  const sb = info.storyboard, checks = sb.checks || {};
  const n = sb.keyframes.length, drawn = info.keyframes.length;
  const weak = Object.values(checks).filter(c => c && !c.ok).length;
  const shotsMode = sb.kind === "shots";
  const len = shotsMode ? sb.keyframes.reduce((a, k) => a + (+k.seconds || 0), 0) : (sb.keyframes[n - 1]?.t ?? 0);
  const strip = sb.keyframes.map((k, i) => {
    const f = `key-${String(i).padStart(2, "0")}.png`;
    return info.keyframes.includes(f) ? `<img src="${outUrl(`story/${name}/${f}`)}?v=${Math.round(info.mtimes[f] || 0)}" alt="keyframe ${i}" title="${esc(k.t + "s: " + k.image)}">` : `<div class="missing">${i}</div>`;
  }).join("");
  const vids = info.videos.filter(f => !f.endsWith(".ogv"));
  main.innerHTML = head + `<div class="notice small">${gpuNote} A full render takes roughly 1.5 minutes per second of video; a draft about 40% of that.</div>
    <div class="card stack">
      <label>Storyboard<select id="sb-pick">${stories.map(x => `<option value="${esc(x.name)}"${x.name === name ? " selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
      <div><b>${esc(sb.title || name)}</b> <span class="muted small">· ${len}s · ${shotsMode ? `${n} shots` : `${n} keyframes · ${n - 1} segments, one take`}</span>
        <p class="muted small" style="margin:4px 0 0">${esc(sb.idea || "")}</p></div>
      <div class="kf-strip">${strip}</div>
      ${drawn < n ? `<p class="error">${n - drawn} keyframe(s) aren't drawn yet. Draw them on the storyboard page first.</p>` : ""}
      ${weak ? `<p class="small" style="color:var(--warn)">${weak} keyframe(s) flagged WEAK by the check. Look at them before a full render.</p>` : ""}
      <p class="small"><a href="#story/${encodeURIComponent(name)}">Edit this storyboard</a> (keyframes, shot text, sounds) · <a href="#story">New storyboard</a>${vids.length ? ` · already rendered: ${vids.map(v => `<a href="${outUrl(`story/${name}/${v}`)}" target="_blank">${esc(v)}</a>`).join(", ")}` : ""}</p>
    </div>
    ${renderFormHtml(sb)}</div>`;
  $("#sb-pick").onchange = e => { location.hash = `#video/story/${encodeURIComponent(e.target.value)}`; };
  const rf = $("#render-form");
  if (drawn < n) rf.querySelector("button[type=submit]").disabled = true;
  rf.onsubmit = async e => {
    e.preventDefault(); $("#render-err").textContent = "";
    try {
      const job = await api("api/run/storyboard", { name, step: "render", ...await formValues(rf, renderFields(sb)) });
      location.hash = `#jobs/${job.id}`;
    } catch (err) { $("#render-err").textContent = err.message; }
  };
}

// The storyboard render form, used on the storyboard page and on the Video page
const RENDER_FIELDS = [
  F.check("draft", "Draft", { value: true, help: "Low resolution, about 2.5× faster" }),
  F.num("end_strength", "Keyframe strength", { min: 0, max: 1, step: 0.05, ph: "0.4", help: "How hard each segment is pulled to its next keyframe (higher rushes then holds still)" }),
  F.text("only", "Re-render segments", { ph: "e.g. 2", help: "New seed for these; later ones follow" }),
  F.text("music", "Music bed", { ph: "epic orchestral battle music", help: "Optional, ACE-Step" }),
  F.check("classic", "Classic segments", { help: "Pin both ends of each segment instead of continuing" }),
];
const SHOT_RENDER_FIELDS = [
  F.check("draft", "Draft", { value: true, help: "Low resolution, about 2.5× faster, one take per shot" }),
  F.num("takes", "Takes per shot", { min: 1, max: 4, step: 1, ph: "draft 1, full 2", help: "The vision model keeps the best; you can switch takes below" }),
  F.text("only", "Re-render shots", { ph: "e.g. 2,5", help: "New takes with a new seed" }),
  F.text("music", "Music bed", { ph: "tense synth pulse", help: "Optional, ACE-Step" }),
  F.check("no_ambience", "No ambience track", { help: "Leave out the background sound under the whole scene" }),
];
const renderFields = sb => sb?.kind === "shots" ? SHOT_RENDER_FIELDS : RENDER_FIELDS;
function renderFormHtml(sb) {
  return `<form class="card stack" id="render-form"><h3>Render</h3>
      <div class="grid">${renderFields(sb).map(fieldHtml).join("")}</div>
      <div class="row"><button class="primary" type="submit">Render</button><span class="error" id="render-err"></span></div></form>`;
}

const SHOT_SIZES = ["wide", "medium", "close-up", "insert", "over-the-shoulder", "reaction"];
// A storyboard field by name: "image", "who" (a list), "line.text", "seconds", ...
const getField = (k, f) => f === "who" ? (k.who || []).join(", ") : f.startsWith("line.") ? (k.line?.[f.slice(5)] ?? "") : String(k[f] ?? "");
function setField(k, f, v) {
  if (f === "who") k.who = v.split(",").map(x => x.trim()).filter(Boolean);
  else if (f === "seconds") k.seconds = +v || k.seconds;
  else if (f.startsWith("line.")) { k.line = { ...(k.line || {}), [f.slice(5)]: v }; if (!k.line.text) k.line = null; }
  else k[f] = v;
}

async function storyPage(name) {
  let info = await api(`api/story/${encodeURIComponent(name)}`);
  let sb = info.storyboard;
  const shotsMode = sb.kind === "shots";
  const castNames = (sb.cast || []).map(c => c.name);
  const kfile = i => `key-${String(i).padStart(2, "0")}.png`;
  // Rendered takes of shot i, full quality first; the one in use is marked, the others can be picked
  const takesHtml = i => ["full", "draft"].filter(tag => info.takes?.[tag]?.[i]).map(tag => {
    const list = info.takes[tag][i], pick = info.storyboard.takes?.[tag]?.[i];
    return `<div class="small muted">${tag === "full" ? "Full" : "Draft"} take${list.length > 1 ? "s" : ""}${pick?.weak ? ` <span class="flag weak">WEAK: every take scored low; re-render this shot or make its action more visible</span>` : ""}</div><div class="take-row">${list.map(({ take, mtime }) => {
      const using = pick?.take === take, sc = pick?.scores?.[take];
      return `<div class="take${using ? " chosen" : ""}"><video src="${outUrl(`story/${name}/shots-${tag}/shot-${String(i).padStart(2, "0")}-t${take}.webm`)}?v=${mtime}" controls preload="metadata" playsinline></video>
        <div class="row small spread"><span>Take ${take + 1}${using ? (pick.by === "you" ? " · your pick" : list.length > 1 ? " · best" : "") : ""}</span>${list.length > 1 && !using ? `<button class="small ghost use" data-i="${i}" data-t="${take}" data-tag="${tag}">Use this</button>` : ""}</div>
        ${sc?.note ? `<div class="muted small">${esc(sc.note)}</div>` : ""}</div>`;
    }).join("")}</div>`;
  }).join("");
  const shotFieldsHtml = (k, i) => `
      <div class="grid shot-meta">
        <label class="small">Shot size<select data-kf="${i}" data-f="size">${SHOT_SIZES.map(z => `<option${z === k.size ? " selected" : ""}>${z}</option>`).join("")}</select></label>
        <label class="small">Camera<input type="text" data-kf="${i}" data-f="camera" value="${esc(k.camera || "")}"></label>
        <label class="small">Seconds<input type="number" min="1" max="8" step="0.5" data-kf="${i}" data-f="seconds" value="${esc(k.seconds)}"></label>
        <label class="small">In shot<input type="text" data-kf="${i}" data-f="who" value="${esc((k.who || []).join(", "))}" title="Names from the cast: ${esc(castNames.join(", "))}"></label>
      </div>
      <label class="small">First frame<textarea data-kf="${i}" data-f="image" aria-label="shot ${i} first frame">${esc(k.image)}</textarea></label>
      <label class="small">Action<textarea data-kf="${i}" data-f="action" rows="2">${esc(k.action || "")}</textarea></label>
      <div class="grid shot-line">
        <label class="small">Speaker<select data-kf="${i}" data-f="line.speaker"><option value="">nobody</option>${castNames.map(c => `<option${c === k.line?.speaker ? " selected" : ""}>${esc(c)}</option>`).join("")}</select></label>
        <label class="small">Spoken line<input type="text" data-kf="${i}" data-f="line.text" value="${esc(k.line?.text || "")}" placeholder="no dialogue"></label>
      </div>
      <label class="small">Sound<textarea data-kf="${i}" data-f="sound" rows="1">${esc(k.sound || "")}</textarea></label>
      <div class="takes stack" data-takes="${i}">${takesHtml(i)}</div>`;
  const flagHtml = i => {
    const c = (info.storyboard.checks || {})[String(i)];
    return c ? `<span class="flag ${c.ok ? "ok" : "weak"}">${c.ok ? "✓" : "WEAK"} ${esc(c.note || "")}</span>` : "";
  };
  const imgHtml = i => {
    const f = kfile(i);
    return info.keyframes.includes(f)
      ? `<a href="${outUrl(`story/${name}/${f}`)}" target="_blank"><img src="${outUrl(`story/${name}/${f}`)}?v=${Math.round(info.mtimes[f] || 0)}" alt="keyframe ${i}"></a>`
      : `<div class="thumb" style="aspect-ratio:3/2;background:var(--panel-2)"></div>`;
  };
  const kfs = sb.keyframes.map((k, i) => `<div class="kf" data-card="${i}"><div class="kf-img">${imgHtml(i)}<div class="busy" hidden><div class="spinner" aria-hidden="true"></div><span class="busy-text">Redrawing…</span></div></div><div class="body stack">
      <div class="row spread"><b>${shotsMode ? `Shot ${i} · ${k.t}s` : `Keyframe ${i} · ${k.t}s`}</b><button class="small ghost undo" data-i="${i}" ${info.previous.includes(i) ? "" : "hidden"} title="Swap back to the version before the last redraw (click again to swap forward)">Undo redraw</button></div>
      <div class="flagbox">${flagHtml(i)}</div>
      ${shotsMode ? shotFieldsHtml(k, i) : `<label class="small">What it shows<textarea data-kf="${i}" data-f="image" aria-label="keyframe ${i} description">${esc(k.image)}</textarea></label>`}
      <details class="redraw-box"><summary>Redraw this ${shotsMode ? "first frame" : "keyframe"}</summary><div class="stack">
        <label class="small">Direction for this redraw<span class="help">One-off instructions, e.g. "the dragon on the right, more fire". For a big camera change, edit the text above instead: it outweighs a direction.</span>
          <textarea data-note="${i}" rows="2" placeholder="optional for a new drawing; required to adjust"></textarea></label>
        <div class="row small">
          <label class="radio"><input type="radio" name="mode-${i}" value="new" checked> New drawing</label>
          <label class="radio"><input type="radio" name="mode-${i}" value="adjust" ${info.keyframes.includes(kfile(i)) ? "" : "disabled"}> Adjust this image</label>
        </div>
        <div class="row"><button class="small primary go" data-i="${i}">Redraw</button><span class="error small" data-err="${i}"></span></div>
      </div></details></div></div>`).join("");
  const segs = sb.segments.map((s, i) => `<div class="seg stack"><b>Segment ${i} · ${s.from}–${s.to}s</b>
      <label>Action<textarea data-seg="${i}" data-f="action">${esc(s.action)}</textarea></label>
      <label>Sound<textarea data-seg="${i}" data-f="sound">${esc(s.sound || "")}</textarea></label></div>`).join("");
  // Rendered films first (full, then draft); the animatic (stills, for timing) last and labelled
  const vidRank = f => /-full\./.test(f) ? 0 : /-draft\./.test(f) ? 1 : /-animatic\./.test(f) ? 3 : 2;
  const vidsHtml = () => info.videos.filter(f => !f.endsWith(".ogv")).sort((a, b) => vidRank(a) - vidRank(b) || a.localeCompare(b))
    .map(f => mediaHtml(`story/${name}/${f}`, f).replace(`<span>${esc(f)}</span>`, `<span>${esc(f)}${
      /-animatic\./.test(f) ? " · <b>animatic</b>: each shot's first frame held for its length (stills, to check timing)"
      : /-draft\./.test(f) ? " · <b>draft render</b> (low resolution)" : /-full\./.test(f) ? " · <b>full render</b>" : ""}</span>`)).join("");
  main.innerHTML = `<div class="stack">
    <div class="row spread"><p class="small" style="margin:0"><a href="#story">Storyboards</a> / <b>${esc(name)}</b> · <a href="#library/story/${encodeURIComponent(name)}">all files</a></p>
      ${itemButtons(`story/${name}`, name)}</div>
    <div><h2>${esc(sb.title || name)}</h2><p class="muted">${esc(sb.idea || "")}</p>
      ${shotsMode ? `<p class="small muted">${(sb.cast || []).map(c => `<b>${esc(c.name)}</b> (${esc(c.tag)}${c.voice ? `; ${esc(c.voice)}` : ""})`).join("<br>")}<br>Setting: ${esc(sb.setting)}<br>Style: ${esc(sb.style)}</p>
        <label class="small">Ambience (under the whole scene)<textarea data-top="ambience" rows="1">${esc(sb.ambience || "")}</textarea></label>`
      : `<p class="small muted">Characters: ${esc(sb.characters)}<br>Setting: ${esc(sb.setting)}<br>Style: ${esc(sb.style)}</p>`}</div>
    <div id="sb-active"></div>
    <div class="media-grid" id="sb-vids">${vidsHtml()}</div>
    ${renderFormHtml(sb)}
    <div class="row spread"><h3 style="margin:0">${shotsMode ? "Shots" : "Keyframes"}</h3><span class="row"><span class="muted small" id="sb-polled"></span><button class="small" id="redraw-all">Redraw all</button></span></div>
    <div class="kf-grid">${kfs}</div>
    ${shotsMode ? "" : `<div class="card stack"><h3>Segments</h3>${segs}</div>`}
    <div class="row"><button class="primary" id="save">Save text changes</button><span class="muted small">${shotsMode ? "Redrawing saves your text first. A shot re-renders when its first frame or text changes; a changed first-frame description needs a redraw." : "Redrawing a keyframe saves your text first. Segments re-render automatically when their text changes."}</span><span class="error" id="save-err"></span></div>
  </div>`;
  bindItemButtons(main.querySelector(".item-actions"), (p, n) => { location.hash = p ? `#story/${encodeURIComponent(n)}` : "#story"; });
  const FIELDS = "[data-kf], [data-seg], [data-top]";
  main.querySelectorAll(FIELDS).forEach(t => { t.dataset.orig = t.value; });

  const saveText = async () => {
    const next = JSON.parse(JSON.stringify(info.storyboard));
    let changed = false;
    const edited = t => { if (t.value.trim() !== t.dataset.orig.trim()) { changed = true; return true; } };
    main.querySelectorAll("[data-kf]").forEach(t => { if (edited(t)) setField(next.keyframes[+t.dataset.kf], t.dataset.f || "image", t.value.trim()); });
    main.querySelectorAll("[data-seg]").forEach(t => { if (edited(t)) next.segments[+t.dataset.seg][t.dataset.f] = t.value.trim(); });
    main.querySelectorAll("[data-top]").forEach(t => { if (edited(t)) next[t.dataset.top] = t.value.trim(); });
    if (!changed) return false;
    await api(`api/story/${encodeURIComponent(name)}`, { storyboard: next });
    main.querySelectorAll(FIELDS).forEach(t => { t.dataset.orig = t.value; });
    info.storyboard = next;
    return true;
  };
  const run = async body => {
    await saveText();
    await api("api/run/storyboard", { name, ...body });
    refresh();
  };
  main.querySelectorAll(".go").forEach(b => b.onclick = async () => {
    const i = +b.dataset.i, err = main.querySelector(`[data-err="${i}"]`);
    const note = main.querySelector(`[data-note="${i}"]`).value.trim();
    const adjust = main.querySelector(`input[name="mode-${i}"]:checked`).value === "adjust";
    err.textContent = "";
    if (adjust && !note) { err.textContent = "Say what to change."; return; }
    try {
      await run({ step: "frames", only: String(i), note, adjust });
      b.closest("details").open = false;
    } catch (e) { err.textContent = e.message; }
  });
  main.querySelectorAll(".undo").forEach(b => b.onclick = async () => {
    try { await api(`api/story/${encodeURIComponent(name)}/undo/${b.dataset.i}`, {}); refresh(); } catch (e) { alert(e.message); }
  });
  $("#redraw-all").onclick = () => confirm("Redraw every keyframe?") && run({ step: "frames" }).catch(e => alert(e.message));
  const rf = $("#render-form");
  rf.onsubmit = async e => {
    e.preventDefault(); $("#render-err").textContent = "";
    try { await run({ step: "render", ...await formValues(rf, renderFields(sb)) }); } catch (err) { $("#render-err").textContent = err.message; }
  };
  // Use another take of a shot: record the pick, then re-join (the shots are cached, so it's quick)
  main.addEventListener("click", async e => {
    const b = e.target.closest("button.use");
    if (!b) return;
    b.disabled = true;
    try {
      const draft = b.dataset.tag === "draft";
      await api(`api/story/${encodeURIComponent(name)}/pick`, { shot: +b.dataset.i, take: +b.dataset.t, draft });
      const v = await formValues(rf, renderFields(sb));
      delete v.only;
      await run({ step: "render", ...v, draft });
    } catch (err) { b.disabled = false; alert(err.message); }
  });
  $("#save").onclick = async () => {
    $("#save-err").textContent = "";
    try { await saveText(); $("#save").textContent = "Saved"; setTimeout(() => $("#save") && ($("#save").textContent = "Save text changes"), 1500); }
    catch (err) { $("#save-err").textContent = err.message; }
  };

  // Every 30 s: new keyframe images, checks, videos and job status, without touching text being edited
  const showActive = () => {
    $("#sb-active").innerHTML = info.active.length ? `<div class="notice small">${info.active.map(j =>
      `<span class="status ${j.status}">${j.status}</span> ${esc(j.title)} · <a href="#jobs/${j.id}">log</a>`).join("<br>")}
      <span class="muted"> This page updates by itself when it finishes.</span></div>` : "";
  };
  // Spinner + timer on every keyframe an active job is drawing (from the server, so it's
  // right after a reload or on another device too). The timer ticks locally every second.
  const fmtClock = sec => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;
  const drawBusy = () => {
    const now = Date.now() / 1000;
    sb.keyframes.forEach((_, i) => {
      const card = main.querySelector(`[data-card="${i}"]`);
      if (!card) return;
      const j = info.active.find(j => j.frames === "all" || (Array.isArray(j.frames) && j.frames.includes(i)));
      const busy = card.querySelector(".busy");
      busy.hidden = !j;
      if (!j) return;
      busy.classList.toggle("queued", j.status === "queued");
      busy.querySelector(".busy-text").textContent = j.status === "queued" ? "Queued…"
        : `${/adjust/.test(j.title) ? "Adjusting" : "Redrawing"}… ${j.started ? fmtClock(now - j.started) : ""}`;
    });
  };
  async function refresh() {
    let next;
    try { next = await api(`api/story/${encodeURIComponent(name)}`); } catch { return; }
    if (!$("#sb-vids")) return;   // left the page
    const old = info;
    info = next;
    next.storyboard.keyframes.forEach((k, i) => {
      const card = main.querySelector(`[data-card="${i}"]`);
      if (!card) return;
      const f = kfile(i);
      if (old.mtimes[f] !== next.mtimes[f] || old.keyframes.includes(f) !== next.keyframes.includes(f)) {
        card.querySelector(".kf-img > a, .kf-img > .thumb").outerHTML = imgHtml(i);
      }
      card.querySelector(".flagbox").innerHTML = flagHtml(i);
      card.querySelector(".undo").hidden = !next.previous.includes(i);
      card.querySelector(`input[name="mode-${i}"][value="adjust"]`).disabled = !next.keyframes.includes(f);
      card.querySelectorAll("[data-kf]").forEach(t => {   // server text changed and not being edited here
        const v = getField(k, t.dataset.f || "image");
        if (t.value === t.dataset.orig && document.activeElement !== t && v !== t.dataset.orig) { t.value = t.dataset.orig = v; }
      });
      const tk = card.querySelector(".takes");
      if (tk && JSON.stringify([old.takes?.full?.[i], old.takes?.draft?.[i], old.storyboard.takes]) !== JSON.stringify([next.takes?.full?.[i], next.takes?.draft?.[i], next.storyboard.takes])) tk.innerHTML = takesHtml(i);
    });
    const vids = next.videos.map(v => v + next.mtimes[v]).join();
    if (vids !== old.videos.map(v => v + old.mtimes[v]).join()) $("#sb-vids").innerHTML = vidsHtml();
    showActive();
    drawBusy();
    $("#sb-polled").textContent = `updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
  }
  showActive();
  drawBusy();
  listen(ev => {
    if ((ev.type === "job" && ev.outdir === `story/${name}`) || (ev.type === "story" && ev.name === name) || ev.type === "reconnect") refresh();
  });
  timers.push(setInterval(() => { if (info.active.length) drawBusy(); }, 1000));
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
  if (events) { events.close(); events = null; }
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
  connectEvents();
  if (!started) {
    started = true;
    window.addEventListener("hashchange", route);
    bus.add(ev => { if (ev.type === "job" || ev.type === "reconnect") updateBadge(); });
    connectEvents();
  }
}

(async () => {
  const me = await fetch("api/me").then(r => r.json()).catch(() => ({ ok: false }));
  me.ok ? start() : showLogin();
})();
