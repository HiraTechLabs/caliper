import { animate } from "motion";

const reduceQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
const reduced = () => reduceQuery.matches;
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const ICON_PATHS = {
  list: '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
  code: '<path d="m18 16 4-4-4-4"/><path d="m6 8-4 4 4 4"/><path d="m14.5 4-5 16"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/><path d="m8 11 2 2 4-4"/>',
  hand: '<path d="M18 11V6a2 2 0 0 0-4 0v5"/><path d="M14 10V4a2 2 0 0 0-4 0v6"/><path d="M10 10.5V6a2 2 0 0 0-4 0v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>',
  check: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>',
  x: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
};

function svg(name, extraClass = "") {
  const paths = ICON_PATHS[name] || ICON_PATHS.list;
  return `<svg class="${extraClass}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
}

const STATUS = {
  submitted: { label: "submitted", tone: "neutral", icon: "list", rank: 0 },
  planning: { label: "Planning", tone: "planning", icon: "list", rank: 1 },
  executing: { label: "Executing", tone: "executing", icon: "code", rank: 2 },
  reviewing: { label: "Reviewing", tone: "reviewing", icon: "search", rank: 3 },
  awaiting_approval: { label: "Awaiting approval", tone: "awaiting", icon: "hand", rank: 4 },
  merged: { label: "Merged", tone: "merged", icon: "check", rank: 5 },
  deployed_dev: { label: "Merged", tone: "merged", icon: "check", rank: 5 },
  live: { label: "Live", tone: "merged", icon: "check", rank: 6 },
  failed: { label: "Failed", tone: "failed", icon: "x", rank: 3 },
  cancelled: { label: "Cancelled", tone: "neutral", icon: "x", rank: 0 },
};

const TONES = ["neutral", "planning", "executing", "reviewing", "awaiting", "merged", "failed", "pending"];

function meta(status) {
  return STATUS[status] || { label: status, tone: "neutral", icon: "list", rank: 0 };
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function setBadge(el, status, animateChange = false) {
  if (!el || el.dataset.status === status) return;
  const next = meta(status);
  const inner = el.querySelector(".status-badge-inner") || el;
  const apply = () => {
    el.classList.remove(...TONES.map((tone) => `tone-${tone}`));
    el.classList.add(`tone-${next.tone}`);
    el.dataset.status = status;
    inner.innerHTML = `<span class="status-icon">${svg(next.icon)}</span><span class="label">${esc(next.label)}</span>`;
  };
  if (animateChange && !reduced() && inner.isConnected) {
    animate(inner, { opacity: [1, 0] }, { duration: 0.075, ease: "easeOut" }).finished.then(() => {
      if (!inner.isConnected) return;
      apply();
      animate(inner, { opacity: [0, 1] }, { duration: 0.075, ease: "easeIn" });
    });
  } else {
    apply();
  }
}

function updateBadges(requests, animateChange) {
  const byId = new Map(requests.map((req) => [req.id, req]));
  document.querySelectorAll("[data-status-badge][data-req-id]").forEach((badge) => {
    const next = byId.get(badge.dataset.reqId);
    if (next) setBadge(badge, next.status, animateChange);
  });
}

function setStepper(status) {
  const stepper = document.querySelector("[data-stepper]");
  if (!stepper) return;
  stepper.dataset.status = status;
  const activeMap = {
    submitted: -1, planning: 0, executing: 1, reviewing: 2,
    awaiting_approval: 3, merged: 4, deployed_dev: 4, live: 5, failed: 2,
  };
  const active = activeMap[status] ?? -1;
  stepper.querySelectorAll(".step").forEach((step, index) => {
    step.classList.toggle("active", index === active);
    step.classList.toggle("failed", status === "failed" && index === 2);
  });
}

function stationStates(status) {
  if (status === "failed") return ["done", "done", "failed", "pending"];
  if (["merged", "deployed_dev", "live"].includes(status)) return ["done", "done", "done", "done"];
  const activeMap = { planning: 0, executing: 1, reviewing: 2, awaiting_approval: 3 };
  const active = activeMap[status];
  if (active === undefined) return ["pending", "pending", "pending", "pending"];
  return [0, 1, 2, 3].map((index) => {
    if (index < active) return "done";
    if (index === active) return "active";
    return "pending";
  });
}

function stationTone(state, index, status) {
  if (state === "done") return "merged";
  if (state === "failed") return "failed";
  if (state === "active") {
    if (index === 3) return "awaiting";
    return meta(status).tone;
  }
  return "pending";
}

function stationResults(status) {
  const states = stationStates(status);
  const results = ["", "", "", ""];
  if (states[0] === "done") results[0] = "Plan ready";
  if (states[0] === "active") results[0] = "Planning now";
  if (status === "submitted") results[0] = "Queued";
  if (states[1] === "done") results[1] = "Diff ready";
  if (states[1] === "active") results[1] = "Executing now";
  if (states[2] === "done") results[2] = "Verdict ready";
  if (states[2] === "active") results[2] = "Reviewing now";
  if (states[2] === "failed") results[2] = "Rules failed";
  if (states[3] === "active") results[3] = "Needs you";
  if (states[3] === "done") results[3] = status === "live" ? "Live" : "Merged";
  return results;
}

function stationProgress(state) {
  if (state === "done" || state === "failed") return 1;
  if (state === "active") return 0.55;
  return 0;
}

function floorMarkup(status, compact = false) {
  const stations = [
    ["planning", "Planning", "list"],
    ["executing", "Executing", "code"],
    ["reviewing", "Reviewing", "search"],
    ["approval", "Approval", "hand"],
  ];
  const states = stationStates(status);
  const results = stationResults(status);
  const chips = {
    plan: meta(status).rank >= 2,
    diff: meta(status).rank >= 3,
    verdict: meta(status).rank >= 4 || status === "failed",
  };
  return `<div class="factory-floor${compact ? " compact" : ""}" data-factory-floor data-status="${esc(status)}" aria-label="Workflow factory floor">
    <div class="factory-stations">
      ${stations.map(([key, label, icon], index) => {
        const tone = stationTone(states[index], index, status);
        return `<div class="factory-station is-${states[index]} tone-${tone}" data-station="${key}" data-tone="${tone}">
          <span class="station-ring"></span>
          <span class="station-icon">${svg(icon)}</span>
          <span class="station-name">${label}</span>
          <span class="station-result" data-station-result>${esc(results[index])}</span>
          <span class="station-progress" aria-hidden="true"><span class="station-progress-bar" style="transform: scaleX(${stationProgress(states[index])})"></span></span>
        </div>`;
      }).join("")}
    </div>
    <div class="factory-lane" data-factory-lane>
      <span class="lane-line"></span>
      <span class="lane-artifacts">
        <span class="artifact-chip plan${chips.plan ? "" : " hidden"}" data-artifact-chip="plan">plan</span>
        <span class="artifact-chip diff${chips.diff ? "" : " hidden"}" data-artifact-chip="diff">diff</span>
        <span class="artifact-chip verdict${chips.verdict ? "" : " hidden"}" data-artifact-chip="verdict">verdict</span>
      </span>
    </div>
  </div>`;
}

function travelChip(floor, type, from, to, failed = false) {
  if (reduced()) return;
  const lane = floor.querySelector("[data-factory-lane]");
  const stations = floor.querySelectorAll(".factory-station");
  if (!lane || !stations[from] || !stations[to]) return;
  const laneRect = lane.getBoundingClientRect();
  if (!laneRect.width) return;
  const fromRect = stations[from].getBoundingClientRect();
  const toRect = stations[to].getBoundingClientRect();
  const chip = document.createElement("span");
  chip.className = `artifact-chip traveling ${type}${failed ? " is-fail" : ""}`;
  chip.textContent = type;
  lane.appendChild(chip);
  const half = chip.offsetWidth / 2;
  const start = fromRect.left + fromRect.width / 2 - laneRect.left - half;
  const end = toRect.left + toRect.width / 2 - laneRect.left - half;
  animate(chip, { x: [start, end], opacity: [0, 1, 1, 0] }, { duration: 0.75, ease: "easeInOut" })
    .finished.then(() => chip.remove());
}

function applyFloor(floor, req, animateChange = false) {
  if (!floor || !req) return;
  const previous = floor.dataset.status;
  const states = stationStates(req.status);
  const results = stationResults(req.status);
  floor.querySelectorAll(".factory-station").forEach((station, index) => {
    const state = states[index];
    const tone = stationTone(state, index, req.status);
    station.classList.remove("is-active", "is-done", "is-failed", "is-pending");
    station.classList.add(`is-${state}`);
    station.dataset.tone = tone;
    station.classList.remove(...TONES.map((item) => `tone-${item}`));
    station.classList.add(`tone-${tone}`);
    const result = station.querySelector("[data-station-result]");
    if (result) result.textContent = results[index];
    const bar = station.querySelector(".station-progress-bar");
    if (bar) {
      const target = stationProgress(state);
      const current = Number(bar.dataset.progress ?? target);
      bar.dataset.progress = String(target);
      if (animateChange && !reduced() && current !== target) {
        animate(bar, { scaleX: [current, target] }, { duration: 0.25, ease: "easeOut" });
      } else {
        bar.style.transform = `scaleX(${target})`;
      }
    }
  });
  const chips = {
    plan: !!req.has_plan || meta(req.status).rank >= 2,
    diff: !!req.has_diff || meta(req.status).rank >= 3,
    verdict: !!req.review || meta(req.status).rank >= 4 || req.status === "failed",
  };
  floor.querySelectorAll("[data-artifact-chip]").forEach((chip) => {
    chip.classList.toggle("hidden", !chips[chip.dataset.artifactChip]);
  });
  if (animateChange && previous && previous !== req.status) {
    if (previous === "planning" && req.status === "executing") travelChip(floor, "plan", 0, 1);
    if (previous === "executing" && req.status === "reviewing") travelChip(floor, "diff", 1, 2);
    if (previous === "reviewing" && req.status === "awaiting_approval") travelChip(floor, "verdict", 2, 3);
    if (previous === "reviewing" && req.status === "executing") travelChip(floor, "verdict", 2, 1, true);
  }
  floor.dataset.status = req.status;
}

function sectionFor(status) {
  if (status === "awaiting_approval") return "needs";
  if (["submitted", "planning", "executing", "reviewing", "merged", "deployed_dev"].includes(status)) return "running";
  return "recent";
}

function createRow(req) {
  const row = document.createElement("li");
  row.className = "card request-card request-row";
  row.dataset.reqId = req.id;
  row.dataset.status = req.status;
  row.dataset.reqText = req.text;
  row.dataset.projectId = req.project_id;
  row.dataset.createdAt = req.created_at;
  row.innerHTML = `
    <div class="request-row-main">
      <a class="request-link" href="/requests/${esc(req.id)}">
        <span class="status-badge tone-neutral" data-status-badge data-status="submitted" data-req-id="${esc(req.id)}">
          <span class="status-badge-inner"><span class="status-icon">${svg("list")}</span><span class="label">submitted</span></span>
        </span>
        <span class="request-text" data-row-text></span>
        <span class="mono muted" data-row-meta></span>
      </a>
      <button class="btn ghost floor-toggle" type="button" data-floor-toggle aria-expanded="false">View floor</button>
    </div>
    <div class="factory-slot" data-factory-slot hidden>${floorMarkup(req.status, true)}</div>`;
  return row;
}

function updateRow(row, req, animateChange) {
  const oldStatus = row.dataset.status;
  row.dataset.status = req.status;
  row.dataset.reqText = req.text;
  row.dataset.projectId = req.project_id;
  row.dataset.createdAt = req.created_at;
  const text = row.querySelector("[data-row-text]");
  if (text) text.textContent = String(req.text || "").slice(0, 90);
  const metaEl = row.querySelector("[data-row-meta]");
  if (metaEl) {
    metaEl.textContent = sectionFor(req.status) === "recent"
      ? req.created_at
      : `${req.project_id} · ${req.id}`;
  }
  const badge = row.querySelector("[data-status-badge]");
  if (badge) setBadge(badge, req.status, animateChange && oldStatus !== req.status);
  const floor = row.querySelector("[data-factory-floor]");
  if (floor) applyFloor(floor, req, animateChange && oldStatus !== req.status);
}

function collapseRemove(row, animateChange) {
  if (!animateChange || reduced()) {
    row.remove();
    return;
  }
  animate(row, { opacity: [1, 0], scaleY: [1, 0] }, { duration: 0.2, ease: "easeIn" })
    .finished.then(() => row.remove());
}

function updateEmptyStates() {
  ["needs", "running", "recent"].forEach((section) => {
    const list = document.querySelector(`[data-request-list="${section}"]`);
    const empty = document.querySelector(`[data-section-empty="${section}"]`);
    if (!list) return;
    const hasRows = list.querySelector(".request-row") !== null;
    list.hidden = !hasRows;
    if (empty) empty.hidden = hasRows;
  });
}

function applyDashboard(payload, animateChange) {
  const needsList = document.querySelector('[data-request-list="needs"]');
  if (!needsList) return;
  const requests = payload.requests || [];
  const ids = new Set(requests.map((req) => req.id));
  const rows = new Map();
  document.querySelectorAll("[data-request-list] .request-row").forEach((row) => rows.set(row.dataset.reqId, row));

  const oldRects = new Map();
  if (animateChange && !reduced()) {
    rows.forEach((row, id) => oldRects.set(id, row.getBoundingClientRect()));
  }

  rows.forEach((row, id) => {
    if (!ids.has(id)) {
      collapseRemove(row, animateChange);
      rows.delete(id);
    }
  });

  requests.forEach((req) => {
    const targetList = document.querySelector(`[data-request-list="${sectionFor(req.status)}"]`);
    if (!targetList) return;
    let row = rows.get(req.id);
    const isNew = !row;
    if (!row) row = createRow(req);
    const moved = row.parentElement !== targetList;
    if (moved) targetList.appendChild(row);
    updateRow(row, req, animateChange && !isNew);
    rows.set(req.id, row);
  });

  if (animateChange && !reduced()) {
    rows.forEach((row, id) => {
      if (!row.isConnected) return;
      const before = oldRects.get(id);
      const after = row.getBoundingClientRect();
      if (!before) {
        animate(row, { opacity: [0, 1], y: [4, 0] }, { duration: 0.2, ease: "easeOut" });
        return;
      }
      const dx = before.left - after.left;
      const dy = before.top - after.top;
      if (dx || dy) {
        animate(row, { x: [dx, 0], y: [dy, 0] }, { type: "spring", stiffness: 400, damping: 35 });
      }
    });
  }

  updateEmptyStates();
}

function skeletonMarkup() {
  return `<div class="review-skeleton" data-review-skeleton aria-label="Review in progress">
    <div class="skeleton skeleton-line"></div>
    <div class="skeleton skeleton-line"></div>
    <div class="skeleton skeleton-line"></div>
  </div>`;
}

function ruleRowMarkup(ruleId, verdict, reason, state) {
  const pass = verdict === "pass";
  const iconName = pass ? "check" : "x";
  const tone = pass ? "merged" : "failed";
  const stateMarkup = state === "resolved"
    ? `<span class="rule-state-icon tone-${tone}">${svg(iconName, pass ? "rule-check" : "")}</span>`
    : `<span class="rule-pending-dot"></span>`;
  return `<li class="card rule-row ${verdict}" data-rule-row data-rule-id="${esc(ruleId)}" data-verdict="${verdict}">
    <span class="rule-state" data-rule-state>${stateMarkup}</span>
    <span class="mono rule-id">${pass ? "PASS" : "FAIL"} ${esc(ruleId)}</span><span>${esc(reason)}</span>
  </li>`;
}

function reviewSummary(review) {
  const entries = Object.entries(review || {});
  const failed = entries.filter(([, verdict]) => verdict.status === "fail").length;
  const passed = entries.length - failed;
  return failed ? `${failed} failed · ${passed} passed` : `All ${passed} rules passed`;
}

function renderReviewStatic(review) {
  const content = document.querySelector("[data-review-content]");
  if (!content) return;
  const entries = Object.entries(review || {});
  const failed = entries.filter(([, verdict]) => verdict.status === "fail");
  const passed = entries.filter(([, verdict]) => verdict.status === "pass");
  content.innerHTML = `<p class="muted" data-review-summary>${reviewSummary(review)}</p>
    <ul class="rule-list" data-rule-list>
      ${failed.map(([id, verdict]) => ruleRowMarkup(id, "fail", verdict.reason, "resolved")).join("")}
      ${passed.length ? `<li data-passed-item><details data-passed-group><summary class="muted">${passed.length} passed - show</summary><ul class="rule-list">${passed.map(([id, verdict]) => ruleRowMarkup(id, "pass", verdict.reason, "resolved")).join("")}</ul></details></li>` : ""}
    </ul>`;
}

function moveFailedToTop(list, animateChange) {
  const rows = Array.from(list.querySelectorAll("[data-rule-row]"));
  if (!rows.length) return;
  const old = new Map();
  if (animateChange && !reduced()) rows.forEach((row) => old.set(row, row.getBoundingClientRect()));
  const failed = rows.filter((row) => row.dataset.verdict === "fail");
  const passed = rows.filter((row) => row.dataset.verdict !== "fail");
  failed.forEach((row) => list.appendChild(row));
  passed.forEach((row) => list.appendChild(row));
  if (!animateChange || reduced()) return;
  rows.forEach((row) => {
    const before = old.get(row);
    const after = row.getBoundingClientRect();
    if (!before) return;
    const dx = before.left - after.left;
    const dy = before.top - after.top;
    if (dx || dy) {
      animate(row, { x: [dx, 0], y: [dy, 0] }, { type: "spring", stiffness: 400, damping: 35 });
    }
  });
}

function renderReviewLive(review) {
  const content = document.querySelector("[data-review-content]");
  if (!content) return;
  const entries = Object.entries(review || {});
  content.innerHTML = `<p class="muted" data-review-summary>${reviewSummary(review)}</p>
    <ul class="rule-list" data-rule-list>${entries.map(([id, verdict]) => ruleRowMarkup(id, verdict.status, verdict.reason, "pending")).join("")}</ul>`;
  const rows = Array.from(content.querySelectorAll("[data-rule-row]"));
  if (reduced()) {
    rows.forEach((row) => {
      const pass = row.dataset.verdict === "pass";
      row.querySelector("[data-rule-state]").innerHTML = `<span class="rule-state-icon tone-${pass ? "merged" : "failed"}">${svg(pass ? "check" : "x")}</span>`;
    });
    moveFailedToTop(content.querySelector("[data-rule-list]"), false);
    return;
  }
  (async () => {
    for (const row of rows) {
      if (!row.isConnected) return;
      const state = row.querySelector("[data-rule-state]");
      state.innerHTML = '<span class="rule-spinner" aria-hidden="true"></span>';
      await wait(40);
      const pass = row.dataset.verdict === "pass";
      state.innerHTML = `<span class="rule-state-icon tone-${pass ? "merged" : "failed"}">${svg(pass ? "check" : "x", pass ? "rule-check" : "")}</span>`;
      if (!pass) animate(row, { x: [0, -4, 4, 0] }, { duration: 0.12, ease: "easeInOut" });
    }
    moveFailedToTop(content.querySelector("[data-rule-list]"), true);
  })().catch(() => {});
}

function updateReview(req, animateChange) {
  const section = document.querySelector("[data-review-section]");
  const content = document.querySelector("[data-review-content]");
  if (!section || !content) return;
  const visible = Boolean(req.review) || req.status === "reviewing";
  section.hidden = !visible;
  if (!visible) return;
  if (req.status === "reviewing") {
    content.dataset.signature = "";
    if (!content.querySelector("[data-review-skeleton]")) content.innerHTML = skeletonMarkup();
    return;
  }
  if (req.review) {
    const failCount = Object.values(req.review).filter((verdict) => verdict.status === "fail").length;
    const signature = `${Object.keys(req.review).join(" ")}|${failCount}`;
    if (content.dataset.signature === signature && content.querySelector("[data-rule-row]")) return;
    content.dataset.signature = signature;
    if (animateChange) renderReviewLive(req.review);
    else renderReviewStatic(req.review);
    return;
  }
  content.dataset.signature = "";
  if (!content.querySelector("[data-review-skeleton]")) content.innerHTML = skeletonMarkup();
}

function updateArtifact(kind, content, hasArtifact, animateChange) {
  const section = document.querySelector(`[data-${kind}-section]`);
  const target = document.querySelector(`[data-${kind}-content]`);
  if (!section || !target) return;
  if (!hasArtifact || content == null) {
    section.hidden = true;
    return;
  }
  const wasHidden = section.hidden;
  target.textContent = content;
  section.hidden = false;
  if (wasHidden && animateChange && !reduced()) {
    animate(section, { opacity: [0, 1], y: [4, 0] }, { duration: 0.2, ease: "easeOut" });
  }
}

function actionsMarkup(req) {
  const id = esc(req.id);
  if (req.status === "submitted") {
    if (document.body.dataset.modelsConfigured === "0") {
      return `<div class="setup-note bad" role="status"><strong>Models are not configured.</strong> <a href="/settings/models">Configure models</a></div>`;
    }
    return `<form method="post" action="/requests/${id}/run"><button class="btn primary" type="submit">Run workflow</button></form>`;
  }
  if (req.status === "awaiting_approval") {
    return `<form method="post" action="/requests/${id}/approve"><button class="btn primary" type="submit">Approve &amp; merge</button></form>
      <form method="post" action="/requests/${id}/changes" class="inline-form">
        <div class="field"><input type="text" name="note" placeholder="What should change?"></div>
        <button class="btn secondary" type="submit">Request changes</button>
      </form>`;
  }
  if (req.status === "deployed_dev") {
    return `<details class="confirm-dialog">
      <summary class="btn danger">Deploy to Prod</summary>
      <div class="card confirm-body">
        <p>This runs <span class="mono">prod</span> deploy for request <span class="mono">${id}</span>. This is the final production gate.</p>
        <form method="post" action="/requests/${id}/prod"><button class="btn danger" type="submit">Yes, deploy to Prod</button>
        <a class="btn ghost" href="/requests/${id}">Cancel</a></form>
      </div>
    </details>`;
  }
  if (req.status === "failed") return `<span class="muted">This request is stopped. Ask the human to decide (edit plan by hand or reject).</span>`;
  if (req.status === "live") return `<span class="muted">Merged and deployed to Prod. Nothing left to do.</span>`;
  return "";
}

function renderActions(req, animateChange) {
  const area = document.querySelector("[data-action-area]");
  if (!area) return;
  const signature = `${req.id}|${req.status}`;
  if (area.dataset.signature === signature) return;
  area.dataset.signature = signature;
  area.innerHTML = actionsMarkup(req);
  if (animateChange && !reduced()) animate(area, { opacity: [0, 1], y: [4, 0] }, { duration: 0.2, ease: "easeOut" });
}

function applyRequest(payload, animateChange) {
  const root = document.querySelector("[data-request-root]");
  if (!root) return;
  const req = (payload.requests || []).find((item) => item.id === root.dataset.requestId);
  if (!req) return;
  const oldStatus = root.dataset.status;
  root.dataset.status = req.status;
  document.querySelectorAll("[data-status-badge][data-req-id]").forEach((badge) => {
    if (badge.dataset.reqId === req.id) setBadge(badge, req.status, animateChange && oldStatus !== req.status);
  });
  setStepper(req.status);
  applyFloor(document.querySelector("[data-factory-floor]"), req, animateChange && oldStatus !== req.status);
  const failedAlert = document.querySelector("[data-failed-alert]");
  if (failedAlert) {
    failedAlert.hidden = req.status !== "failed";
    if (req.status === "failed") {
      failedAlert.textContent = `Review failed ${Number(req.loop_count || 0) + 1} times. The workflow stopped and needs your call: edit the plan by hand or reject the request.`;
    }
  }
  const note = document.querySelector("[data-review-note]");
  if (note) {
    note.hidden = !req.review_note;
    const noteText = note.querySelector("[data-review-note-text]");
    if (noteText) noteText.textContent = req.review_note || "";
  }
  updateReview(req, animateChange && oldStatus !== req.status);
  updateArtifact("plan", req.plan, req.has_plan, animateChange && oldStatus !== req.status);
  updateArtifact("diff", req.diff, req.has_diff, animateChange && oldStatus !== req.status);
  if (!window.__caliperSubmitting) renderActions(req, animateChange && oldStatus !== req.status);
}

function handlePayload(payload, animateChange) {
  window.__caliperPayload = payload;
  const requests = payload.requests || [];
  const map = window.__caliperRequests || new Map();
  requests.forEach((req) => map.set(req.id, req));
  window.__caliperRequests = map;
  if (document.querySelector('[data-request-list="needs"]')) {
    applyDashboard(payload, animateChange);
  } else if (document.querySelector("[data-request-root]")) {
    applyRequest(payload, animateChange);
  }
  updateBadges(requests, animateChange);
}

function connectSSE() {
  if (typeof EventSource === "undefined") return;
  const root = document.querySelector("[data-request-root]");
  const requestId = document.body.dataset.page === "request" && root ? root.dataset.requestId : null;
  const url = requestId ? `/api/events?request=${encodeURIComponent(requestId)}` : "/api/events";
  if (window.__caliperSSE && window.__caliperSSEUrl === url) return;
  if (window.__caliperSSE) window.__caliperSSE.close();
  const source = new EventSource(url);
  window.__caliperSSE = source;
  window.__caliperSSEUrl = url;
  let first = true;
  const accept = (event, canAnimate) => {
    try {
      handlePayload(JSON.parse(event.data), canAnimate && !first);
    } catch (error) {
      return;
    }
    first = false;
  };
  source.addEventListener("snapshot", (event) => accept(event, false));
  source.addEventListener("update", (event) => accept(event, true));
}

function setButtonState(button, state) {
  if (!button) return;
  if (state === "loading") {
    if (!button.dataset.label) button.dataset.label = button.textContent.trim();
    button.dataset.state = "loading";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.innerHTML = '<span class="btn-spinner" aria-hidden="true"></span>';
    return;
  }
  if (state === "done") {
    button.dataset.state = "done";
    button.removeAttribute("aria-busy");
    button.innerHTML = svg("check", "btn-check");
    return;
  }
  button.dataset.state = "idle";
  button.disabled = false;
  button.removeAttribute("aria-busy");
  if (button.dataset.label) button.textContent = button.dataset.label;
}

async function submitAsync(form, event) {
  if (!(form instanceof HTMLFormElement)) return;
  if (form.hasAttribute("data-native")) return;
  if ((form.getAttribute("method") || "").toLowerCase() !== "post") return;
  if (form.dataset.submitting === "1") {
    event.preventDefault();
    return;
  }
  event.preventDefault();
  form.dataset.submitting = "1";
  window.__caliperSubmitting = true;
  let samePage = false;
  const button = form.querySelector('button[type="submit"]') || form.querySelector("button");
  setButtonState(button, "loading");
  try {
    const response = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { Accept: "text/html" },
      redirect: "follow",
    });
    if (response.ok) {
      setButtonState(button, "done");
      await wait(200);
      const target = new URL(response.url || window.location.href, window.location.href);
      samePage = target.pathname === window.location.pathname && target.search === window.location.search;
      if (!samePage) {
        window.location.assign(target.href);
        return;
      }
      if (window.__caliperSSE && window.__caliperSSE.readyState === EventSource.OPEN) return;
      window.location.reload();
      return;
    }
    setButtonState(button, "idle");
    const html = await response.text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    const nextMain = doc.querySelector("main.main");
    const currentMain = document.querySelector("main.main");
    if (nextMain && currentMain) {
      currentMain.replaceWith(document.importNode(nextMain, true));
      if (window.__caliperPayload) handlePayload(window.__caliperPayload, false);
      window.scrollTo({ top: 0, behavior: reduced() ? "auto" : "smooth" });
    } else {
      window.location.reload();
    }
  } catch (error) {
    setButtonState(button, "idle");
    HTMLFormElement.prototype.submit.call(form);
  } finally {
    form.dataset.submitting = "0";
    window.__caliperSubmitting = false;
    if (samePage) {
      const root = document.querySelector("[data-request-root]");
      const req = (window.__caliperPayload?.requests || []).find((item) => item.id === root?.dataset.requestId);
      if (req) renderActions(req, false);
    }
  }
}

function initDeleteProject() {
  const dialog = document.querySelector("[data-delete-project-dialog]");
  const openButton = document.querySelector("[data-delete-project-open]");
  if (!dialog || !openButton) return;
  const form = dialog.querySelector("[data-delete-project-form]");
  const confirmInput = dialog.querySelector("[data-delete-project-confirm]");
  const submitButton = dialog.querySelector("[data-delete-project-submit]");
  const errorBox = dialog.querySelector("[data-delete-project-error]");
  const stopDev = dialog.querySelector("[data-stop-dev]");
  const stopProd = dialog.querySelector("[data-stop-prod]");
  const projectId = dialog.dataset.projectId;
  const projectName = dialog.dataset.projectName;

  const sync = () => {
    if (submitButton.dataset.state !== "loading") {
      submitButton.disabled = confirmInput.value !== projectName;
    }
  };

  const close = () => {
    if (dialog.open) dialog.close();
  };

  openButton.addEventListener("click", () => {
    confirmInput.value = "";
    stopDev.checked = false;
    stopProd.checked = false;
    errorBox.hidden = true;
    errorBox.textContent = "";
    sync();
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    confirmInput.focus();
  });

  dialog.querySelectorAll("[data-delete-project-close]").forEach((button) => {
    button.addEventListener("click", close);
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  dialog.addEventListener("cancel", (event) => {
    if (submitButton.dataset.state === "loading") event.preventDefault();
  });
  confirmInput.addEventListener("input", sync);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (confirmInput.value !== projectName) {
      sync();
      return;
    }
    errorBox.hidden = true;
    setButtonState(submitButton, "loading");
    const params = new URLSearchParams({
      stop_dev: stopDev.checked ? "true" : "false",
      stop_prod: stopProd.checked ? "true" : "false",
    });
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}?${params.toString()}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        errorBox.textContent = payload.error || `Delete failed (${response.status}).`;
        errorBox.hidden = false;
        setButtonState(submitButton, "idle");
        sync();
        return;
      }
      setButtonState(submitButton, "done");
      try {
        sessionStorage.setItem("caliper-toast", "Project deleted. Remove the deploy key in the repository's GitHub settings.");
      } catch (error) {
        // The dashboard still opens if storage is unavailable.
      }
      await wait(150);
      window.location.assign("/");
    } catch (error) {
      errorBox.textContent = `Delete failed: ${error.message}`;
      errorBox.hidden = false;
      setButtonState(submitButton, "idle");
      sync();
    }
  });
}

function initToast() {
  const toast = document.querySelector("[data-toast]");
  if (!toast) return;
  let message = "";
  try {
    message = sessionStorage.getItem("caliper-toast") || "";
    if (message) sessionStorage.removeItem("caliper-toast");
  } catch (error) {
    return;
  }
  if (!message) return;
  toast.textContent = message;
  toast.hidden = false;
  setTimeout(() => { toast.hidden = true; }, 7000);
}

function initTheme() {
  const stored = localStorage.getItem("caliper-theme");
  if (stored === "dark") document.documentElement.classList.add("dark");
  else if (stored === "light") document.documentElement.classList.remove("dark");
  else if (window.matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.classList.add("dark");
  const toggle = document.querySelector(".theme-toggle");
  if (!toggle) return;
  toggle.addEventListener("click", () => {
    const dark = document.documentElement.classList.toggle("dark");
    localStorage.setItem("caliper-theme", dark ? "dark" : "light");
  });
}

function init() {
  initTheme();
  initToast();
  initDeleteProject();
  connectSSE();
}

document.addEventListener("submit", (event) => submitAsync(event.target, event));
document.addEventListener("click", (event) => {
  const toggle = event.target.closest("[data-floor-toggle]");
  if (!toggle) return;
  const row = toggle.closest(".request-row");
  if (!row) return;
  const slot = row.querySelector("[data-factory-slot]");
  if (!slot) return;
  const opening = slot.hidden;
  slot.hidden = !opening;
  toggle.setAttribute("aria-expanded", String(opening));
  toggle.textContent = opening ? "Hide floor" : "View floor";
  if (opening) {
    const req = (window.__caliperRequests || new Map()).get(row.dataset.reqId);
    const floor = slot.querySelector("[data-factory-floor]");
    if (floor && req) applyFloor(floor, req, false);
    if (!reduced()) animate(slot, { opacity: [0, 1], y: [4, 0] }, { duration: 0.2, ease: "easeOut" });
  }
});

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
