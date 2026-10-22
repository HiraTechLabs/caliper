# AGENTS.md — Caliper (Private Software Factory), Iteration 2

Read this fully before touching the repo. Follow it literally. No extra features.

## 0. Fresh start — rules that prevent the previous failure
The previous attempt failed: UI not understandable, CI purpose unclear, ~90% of features non-functional.
This repo starts empty. Do not reuse or recreate anything from it by habit.

1. **Vertical slice first.** Build one thin path that works end-to-end (register project -> submit request -> 3 roles with mock adapters -> approval -> Dev/Prod state) before adding anything else.
2. **One step at a time.** Never start step N+1 until step N runs, is tested, and is shown to the human.
3. **No stubs in the UI.** A feature that does not work does not appear. No placeholder pages, no dead buttons.
4. **No feature without a test and a user-visible effect.** If the human cannot see it work, it is not done.
5. **No unexplained component.** Every file, job, and dependency has a one-line reason in `.caliper/DECISIONS.md`. If you cannot write the reason, do not add it.
6. **Report honestly.** After each step output: what works, what does not, what is next. Never claim "done" without running it.
7. **Stay in scope.** Anything not listed in this file requires a human decision first.

## 1. What Caliper is
A single fixed workflow. Every change (feature, bug fix) passes through it.
One Caliper hosts many **Projects**. It is NOT a build/supply-chain pipeline.

```
Request (feature | bugfix)
  -> Planning Model  -> plan
  -> Execution Model -> change on a branch (Dev env)
  -> Review Model    -> verdict against the project's Principles
  -> Human approval  -> merge -> Prod env
```

## 2. Entities (per Project)
| Entity | Meaning |
|---|---|
| Principles (6) | Security, Business, Clean Architecture / Design Patterns, TechStack, Clean Code, Performance |
| Codebase | Git repo of the project |
| Dev Environment | Where every change lands first |
| Prod Environment | Only after human approval |
| Decisions | Every decision + its reason (append-only) |
| Graph | Project architecture + relations between its components |

Global (per Caliper, not per project): three models — **Planning, Execution, Review**.

## 3. Models — hard rule
- The three models MUST be three different models (preferably different families/vendors).
  Reason: same model reviewing itself repeats its own blind spots.
- Config in `caliper/models.yaml`; startup validation **fails** if any two roles resolve to the same model.
- Model backend is pluggable (local runtime or remote API). Speed is not a requirement.
- AI roles are disabled by default. Human approval is mandatory for merge and Prod.

## 4. Principles files
Per project: `projects/<id>/principles/{security,business,architecture,techstack,cleancode,performance}.md`
- Each principle = numbered, checkable rules (`SEC-001`, `ARC-004`...).
- Planner must cite rule IDs it relies on. Reviewer must output pass/fail **per rule ID**.
- A review with no rule IDs is invalid.

## 5. Mandatory self-documentation (from the first commit)
Caliper documents itself with the same system it provides. `.caliper/` is the single home for
Caliper's own decisions, graph and manifest; `docs/` holds human-facing documents only.
1. `.caliper/DECISIONS.md` — one entry per decision: id, date, decision, reason, alternatives rejected.
   Every non-trivial choice you make gets an entry **in the same commit**.
2. `.caliper/GRAPH.md` (+ `.caliper/graph.yaml` as source of truth) — nodes (components) and edges (relations).
   Update in the same commit as any structural change. CI fails if `graph.yaml` references a missing component
   or if the decision index and entries disagree.
3. `docs/UI_SYSTEM.md` — see section 7. `docs/` also holds `AUDIT.md`, `PLAN_P1.md` and reports.

## 6. Stack (fixed, do not change without a Decision entry)
Backend: Python, FastAPI, SQLite (no external DB), Git, YAML manifests per project.
Frontend: React + TypeScript + Vite + Tailwind CSS + shadcn/ui (lucide icons) + `motion` for animation (see UI_SYSTEM.md section 8). Built to static assets and served by FastAPI, so production is still one process and one command. Node is needed only to build.
API: JSON under `/api`. Live updates via Server-Sent Events.

## 7. UI / UX rules (UX is a top priority)
Language: English only. No i18n/RTL work for now.

**System**
- `docs/UI_SYSTEM.md` is written and approved by the human BEFORE any page is built: palette, type scale, spacing, components, state colors, light + dark themes.
- Reuse the defined components only. No per-page custom styling, no random generated look.
- shadcn/ui components + Tailwind only. Forms use react-hook-form + zod. Data fetching with TanStack Query.

**Every screen answers, at a glance:** What is this? What state is it in? What can I do next? One primary action per screen.

