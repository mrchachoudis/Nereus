"use strict";

// Nereus web interface. Plain DOM, no build step, no external requests.
// All report content is inserted as text, never as HTML: titles and details
// can come from outside sources such as Crossref.

const STEPS = [
  { name: "Plan", phases: ["planning", "clarification_needed"] },
  { name: "Gather data", phases: ["retrieval"] },
  { name: "Analyse", phases: ["analysis"] },
  { name: "Draft and review", phases: ["synthesis", "critique"] },
  { name: "Assemble report", phases: ["render", "done"] },
];

const RUN_STATE_LABEL = {
  running: "Running",
  done: "Ready",
  clarification: "Needs detail",
  failed: "Failed",
};

const CITATION_KIND = {
  analysis: "Analysis",
  reference: "Paper",
  landings: "Landings",
  assessment: "Assessment",
  covariate: "Ocean data",
};

const state = {
  info: null,
  runs: [],
  currentId: null,
  token: 0,
  timer: null,
  renderedReport: null,
  announced: "",
};

const $ = (selector) => document.querySelector(selector);
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const dayFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

// ---- DOM helpers ------------------------------------------------------------

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  return append(el, children);
}

function svg(tag, attrs, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value !== null && value !== undefined) el.setAttribute(key, String(value));
  }
  return append(el, children);
}

// "B/B" + subscript MSY, used for reference-point ratios.
const msy = (letter) => [letter, h("sub", null, "MSY")];

function formatDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : dateFormat.format(d);
}

function formatDuration(fromIso, toIso) {
  const ms = new Date(toIso).getTime() - new Date(fromIso).getTime();
  if (!Number.isFinite(ms)) return "";
  const secs = Math.max(0, Math.round(ms / 1000));
  if (secs < 60) return `${secs} s`;
  return `${Math.floor(secs / 60)} min ${secs % 60} s`;
}

function fileUrl(runId, path, download) {
  const q = new URLSearchParams({ id: runId, path });
  if (download) q.set("download", "1");
  return `/file?${q}`;
}

function baseName(path) {
  return String(path).split(/[\\/]/).pop();
}

function anchorFor(marker) {
  return "cite-" + String(marker).replace(/[^A-Za-z0-9]/g, "");
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

// ---- API ------------------------------------------------------------------------

async function api(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) throw new Error((body && body.error) || `The server answered ${res.status}.`);
  return body;
}

// ---- Composer --------------------------------------------------------------------

function renderComposerOptions() {
  const info = state.info;

  const examples = info.examples || [];
  const exampleList = $("#examples .example-list");
  exampleList.replaceChildren(
    ...examples.map((text) =>
      h(
        "li",
        null,
        h(
          "button",
          {
            type: "button",
            class: "example",
            onclick: () => {
              const box = $("#question");
              box.value = text;
              box.focus();
            },
          },
          text,
        ),
      ),
    ),
  );
  $("#examples").hidden = examples.length === 0;

  const group = $("#sources");
  group.querySelector(".choices")?.remove();
  const firstAvailable = info.sources.find((s) => s.available);
  const choices = h(
    "div",
    { class: "choices" },
    info.sources.map((s) =>
      h(
        "label",
        { class: s.available ? "choice" : "choice is-unavailable" },
        h("input", {
          type: "radio",
          name: "source",
          value: s.id,
          checked: firstAvailable && s.id === firstAvailable.id,
          disabled: !s.available,
        }),
        h(
          "span",
          null,
          h(
            "span",
            { class: "choice-title" },
            s.label,
            s.needs_internet ? h("span", { class: "tag" }, "Needs internet") : null,
          ),
          h("span", { class: "choice-note" }, s.available ? s.description : s.unavailable),
        ),
      ),
    ),
  );
  group.append(choices);

  const claude = info.claude;
  const box = $("#use-claude");
  box.disabled = !claude.available;
  box.checked = claude.available;
  box.closest(".check").classList.toggle("is-unavailable", !claude.available);
  $("#claude-note").textContent = claude.available
    ? `Uses ${claude.model}. Statistics are always computed locally, never by the model.`
    : "Set ANTHROPIC_API_KEY before starting fra ui to turn this on. Without it, an offline " +
      "rule engine writes the report from the same data.";
}

function showFormError(message) {
  $("#form-error").textContent = message || "";
}

