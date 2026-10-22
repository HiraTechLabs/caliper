# AUDIT — Phase 0 (Verify)

Date: 2026-10-22. Scope: verify `PLAN_P1.md` section 0 against the live repo.
No code changed. `docs/AUDIT.md` is the only file added. Nothing merged or pushed.

Environment: Python 3.12 (`.venv`), Linux, `ssh`/`ssh-keyscan` installed.

## 1. Branch state

| Branch | Last commit | vs local `main` | vs `origin/main` | pytest |
|---|---|---|---|---|
| `main` (local) | `60ab25c` 2026-10-21 "opencode -ollama" | — | 0 ahead, 1 behind | **130 passed, 1 failed** |
| `origin/main` | `c54d814` 2026-10-21 "docs(caliper): initialize project docs" | 1 ahead, 0 behind | — | **130 passed, 1 failed** |
| `feature/opencode-ollama-adapters` (local = `origin`) | `8b4650b` 2026-10-22 "plan1.md" | 4 ahead, 0 behind | 4 ahead, 1 behind | **169 passed** |
| `r2c/principles-environments` | — | — | — | does not exist (local or remote) |

- The feature branch has 4 commits: `3727ffd` (test fixture key mode), `18d5edd` (provider-aware models + OpenCode), `20ffa68` (patch-safety primitives), `8b4650b` (this plan).
- `c54d814` adds only `.caliper/manifest.yaml` and `.caliper/decisions/README.md`; it overlaps no file the feature branch touches.
- `r2c/*` was merged/landed on `main` as `33e2110`/`62d991f`; the branch is gone.
- Working tree clean except one untracked user file, `RUN_LOCAL.md` (left untouched).

**Failure on `main`:** `tests/test_environments.py::test_environment_private_key_is_owner_only`
asserts mode `0600`, gets `0644` (the old fixture relied on default modes). `3727ffd` on the feature branch fixes exactly this; the test passes there.

## 2. pytest

| Run | Command | Result |
|---|---|---|
| feature | `.venv/bin/python -m pytest -q` | 169 passed, 1 warning, 105 s |
| `main` (worktree `/tmp/opencode/caliper-main`) | same | 130 passed, **1 failed**, 128 s |
| `origin/main` (worktree) | same | 130 passed, **1 failed**, 127 s |
| `tests/test_environments.py`, `PATH` without `ssh-keyscan` | same | **8 failed, 6 passed** |

The suite runs against the worktree's own package, so the branch result is real, not the
editable install leaking in.

## 3. Section 0 findings — confirm / correct