**Request states are always visible and color-coded:** Planning / Executing / Reviewing / Awaiting approval / Merged / Failed.
Live progress updates without page reload.

**Three core flows must be simple and elegant. Hard limits:**
1. Add project: two fields (name, GitHub repo URL). No stack preset anywhere: the TechStack principles file covers it. After creation the project page shows a Setup checklist (section 12). Nothing is hard-coded or faked.
2. Add feature: one text box ("What do you want?") + optional attachments. Submit. Nothing else required. Caliper derives the rest.
3. Fix bug: same single box, a "Bug" toggle, optional paste of error/log. Submit.
Rule: any field that can be inferred or defaulted must not be asked.

**Approval screen:** plan, diff, and per-rule review verdict on ONE screen. Primary actions: Approve / Request changes. Failed rules are shown first.

**Quality bar**
- Empty, loading, and error states say what happened and what to do next. Never a raw stack trace.
- Destructive actions need confirmation and are undoable where possible.
- Keyboard-friendly: Enter submits, Esc cancels, visible focus.
- Every button works or does not exist. No placeholder pages.
- Max 3 clicks from the dashboard to any action.

## 7b. Architecture (fixed)
**Where things live**
- Laptop (control plane): Caliper core (FastAPI, workflow engine, SQLite for run state), model runtime (3 models, one loaded at a time), git worktrees (one branch per request: `caliper/req-<id>`).
- Project repo (Git remote): code + `.caliper/` containing `principles/`, `decisions/`, `graph.yaml`, `manifest.yaml`. Git is the source of truth; SQLite holds run state only and must be safely deletable.
- Dev env: separate machine, Docker. Prod env: AWS EC2, Docker.

**Access:** the laptop and Dev machine are headless servers reached over SSH from another machine. Caliper binds to `127.0.0.1` by default (configurable via env), and the human opens the UI through an SSH tunnel (`ssh -L 8080:localhost:8080 <laptop>`). No auth is exposed to the network. Document the exact command in README. All setup must work without a desktop session (no GUI tools).

**How components communicate**
- Browser <-> core: JSON API + SSE (React app served by FastAPI).
- Core <-> models: one adapter interface (HTTP to local runtime or remote API). Roles mapped in `models.yaml`.
- Core <-> Git: git CLI in worktrees.
- Core <-> Dev/Prod: SSH running `docker compose`, then a health check; result recorded.
- Roles never talk to each other. Each reads files and writes one file; only the core orchestrates:
  - Planning: request + principles + graph -> `plan.md` (cites rule IDs)
  - Execution: plan + principles + code -> commits on the branch (including decisions + graph updates)
  - Review: plan + diff + principles -> `review.json` (pass/fail per rule ID). Review must NOT see Execution's reasoning.

**Request lifecycle:** submitted -> planning -> executing -> reviewing -> (fail: back to executing, max 3 loops, then stop and ask human) -> awaiting approval -> merged + deployed to Dev -> human approves -> Prod.

## 8. CI
CI has exactly three jobs and the README explains each in one line:
1. `test` — unit + one end-to-end test of the full workflow (mock models allowed).
2. `lint` — style + type checks.
3. `graph-check` — graph.yaml consistent with the code and decisions log updated.
Nothing else.

## 9. Work order (do in this order, one step at a time)
1. Scaffold: repo layout, README (what Caliper is, how to run in one command), `docs/` folder, one passing smoke test.
2. Create `.caliper/DECISIONS.md`, `.caliper/GRAPH.md`/`graph.yaml`, `docs/UI_SYSTEM.md` (and record the scaffold decisions).
3. Project registry (create/list/view project, manifest + 6 principles files + env definitions).
4. Request intake (feature | bugfix) bound to a project.
5. Workflow engine with the 3 roles behind an adapter interface; mock adapters first.
6. Distinct-model validation.
7. Real model adapters.
8. Approval gate -> merge -> Prod.
9. UI polish per UI_SYSTEM.md.

After each step: run it, show output, commit. If something cannot work, say so — never fake it.

## 10. Definition of done (per step)
Runs locally with one command, covered by a test, visible in the UI, recorded in DECISIONS, reflected in GRAPH.

## 11. Driving use case: COS (Caliper matures by producing it)
Caliper's first real workload is COS, a product made of several interlinked apps. Each COS app is a Caliper Project.
- Manifest supports an OPTIONAL `product:` field (e.g. `product: cos`). It is accepted and stored from step 3, but NO product-level features (inheritance, multi-project plans, integration env) are built until the human asks. Record that as a Decision.
- Milestone M1 (before any COS): the full workflow works end-to-end on a throwaway `sandbox` project with mock adapters, then with real models.
- Milestone M2: first real COS project through Caliper = `cos-contracts` (schemas/specs only, low risk). Second = `cos-platform` skeleton.
- Dogfooding rule: every limitation or friction found while producing COS is logged as a Caliper request (type: improvement) and a Decision entry. Caliper is improved through its own workflow.
- Never modify Lynx. COS is built beside it.