async function submitQuestion(event) {
  event.preventDefault();
  const question = $("#question").value.trim();
  if (!question) {
    showFormError("Type a research question first.");
    $("#question").focus();
    return;
  }
  const source = document.querySelector('input[name="source"]:checked');
  if (!source) {
    showFormError("Pick where the data should come from.");
    return;
  }
  const button = $("#run-button");
  button.disabled = true;
  button.textContent = "Starting…";
  try {
    const { id } = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, source: source.value, use_claude: $("#use-claude").checked }),
    });
    showFormError("");
    await refreshRuns();
    location.hash = "run=" + encodeURIComponent(id);
  } catch (err) {
    showFormError(err.message);
  } finally {
    button.disabled = false;
    button.textContent = "Generate report";
  }
}

// ---- Views and routing -------------------------------------------------------------

function stopPolling() {
  state.token += 1;
  clearTimeout(state.timer);
  state.timer = null;
}

function showComposer({ focus = false } = {}) {
  stopPolling();
  state.currentId = null;
  state.renderedReport = null;
  $("#run-view").hidden = true;
  $("#composer").hidden = false;
  document.title = "Nereus";
  renderRunList();
  if (focus) $("#question").focus();
}

function showRun(id) {
  stopPolling();
  const changed = state.currentId !== id;
  state.currentId = id;
  if (changed) {
    state.renderedReport = null;
    state.announced = "";
    $("#report").replaceChildren();
    $("#run-message").replaceChildren();
    $("#route").replaceChildren();
    $("#run-meta").replaceChildren();
    const known = state.runs.find((r) => r.id === id);
    $("#run-question").textContent = known ? known.question : "";
  }
  $("#composer").hidden = true;
  $("#run-view").hidden = false;
  renderRunList();
  loadRun(id, state.token);
}

function route() {
  const match = location.hash.match(/^#run=(.+)$/);
  if (match) {
    let id;
    try {
      id = decodeURIComponent(match[1]);
    } catch {
      id = match[1];
    }
    showRun(id);
  } else {
    showComposer();
  }
}

function goToComposer() {
  if (location.hash) {
    history.pushState(null, "", location.pathname);
  }
  showComposer({ focus: true });
}

// ---- Past reports ---------------------------------------------------------------------

async function refreshRuns() {
  try {
    const { runs } = await api("/api/runs");
    state.runs = runs;
  } catch {
    // Keep the last list; the next refresh will try again.
  }
  renderRunList();
}

function renderRunList() {
  const items = state.runs.map((r) =>
    h(
      "li",
      null,
      h(
        "a",
        {
          class: "run-link",
          href: "#run=" + encodeURIComponent(r.id),
          "aria-current": r.id === state.currentId ? "page" : null,
        },
        h("span", { class: "run-link-question" }, r.question),
        h(
          "span",
          { class: "run-link-meta" },
          h("span", { class: `run-state is-${r.status}` }, RUN_STATE_LABEL[r.status] || r.status),
          h("time", { datetime: r.started_at }, formatDate(r.started_at)),
        ),
      ),
    ),
  );
  $("#run-list").replaceChildren(...items);
  $("#run-list-empty").hidden = state.runs.length > 0;
}

// ---- A single run --------------------------------------------------------------------------

async function loadRun(id, token) {
  let run;
  try {
    run = await api(`/api/run?id=${encodeURIComponent(id)}`);
  } catch (err) {
    if (token !== state.token) return;
    renderMissingRun(err.message);
    return;
  }
  if (token !== state.token) return;

  renderRun(run);

  const listed = state.runs.find((r) => r.id === run.id);
  if (!listed || listed.status !== run.status) refreshRuns();

  if (run.status === "running") {
    state.timer = setTimeout(() => loadRun(id, token), 1000);
  }
}

function renderMissingRun(message) {
  $("#run-question").textContent = "Report not found";
  $("#run-meta").replaceChildren();
  $("#route").replaceChildren();
  $("#report").replaceChildren();
  $("#run-message").replaceChildren(
    h(
      "div",
      { class: "notice notice-danger" },
      h("p", null, message),
      h("button", { type: "button", class: "button button-quiet", onclick: goToComposer }, "New report"),
    ),
  );
}

function renderRun(run) {
  document.title = `${run.question.slice(0, 60)} – Nereus`;
  $("#run-question").textContent = run.question;

  const meta = [h("span", null, "Started ", formatDate(run.started_at))];
  if (run.source_label) meta.push(h("span", null, run.source_label));
  if (run.writer === "claude") meta.push(h("span", null, "Written by ", run.model || "Claude"));
  else if (run.writer === "offline") meta.push(h("span", null, "Written by the offline rule engine"));
  if (run.status === "running") {
    meta.push(h("span", null, "Running for ", formatDuration(run.started_at, new Date().toISOString())));
  } else if (run.finished_at) {
    meta.push(h("span", null, "Took ", formatDuration(run.started_at, run.finished_at)));
  }
  $("#run-meta").replaceChildren(...meta);

  renderRoute(run);
  renderMessage(run);

  if (run.status === "done" && run.report && state.renderedReport !== run.id) {
    renderReport(run);
    state.renderedReport = run.id;
  }
}

function lastEvent(events, name) {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    if (events[i].event === name) return events[i];
  }
  return null;
}

