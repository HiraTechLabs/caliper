# AGENTS.md — Caliper (Private Software Factory), Iteration 2

Read this fully before touching the repo. Follow it literally. No extra features.

## 0. Fresh start — rules that prevent the previous failure
The previous attempt failed: UI not understandable, CI purpose unclear, ~90% of features non-functional.
This repo starts empty. Do not reuse or recreate anything from it by habit.

1. **Vertical slice first.** Build one thin path that works end-to-end (register project -> submit request -> 3 roles with mock adapters -> approval -> Dev/Prod state) before adding anything else.
2. **One step at a time.** Never start step N+1 until step N runs, is tested, and is shown to the human.
3. **No stubs in the UI.** A feature that does not work does not appear. No placeholder pages, no dead buttons.
4. **No feature without a test and a user-visible effect.** If the human cannot see it work, it is not done.
5. **No unexplained component.** Every file, job, and dependency has a one-line reason in `docs/DECISIONS.md`. If you cannot write the reason, do not add it.
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
Caliper documents itself with the same system it provides:
1. `docs/DECISIONS.md` — one entry per decision: id, date, decision, reason, alternatives rejected.
   Every non-trivial choice you make gets an entry **in the same commit**.
2. `docs/GRAPH.md` (+ `graph.yaml` as source of truth) — nodes (components) and edges (relations).
   Update in the same commit as any structural change. CI fails if `graph.yaml` references a missing component.
3. `docs/UI_SYSTEM.md` — see section 7.

## 6. Stack (fixed, do not change without a Decision entry)
Python, FastAPI, Jinja2, YAML manifests per project, Git. SQLite for state (no external DB).

## 7. UI / UX rules (UX is a top priority)
Language: English only. No i18n/RTL work for now.

**System**
- `docs/UI_SYSTEM.md` is written and approved by the human BEFORE any page is built: palette, type scale, spacing, components, state colors, light + dark themes.
- Reuse the defined components only. No per-page custom styling, no random generated look.
- Server-rendered (Jinja2) with HTMX for partial updates. No SPA framework.

**Every screen answers, at a glance:** What is this? What state is it in? What can I do next? One primary action per screen.

**Request states are always visible and color-coded:** Planning / Executing / Reviewing / Awaiting approval / Merged / Failed.
Live progress updates without page reload.

**Three core flows must be simple and elegant. Hard limits:**
1. Add project: one form, 3 fields max up front (name, repo URL or "create new", stack preset). Principles are pre-filled from templates and editable later. Done in under a minute.
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
- Browser <-> core: HTTP (Jinja2 + HTMX).
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
2. Create DECISIONS.md, GRAPH.md/graph.yaml, UI_SYSTEM.md (and record the scaffold decisions).
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