## 12. Setup, access, and no-mock rules

**12.1 No mocks in the running app.** Mock adapters live only under `tests/` and are never selectable at runtime. Anything not configured shows its setup state and blocks dependent actions with a one-line reason. Never fake success.

**12.2 Setup is progressive and visible.**
- Global, `Settings > Models`: three roles (Planning, Execution, Review). Each has: base URL (OpenAI-compatible), model name, API key (secret), Test button. Save is rejected if two roles resolve to the same model.
- Per project, Setup checklist on the project page: Repository access, Principles (6, each Draft or Approved), Dev environment, Prod environment. Submitting a request is disabled until all are complete, and the button says which item is missing.

**12.3 Repository access.**
- Existing GitHub repo only (may be empty). Caliper does not create repos for now.
- Per-project SSH deploy key (ed25519) generated by Caliper. The UI shows the public key and a link to the repo's Deploy keys page; the human adds it with write access.
- "Test connection" verifies read and write (ls-remote + push dry-run). Result shown plainly.
- Private keys are stored under `CALIPER_SECRETS_DIR` with mode 600. Never in SQLite, the repo, logs, or any model prompt.

**12.4 Principles.**
- Source of truth: `.caliper/principles/{security,business,architecture,techstack,cleancode,performance}.md` in the project repo. Caliper reads, edits, and commits them there.
- Per principle the human chooses: Write, Import from repo, or Draft with Planning model. A draft stays Draft until the human approves it. Starter templates contain headings and guidance only, no invented rules.

**12.5 Environments (Dev, Prod).**
- Fields: host, user, deploy path, compose file, health URL.
- Caliper generates an SSH key per environment and shows the public key for `authorized_keys`.
- "Test connection" runs `ssh <host> true` and `docker compose version`.

**12.6 Step discipline.** Implement only the step requested. Stop and report. Code for later steps written early is a violation and must be removed.

**12.7 Project deletion.**
- Meaning: "remove from Caliper". Caliper NEVER deletes the GitHub repo and NEVER touches `.caliper/` files inside it (principles, decisions, graph stay in Git).
- Removed: run history (requests, runs, verdicts, approvals), local clone and worktrees, the repo deploy key and environment SSH keys, the project row.
- Optional, off by default, listed separately: stop the Dev deployment, stop the Prod deployment (`docker compose down`, volumes are kept; never delete data volumes).
- API: `DELETE /api/projects/{id}?stop_dev=bool&stop_prod=bool`.
- Order: (1) cancel active requests, (2) run the optional undeploys; if any fails, ABORT with the exact error and delete nothing, (3) delete secrets, local files, then DB rows in one transaction, (4) write an audit event (project name, time, what was removed) to a table that survives deletion.
- Safety: local deletion only under `CALIPER_DATA_DIR/projects/<id>`; resolve the real path and assert the prefix before removing anything. Covered by a test, including symlink and `..` cases.
- The UI cannot delete without the confirmation described in UI_SYSTEM.md. After deletion, show the one-line reminder to remove the deploy key from the GitHub repo settings (Caliper cannot do it).

**12.8 Addenda (principles, environments, usage).**
- Principles states: Missing (no file in repo), Draft (present or edited, not approved), Approved (approved at a recorded content hash and commit). Editing an approved file resets it to Draft. Approve requires at least one rule and unique IDs of the form `XXX-NNN`. There are NO generated or pre-filled rules anywhere: a new principle file is a heading and a one-line instruction only.
- Principles flow: Sync from repo reads `.caliper/principles/*.md` from the default branch; the in-app editor writes the file and commits it to the default branch (human-authored, human-approved; record as a Decision).
- Environments: Caliper stores no passwords. It generates one ed25519 key per environment, shows the public key and the exact `ssh-copy-id -i <key path> <user>@<host>` command (the human uses their own password once, outside Caliper). Host key is pinned on first successful connection, its fingerprint is shown, and a later change fails loudly.
- Test connection uses `BatchMode`, checks `ssh <host> true`, `docker compose version`, and that the deploy path exists and is writable. Failures map to plain-language reasons with the exact fix command.
- Model usage: record tokens and cost per role per request and show them on the request page. Settings > Models supports an agent-CLI adapter type for Execution later; not before the chat-completions adapters work end to end.