function stepDetails(run) {
  const events = run.events || [];
  const details = ["", "", "", "", ""];
  const plan = run.report && run.report.final_report && run.report.final_report.plan;

  if (run.status === "clarification") details[0] = "Needs more detail";
  else if (plan) {
    const taxa = (plan.taxa || []).map((t) =>
      t.common_name ? t.common_name : h("span", { class: "sci" }, t.scientific_name),
    );
    const areas = (plan.areas || []).map((a) => `FAO ${a.fao_area}`);
    const years = plan.time_range ? [`${plan.time_range.start_year}–${plan.time_range.end_year}`] : [];
    details[0] = [...taxa, ...areas, ...years].flatMap((part, i) => (i ? [", ", part] : [part]));
  } else if (lastEvent(events, "retrieval_complete")) details[0] = "Scope set";
  else details[0] = "Reading the question";

  const got = lastEvent(events, "retrieval_complete");
  if (got) {
    const parts = [
      plural(got.landings, "landings record", "landings records"),
      plural(got.assessments, "assessment year", "assessment years"),
      plural(got.covariates, "ocean series", "ocean series"),
      plural(got.references, "paper", "papers"),
    ];
    if (got.gaps) parts.push(plural(got.gaps, "gap", "gaps"));
    details[1] = parts.join(", ");
  }

  const analysed = lastEvent(events, "analysis_complete");
  if (analysed) details[2] = plural(analysed.results, "result", "results");

  const review = lastEvent(events, "critic_round");
  const capped = lastEvent(events, "revision_cap_reached");
  if (capped) details[3] = `Open notes after ${plural(capped.round + 1, "round", "rounds")}`;
  else if (review && review.verdict === "pass") details[3] = `Passed review in round ${review.round + 1}`;
  else if (review) details[3] = `Revising after round ${review.round + 1}`;

  const figures = lastEvent(events, "figures_built");
  if (run.status === "done") {
    details[4] = figures ? `${plural(figures.figures.length, "figure", "figures")}, report ready` : "Report ready";
  }
  return details;
}

function renderRoute(run) {
  const events = run.events || [];
  const phases = events.filter((e) => e.event === "phase").map((e) => e.phase);
  const lastPhase = phases[phases.length - 1];
  let current = lastPhase ? STEPS.findIndex((s) => s.phases.includes(lastPhase)) : 0;
  if (current < 0) current = 0;
  const details = stepDetails(run);

  const items = STEPS.map((step, i) => {
    let cls = "";
    if (run.status === "done") cls = "is-done";
    else if (run.status === "clarification") cls = i === 0 ? "is-stopped" : "";
    else if (i < current) cls = "is-done";
    else if (i === current) cls = run.status === "failed" ? "is-failed" : "is-current";
    return h(
      "li",
      { class: `waypoint ${cls}`.trim(), "aria-current": cls === "is-current" ? "step" : null },
      h("span", { class: "waypoint-name" }, step.name),
      details[i] && details[i].length ? h("span", { class: "waypoint-detail" }, details[i]) : null,
    );
  });
  $("#route").replaceChildren(...items);

  let spoken = `Step ${current + 1} of ${STEPS.length}: ${STEPS[current].name}`;
  if (run.status === "done") spoken = "Report ready";
  else if (run.status === "clarification") spoken = "The question needs more detail";
  else if (run.status === "failed") spoken = "The report could not be finished";
  if (spoken !== state.announced) {
    $("#route-status").textContent = spoken;
    state.announced = spoken;
  }
}

