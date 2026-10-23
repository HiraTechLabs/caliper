# Report — Repository access management, knowledge workspace, graph visualization

**Date:** 2026-10-23
**Branch:** `feature/phase1-container-secret-store` (code committed on this feature branch; not merged)
**Scope:** WS1 deploy-key replace/remove without deleting a project; WS2 unified knowledge
workspace (principles/manifest/decisions/graph) on the project page; WS3 interactive project
graph rendering. Decisions D-069..D-072.

## Objective

One project page must answer, at a glance: what can this project reach, what does it know about
itself, and what does its architecture look like — plus a safe way to rotate or remove the
credential that grants that access, without deleting the project or faking success.

## What was built

- **WS1 — credential lifecycle** (`caliper/registry.py`, endpoints in `caliper/main.py`):
  - `POST /api/projects/{id}/repository/key/replace` — generates a replacement ed25519 pair,
    stores it under scope `project/<id>` as `deploy_key_replacement` /
    `deploy_key_replacement.pub`, and shows the new public key while the active key keeps
    working. Nothing is overwritten.
  - `POST .../key/activate` — runs the repository access test **with the replacement material**
    (via the injected repo tester), and only after a verified read+write promotes the pair over
    the active pair, records `rotated_at`, and sets `remote_revocation_pending: true`.
  - `POST .../key/discard` — deletes only the staged pair.
  - `DELETE /api/projects/{id}/repository/key` — removes the credentials (secret store entries
    + public cache + key paths) but never the project, its rows or the GitHub repository.
  - `caliper/remote.py`: `ReadCheckError` / `WriteCheckError` subclasses; the recorded access
    level becomes `repo["access"] = "write" | "read" | None`, with manifest defaults applied
    backward-compatibly for legacy manifests (`repo.setdefault`).
  - All form twins under `/projects/{id}/...` render via the existing project page.
- **WS2 — workspace** (`caliper/project_docs.py`, `caliper/templates/project_detail.html`):
  one `#workspace` section with four server-rendered tab views selected by
  `?view=principles|manifest|decisions|graph` (unknown values fall back to `principles`).
  Anchor aliases `#principles` and `#docs` are kept inside the section so old deep links and
  tests still land. Docs forms pass a hidden `view` field and redirect through
  `_docs_redirect`, which keeps the active view.
  - Principles tab: rule chips parsed from the repository content (`parse_rules`).
  - Manifest tab: structured row tables (`manifest_workspace`) plus a redacted raw file pane;
    secret-looking keys (password/secret/token/credential/private-key/api-key/access-key/
    master-key, case-insensitive) show `[redacted]` (D-071).
  - Decisions tab: newest first, with parsed fields (`decision_fields`) and the raw file behind
    a details element; empty state explains what to append.
- **WS3 — graph** (`normalize_graph` + `caliper/static/js/app.js`):
  the page embeds a JSON payload in `<script id="caliper-graph-data" type="application/json">`
  (with `</` escaped) and the vanilla JS renderer draws a circular layout with pan/drag, wheel
  and button zoom, click-to-select with related-node highlighting and dimming, per-kind filter
  checkboxes and keyboard support (Esc, arrows). Broken graph input renders with plain warnings
  and keeps the valid part; raw `graph.yaml` in the repository is never rewritten (D-072). The
  bundle was rebuilt with esbuild (`caliper/static/app.js`).
- **Redirect bug fixed during testing:** `/projects/<id>/docs/*` form redirects with a `view`
  field produced `?view=<v>?docs_message=...` (double `?`), losing the view param on follow.
  `_docs_redirect` now separates query parameters with `&` when a view is present.

## Intentional rename (documented)

Section heading "Project docs" changed to "Knowledge workspace";
`tests/test_project_docs.py::test_project_page_shows_the_docs_section` was updated to the new
heading. No other existing test semantics changed (`#docs` hash preserved).

## Evidence (exact commands and actual results)

| Check | Command | Actual result |
|---|---|---|
| Graph consistency | `.venv/bin/python scripts/graph_check.py` | `OK: 29 nodes, 72 edges, 72 decisions` |
| Lint/type style | `.venv/bin/ruff check caliper tests scripts` | `All checks passed!` |
| New + adjacent tests | `.venv/bin/python -m pytest tests/test_workspace.py tests/test_project_docs.py tests/test_repository_access.py -q` | `46 passed` |
| Full suite | `.venv/bin/python -m pytest -q` | `1 failed, 292 passed, 2 skipped` |

- New test files: `tests/test_repository_access.py` (15 tests: legacy manifest defaults, staged
  replacement, failing verification keeps the active key, activation, discard, no-staged error,
  secret-store failure isolation, removal keeps the project, double-remove idempotent, project
  isolation, private-key redaction across API/page/manifest, read-only and unreadable access
  levels, full form lifecycle) and `tests/test_workspace.py` (20 tests: unit coverage of
  `normalize_graph` / `decision_fields` / redaction helpers; page tests for the default view,
  rule chips from the repository, redacted manifest rows, decisions newest first and empty
  state, graph payload embedding, missing-edge and duplicate-id warnings with the valid part
  drawn, empty and no-file graph states, and view-preserving form redirects).
- **The one full-suite failure is pre-existing and out of scope:**
  `tests/test_container_assets.py::test_compose_publishes_loopback_only_and_writes_only_to_mounted_paths`
  fails because the working tree contains an uncommitted `compose.yaml` change (ports changed
  from `127.0.0.1:8080:8080` to `8080:8080`) made before this task. It was left untouched on
  purpose; it needs a human decision (loopback-only binding is a security invariant of
  `docs/UI_SYSTEM.md`/README). Baseline before this task was `258 passed, 2 skipped`.

## Self-documentation updated in the same commit

- `.caliper/DECISIONS.md`: D-069 (staged, verified deploy-key rotation; manual remote
  revocation), D-070 (workspace tab views), D-071 (manifest redaction at the view layer),
  D-072 (normalized graph rendering with warnings). Index + entries, contiguous IDs.
- `.caliper/graph.yaml` + `.caliper/GRAPH.md`: node descriptions updated for `project-docs`,
  `project-registry`, `app-ui`, `tests`; snapshot wording updated. No new nodes or edges needed
  — every changed path already had a node; graph-check green.

## Known limitations, stated plainly

1. **No real-GitHub integration test.** Repository access verification uses the same injected
   tester mechanism as the existing key tests; end-to-end only against local `file://` remotes
   and scripted testers. Labeled as such.
2. **Remote revocation is manual.** After an activated rotation the UI shows "Rotation pending
   on GitHub"; the human must remove the old key on the repo's deploy-keys page. Caliper cannot
   and does not fake it.
3. **Repository access checks use `StrictHostKeyChecking=accept-new`** — no per-repo pinned
   known_hosts for repository tests (per-environment probes still pin host keys).
4. **`compose.yaml` loopback failure** is the human's uncommitted change, unresolved here.
5. The graph renderer is vanilla JS + SVG, consistent with the transitional UI; it is not part
   of the React UI_SYSTEM.md v2 port (D-044).

## Next step

Human: (a) decide the `compose.yaml` port change, (b) review the rotation flow on a real
repository. Nothing was merged and no remote branch was pushed.