| # | Finding | Verdict | Evidence |
|---|---|---|---|
| 1 | Deploy is `MockDeployAdapter` by default; "merge" writes a marker; no git branch/merge/push in the workflow | **Confirmed** | `caliper/workflow.py:41` defaults to mock, `:127` writes `merged.marker`; `main.py:63-68` builds `Workflow` without a deploy adapter. `workflow.py` contains no git call. `repo.py` only commits/pushes docs and principles to the default branch, never a request branch/merge. |
| 2 | Execution never sees the project code; OpenCode runs with `cwd=None`; Execution is asked for a unified diff | **Confirmed** | `http_models.py:104-112`: prompt = request + plan + graph/decisions, then "Return ONLY a unified diff". `opencode_models.py:46` `cwd=None`, and `models_config.py:116-121` builds `OpenCodeClient` without `cwd`, so it runs in Caliper's directory. |
| 3 | `patch_safety.py` is imported only by tests | **Confirmed** | Only `tests/test_patch_safety.py` imports it; no `caliper/` module does. |
| 4 | A failed review re-reviews the same diff; `request_changes` does not call Execution; the human note is unused | **Confirmed** | `workflow.py:87-102` loops without re-running `execution.execute`; `:113-122` only flips statuses and logs the note; `review_note` appears in `state.py`/templates/UI only, never in a prompt. |
| 5 | Review validation is structural only; planner check is a prefix search | **Confirmed** | `workflow.py:18-25` checks only presence of `status`/`reason` and `pass|fail`; no coverage of approved IDs. `http_models.py:95` tests for the literal prefixes `SEC-`/`ARC-`/`PRF-`/`CLC-`/`BUS-`/`TEC-`. |
| 6 | `httpx` and `python-multipart` needed at runtime but missing from `dependencies` | **Confirmed** | `pyproject.toml`: runtime = fastapi, uvicorn, jinja2, pyyaml; `httpx` only in the `dev` extra; `python-multipart` absent (yet installed in the venv, and `Form(...)` routes in `main.py` depend on it). `http_models.py:22` imports `httpx` at runtime. |
| 7 | `ModelsSettings.save()` drops `executable`; API keys are written then chmod-ed | **Confirmed** | `models_config.py:226-234` writes provider/base_url/model/timeout only; `:246-252` `write_text` then `chmod(0600)`. |
| 8 | Frontend is Jinja2 + static JS; "two `app.js` copies"; every file CRLF | **Partially corrected** | Jinja2 + vanilla JS confirmed (no React/Vite/Tailwind/shadcn). The two `app.js` files are **not duplicates**: `caliper/static/js/app.js` is the source and `caliper/static/app.js` its committed esbuild bundle (`package.json` build script; `templates/base.html:8` loads `/static/app.js`). **CRLF is not present**: working tree and committed blobs are LF, `git status` is clean; only `.caliper-state/state.sqlite3` contains `\r`. No line-ending normalization commit is needed; `.gitattributes` is still missing. |
| 9 | 161 tests pass; 8 environment tests fail without `ssh-keyscan` | **Corrected / confirmed** | Count is **169** on the feature branch (131 on `main`, one failing). The `ssh-keyscan` dependency is **confirmed**: 8 failures without it, because only some tests monkeypatch `scan_host_key`. |
| 10 | Good and to keep: repo-backed principles, per-env keys + pinning, `.caliper/` project docs, safe deletion, SSE, D-001..D-031, OpenCode design | **Confirmed** | PrinciplesService reads/writes `.caliper/principles/`; `remote.py` pins host keys per environment; `project_docs.py`; `deletion.py` with traversal/symlink tests (`tests/test_project_delete.py:137,150`); `/api/events` SSE + `EventSource`; D-031 is the last entry; OpenCode argv-only, no `--auto`, token redaction (`opencode_models.py:23-36,63`). |

## 4. Additional findings (not in section 0)

1. **No CI at all.** No `.github/` directory, and `git log --all -- .github` is empty. AGENTS.md
   section 8 requires exactly three jobs; both `docs/GRAPH.md` and `docs/graph.yaml` say
   "when CI is wired". This is an AGENTS violation, not just a plan gap.
2. **The stack deviates from AGENTS.md section 6.** The UI is Jinja2 + hand-written CSS +
   vanilla JS + motion/esbuild, not React + TypeScript + Vite + Tailwind + shadcn/ui. The plan
   tolerates this until the frontend port, but it must be recorded/approved as an amendment.
3. **`docs/UI_SYSTEM.md` and root `UI_SYSTEM.md` contradict each other and both are still
   DRAFT.** `docs/` describes Jinja2+HTMX; root describes React+shadcn. AGENTS.md section 7
   requires the human-approved UI system *before* pages are built; there is no recorded approval,
   yet the whole Jinja UI exists.
4. **Duplicated, diverged self-documentation.** `docs/DECISIONS.md`, `docs/GRAPH.md`,
   `docs/graph.yaml`, `docs/UI_SYSTEM.md` and `.caliper/DECISIONS.md`, `.caliper/GRAPH.md`,
   `.caliper/graph.yaml` all exist and differ: `docs/GRAPH.md` has 18 nodes vs 16 in
   `.caliper/GRAPH.md`; D-031 exists only in `docs/DECISIONS.md`; UI specs differ. AGENTS.md
   section 5 names `docs/` as authoritative.