function renderMessage(run) {
  const box = $("#run-message");
  if (run.status === "clarification") {
    box.replaceChildren(
      h(
        "div",
        { class: "notice notice-caution" },
        h("h2", null, "Add a little more detail"),
        h("p", null, "Nereus needs this before it can plan the research:"),
        h("ul", null, (run.clarification || []).map((q) => h("li", null, q))),
        h(
          "button",
          { type: "button", class: "button button-quiet", onclick: () => editQuestion(run) },
          "Edit question",
        ),
      ),
    );
  } else if (run.status === "failed") {
    const hasLog = (run.events || []).length > 0;
    box.replaceChildren(
      h(
        "div",
        { class: "notice notice-danger" },
        h("h2", null, "This report wasn't finished"),
        h("p", null, run.error || "The run stopped early."),
        hasLog
          ? h("p", null, h("a", { href: fileUrl(run.id, "run_log.jsonl", true) }, "Download the run log"))
          : null,
        h(
          "button",
          { type: "button", class: "button button-quiet", onclick: () => editQuestion(run) },
          "Try again",
        ),
      ),
    );
  } else {
    box.replaceChildren();
  }
}

function editQuestion(run) {
  goToComposer();
  $("#question").value = run.question;
  if (run.source) {
    const radio = document.querySelector(`input[name="source"][value="${CSS.escape(run.source)}"]`);
    if (radio && !radio.disabled) radio.checked = true;
  }
  $("#question").focus();
}

// ---- Report ------------------------------------------------------------------------------

function renderReport(run) {
  const report = run.report;
  const final = report.final_report;
  const citations = final.citations || [];
  const byMarker = new Map(citations.map((c) => [c.marker, c]));

  const parts = [
    reportBar(run, final),
    statusCards(report, citations),
    gapsNotice(report.coverage_gaps || []),
    notesNotice(final.outstanding_critic_notes || []),
    figureGrid(run.id, final.figures || []),
    h("div", { class: "prose" }, (final.sections || []).map((s) => section(s, byMarker))),
    sourcesSection(citations, report.provenance || []),
  ];
  $("#report").replaceChildren(...parts.filter(Boolean));
}

function reportBar(run, final) {
  const plan = final.plan || {};
  const scope = [];
  (plan.taxa || []).forEach((t, i) => {
    if (i) scope.push(", ");
    scope.push(h("span", { class: "sci" }, t.scientific_name));
    if (t.common_name) scope.push(` (${t.common_name})`);
  });
  const areas = (plan.areas || []).map((a) => `FAO ${a.fao_area}`).join(", ");
  if (areas) scope.push(` in ${areas}`);
  if (plan.time_range) scope.push(`, ${plan.time_range.start_year}–${plan.time_range.end_year}`);

  return h(
    "div",
    { class: "report-bar" },
    h("p", { class: "scope" }, scope.length ? scope : "Scope not recorded"),
    h(
      "p",
      { class: "downloads" },
      "Download ",
      h("a", { href: fileUrl(run.id, "report.md", true) }, "Markdown"),
      h("a", { href: fileUrl(run.id, "report.json", true) }, "JSON audit trail"),
      h("a", { href: fileUrl(run.id, "run_log.jsonl", true) }, "Run log"),
    ),
  );
}

const STATUS_COPY = {
  healthy: {
    verdict: "Healthy",
    explain: () => ["Biomass is at or above ", msy("B"), " and fishing is at or below ", msy("F"), "."],
  },
  overfished: {
    verdict: "Overfished",
    explain: () => ["Biomass is below ", msy("B"), " while fishing is at or below ", msy("F"), "."],
  },
  overfishing: {
    verdict: "Overfishing",
    explain: () => ["Fishing is above ", msy("F"), " while biomass is at or above ", msy("B"), "."],
  },
  both: {
    verdict: "Overfished and overfishing",
    explain: () => ["Biomass is below ", msy("B"), " and fishing is above ", msy("F"), "."],
  },
};