5. **`docs/graph.yaml` describes code that does not exist.** It says the workflow owns
   "review validation (per-rule verdicts mandatory)" and lists a `deploy-adapter` that is the
   mock. The nodes' paths all exist, but the descriptions drift from the code; no `graph-check`
   can catch it.
6. **`node_modules/` is committed** (1001 tracked files, including a Windows esbuild binary;
   pack size 7.00 MiB) with no DECISIONS entry and not in `.gitignore`. `package.json`/bundle are
   committed too, so the source/bundle pair is fine — the vendored dependency tree is not.
7. **OpenCode is selectable for all three roles**, although AGENTS.md section 12.8 allows an
   agent-CLI adapter "for Execution later", not before the chat-completions adapters work end to
   end. There is no evidence of a real end-to-end model run.
8. **Plaintext secret on disk.** `projects/caliper`'s repository deploy key is a plaintext
   OpenSSH private key at `.caliper-state/secrets/projects/caliper/repo_deploy` (file 600,
   directories 755), outside Git; no API keys exist; the SQLite DB contains no key material.
   This is the Phase 1 migration target, not a leak, but AGENTS.md 12.3's "never in plaintext"
   intent is not met yet.
9. **No `.gitattributes`** (line-ending policy) — still worth adding, even though no normalization
   is needed today.
10. **`3727ffd` (test fixture fix) has no DECISIONS entry.** Minor, test-only; noting it for
    completeness. `docs/DECISIONS.md` and `graph.yaml` were otherwise updated in the same commits
    as the code (`18d5edd`, `20ffa68`).
11. **Startup distinct-model validation does work** (AGENTS.md section 3): `normalize_settings`
    raises on duplicate model ids and `create_app` builds the workflow eagerly, so startup fails.
    Roles are blank by default. Not a defect — recorded here so nobody "fixes" it.

## 5. Recommendation for landing `feature/opencode-ollama-adapters`

Land it as a normal merge; the branches are disjoint (the only overlap candidate is `docs/`,
which `c54d814` does not touch). It also fixes the one failing test on `main`.

1. `git fetch origin`
2. `git switch main && git merge --ff-only origin/main` — bring local `main` up to `c54d814`.
   (Local `main` is stale; merging the feature into the stale `main` would silently drop the
   `.caliper/` commit.)
3. `git merge --no-ff feature/opencode-ollama-adapters -m "Merge provider-aware models and patch-safety primitives"`.
   No force push, no rebase: keeps published SHAs and avoids rewriting the feature branch.
4. Push `main`, then run `pytest -q` on the merged `main` — expect 169 passed (the branch's
   `3727ffd` fixes the 0600 fixture; `c54d814` only adds `.caliper/manifest.yaml` and
   `.caliper/decisions/README.md`, already covered by `3a15555`).
5. Delete `feature/opencode-ollama-adapters` (local and remote) only after the human confirms the
   merged `main`.
6. Do **not** bundle Phase 0b fixes into this merge. Land it as-is, then start Phase 0b
   (review loop, review coverage, dependencies, settings, mock removal, hygiene). Two items in
   Phase 0b shrink: no line-ending normalization is needed, and the `app.js` question is a
   source/bundle decision (keep both or stop committing the bundle), not a duplicate removal.
7. This `docs/AUDIT.md` commit is on the feature branch, so it lands with the merge.

Risk: low. No code overlap, 169/169 green on the branch, and the merged tree adds only two
documentation files from `origin/main`. Residual risk is the pre-existing gaps above (no CI,
mock deploy, Jinja UI), all of which Phase 0b/0c and the plan's later phases address.

## 6. Report

- **Works:** full suite green on the feature branch; distinct-model validation; repo-backed
  principles with states; environment keys + host-key pinning; safe deletion; SSE; provider-aware
  model config; patch-safety primitives (tested, unwired).
- **Does not work:** no real deploy, merge, branch, worktree or code-aware Execution; review
  coverage and the review→Execution loop are not enforced; runtime deps incomplete; UI is not the
  approved stack/spec; no CI; duplicated docs trees; `node_modules` committed.
- **Next:** human approval of this audit and the merge plan, then the merge, then Phase 0b.