function statusCards(report, citations) {
  const results = (report.analyses || []).filter(
    (a) => a.kind === "status_classification" && a.detail && STATUS_COPY[a.detail.status],
  );
  if (!results.length) return null;
  return h(
    "div",
    { class: "status-list" },
    results.map((a) => {
      const d = a.detail;
      const copy = STATUS_COPY[d.status];
      const citation = citations.find((c) => c.ref_id === a.id);
      const year = d.year ? Math.round(d.year) : null;
      return h(
        "section",
        { class: "status", "aria-label": `Stock status: ${copy.verdict}` },
        kobe(d.b_ratio, d.f_ratio, d.status),
        h(
          "div",
          null,
          h("p", { class: "status-target" }, year ? `Stock status in ${year}` : "Stock status", citation ? citeLink(citation) : null),
          h("p", { class: `status-verdict is-${d.status}` }, copy.verdict),
          h("p", { class: "status-explain" }, copy.explain()),
          h(
            "p",
            { class: "status-ratios" },
            h("span", null, "B/", msy("B"), " ", h("b", null, Number(d.b_ratio).toFixed(2))),
            h("span", null, "F/", msy("F"), " ", h("b", null, Number(d.f_ratio).toFixed(2))),
          ),
        ),
      );
    }),
  );
}

// Miniature Kobe plot: x is B/BMSY, y is F/FMSY. Each axis runs from 0 to at
// least 2 and stretches to keep the point inside; quadrants split at 1.
function kobe(b, f, status) {
  const x0 = 20, y0 = 4, size = 104;
  const span = (v) => Math.min(4, Math.max(2, Math.ceil(Number(v) * 1.15 * 2) / 2));
  const maxX = span(b), maxY = span(f);
  const px = (v) => x0 + (Math.min(Math.max(v, 0), maxX) / maxX) * size;
  const py = (v) => y0 + size - (Math.min(Math.max(v, 0), maxY) / maxY) * size;
  const midX = px(1), midY = py(1), right = x0 + size, bottom = y0 + size;
  const quad = (cls, x1, y1, x2, y2, on) =>
    svg("rect", { class: `q ${cls}${on ? " is-on" : ""}`, x: x1, y: y1, width: x2 - x1, height: y2 - y1 });
  const label = `Kobe plot: B/BMSY ${Number(b).toFixed(2)}, F/FMSY ${Number(f).toFixed(2)}`;
  return svg(
    "svg",
    { class: "kobe", viewBox: "0 0 132 132", role: "img", "aria-label": label },
    quad("q-both", x0, y0, midX, midY, status === "both"),
    quad("q-overfishing", midX, y0, right, midY, status === "overfishing"),
    quad("q-overfished", x0, midY, midX, bottom, status === "overfished"),
    quad("q-healthy", midX, midY, right, bottom, status === "healthy"),
    svg("line", { class: "ref", x1: midX, y1: y0, x2: midX, y2: bottom }),
    svg("line", { class: "ref", x1: x0, y1: midY, x2: right, y2: midY }),
    svg("line", { class: "axis", x1: x0, y1: y0 + size, x2: x0 + size, y2: y0 + size }),
    svg("line", { class: "axis", x1: x0, y1: y0, x2: x0, y2: y0 + size }),
    svg("circle", { class: "point", cx: px(b), cy: py(f), r: 5 }),
    svg("text", { x: x0 + size / 2, y: 128, "text-anchor": "middle" }, "B/Bmsy"),
    svg("text", { x: 9, y: y0 + size / 2, "text-anchor": "middle", transform: `rotate(-90 9 ${y0 + size / 2})` }, "F/Fmsy"),
  );
}

function gapsNotice(gaps) {
  if (!gaps.length) return null;
  return h(
    "section",
    { class: "notice notice-caution", "aria-labelledby": "gaps-title" },
    h("h2", { id: "gaps-title" }, "Missing data"),
    h("p", null, "These sources returned nothing for this question. The report lists them as gaps instead of guessing."),
    h(
      "ul",
      null,
      gaps.map((g) => h("li", null, h("b", null, g.domain), ": ", g.detail, g.connector ? ` (${g.connector})` : "")),
    ),
  );
}

function notesNotice(notes) {
  if (!notes.length) return null;
  return h(
    "section",
    { class: "notice", "aria-labelledby": "notes-title" },
    h("h2", { id: "notes-title" }, "Open review notes"),
    h("p", null, "The reviewer still had these notes when the revision limit was reached:"),
    h(
      "ul",
      null,
      notes.map((n) => h("li", null, h("b", null, n.severity), n.section_title ? ` in ${n.section_title}` : "", ": ", n.message)),
    ),
  );
}

function figureGrid(runId, figures) {
  if (!figures.length) return null;
  return h(
    "div",
    { class: "figures" },
    figures.map((fig) => {
      const src = fileUrl(runId, `figures/${baseName(fig.path)}`);
      return h(
        "button",
        { type: "button", class: "figure-button", onclick: () => openFigure(src, fig) },
        h("img", { src, alt: `${fig.title}. ${fig.caption}`, loading: "lazy" }),
        h("span", { class: "figure-label" }, fig.title),
      );
    }),
  );
}

function openFigure(src, fig) {
  const dialog = $("#figure-dialog");
  $("#figure-dialog-title").textContent = fig.title;
  const img = $("#figure-dialog-img");
  img.src = src;
  img.alt = fig.title;
  $("#figure-dialog-caption").textContent = fig.caption;
  dialog.showModal();
}

function citeLink(citation) {
  const label = String(citation.marker).replace(/^\[|\]$/g, "");
  return h(
    "a",
    {
      class: "cite",
      href: "#" + anchorFor(citation.marker),
      title: citation.detail || citation.ref_id,
      onclick: (event) => {
        event.preventDefault();
        const row = document.getElementById(anchorFor(citation.marker));
        if (!row) return;
        document.querySelectorAll("tr.is-target").forEach((r) => r.classList.remove("is-target"));
        row.classList.add("is-target");
        row.scrollIntoView({ behavior: reduceMotion.matches ? "auto" : "smooth", block: "center" });
        row.focus({ preventScroll: true });
      },
    },
    label,
  );
}

function section(sec, byMarker) {
  return [
    h("h2", null, sec.title),
    (sec.claims || []).map((claim) =>
      h(
        "p",
        null,
        claim.text,
        (claim.citation_markers || []).map((m) => {
          const c = byMarker.get(m);
          return c ? citeLink(c) : h("span", { class: "cite" }, m);
        }),
      ),
    ),
  ];
}

function sourcesSection(citations, provenance) {
  const parts = [h("h2", null, "Sources")];
  if (citations.length) {
    parts.push(
      h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          null,
          h("thead", null, h("tr", null, h("th", { scope: "col" }, "Ref"), h("th", { scope: "col" }, "Type"), h("th", { scope: "col" }, "What it is"), h("th", { scope: "col" }, "Record id"))),
          h(
            "tbody",
            null,
            citations.map((c) =>
              h(
                "tr",
                { id: anchorFor(c.marker), tabindex: "-1" },
                h("td", null, String(c.marker).replace(/^\[|\]$/g, "")),
                h("td", null, CITATION_KIND[c.kind] || c.kind),
                h("td", null, c.detail || ""),
                h("td", null, h("span", { class: "id" }, c.ref_id)),
              ),
            ),
          ),
        ),
      ),
    );
  }
  if (provenance.length) {
    parts.push(h("h3", null, "Where the data came from"));
    parts.push(
      h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          null,
          h("thead", null, h("tr", null, h("th", { scope: "col" }, "Source"), h("th", { scope: "col" }, "Query"), h("th", { scope: "col" }, "Retrieved"), h("th", { scope: "col", class: "num" }, "Records"))),
          h(
            "tbody",
            null,
            provenance.map((p) => {
              const query = /^https?:\/\//i.test(p.url_or_query)
                ? h("a", { href: p.url_or_query, target: "_blank", rel: "noopener noreferrer" }, p.url_or_query)
                : p.url_or_query;
              const day = new Date(p.retrieved_at);
              return h(
                "tr",
                null,
                h("td", null, p.source),
                h("td", null, h("span", { class: "id" }, query)),
                h("td", null, Number.isNaN(day.getTime()) ? "" : dayFormat.format(day)),
                h("td", { class: "num" }, (p.record_ids || []).length),
              );
            }),
          ),
        ),
      ),
    );
  }
  return h("section", { class: "sources-section" }, parts);
}

// ---- Start-up -----------------------------------------------------------------------------

async function init() {
  $("#ask-form").addEventListener("submit", submitQuestion);
  $("#question").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      $("#ask-form").requestSubmit();
    }
  });
  $("#new-report").addEventListener("click", goToComposer);
  $("#brand-link").addEventListener("click", (event) => {
    event.preventDefault();
    goToComposer();
  });
  $("#figure-dialog").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) event.currentTarget.close();
  });
  window.addEventListener("hashchange", route);
  window.addEventListener("popstate", route);

  try {
    state.info = await api("/api/info");
    renderComposerOptions();
  } catch (err) {
    showFormError(`Couldn't reach the Nereus server: ${err.message}`);
  }
  await refreshRuns();
  route();
}

init();
