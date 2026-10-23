# Phase 2 readiness review — Environments

**Review date:** 2026-10-22
**Review time:** 18:09 UTC (start of evidence collection)
**Branch:** `feature/phase1-container-secret-store`
**HEAD:** `52c96fe`
**Base:** `main` / `origin/main` at `7cb3dda`
**Scope:** review only. No application code, test, branch, merge, push, volume, master key, or
credential was changed.

## Verdict

**NOT READY to begin Phase 2 (Environments) today.** The Phase 1 foundation and the Phase 0c
real-Git workflow are present and verified, but the plan's start gate is not closed:

1. The Phase 0c **manual real-model milestone** has no recorded evidence. The end-to-end tests
   use test role adapters, a scripted local HTTP handler, or stubbed CLI execution; none is a real
   model call. The README's claim that the workflow runs "with configured real models" is not
   backed by a report or log entry.
2. Phase 1 is pushed but is **not merged to `main`**. Continuing Phase 2 on the Phase 1 feature
   branch is a human branch-strategy decision.

The Phase 2 implementation surface itself is almost entirely absent. That is expected before
Phase 2 starts, but it means there is no existing environment/deploy feature to review as
"ready": no `.caliper/deploy.yaml`, no deploy strategy model, no runtime deploy adapter, no
remote executor, no release/rollback behavior, and no app environment-variable values in the
secret store. The existing environment form is still the Phase 0c form (host, user, deploy path,
compose file, health URL) with generated keys and `ssh-keyscan` host-key pinning.

This is a start-gate verdict, not a claim that Phase 2 has been implemented.

## Commands and actual results

### Repository state

```text
$ git status --porcelain=v1
(no output: clean before this review)
$ git branch --show-current
feature/phase1-container-secret-store
$ git log --oneline -8
52c96fe Phase 1: audit evidence and add file-based reporting
a330316 Phase 1: record verification evidence in the report
9b7be48 Phase 1: Container Foundation and Secret Store
7cb3dda 0c-1: make the factory loop real
ad6db2f 0b-10: correct graph and README descriptions to match the code
d08e734 0b-9: keep the v2 UI system as the only UI spec
aad0b2e 0b-8: make .caliper the single home for decisions, graph and manifest
180fe71 0b-7: add CI with test, lint and graph-check jobs
$ git rev-parse HEAD origin/feature/phase1-container-secret-store main origin/main
52c96fe376365611dfb1cfde71c505f797e5b470
52c96fe376365611dfb1cfde71c505f797e5b470
7cb3ddafdec6284715a077a70d71cc0ef5f6dad7
7cb3ddafdec6284715a077a70d71cc0ef5f6dad7
$ git rev-list --left-right --count origin/feature/phase1-container-secret-store...HEAD
0	0
$ git ls-remote --heads origin refs/heads/feature/phase1-container-secret-store refs/heads/main
52c96fe376365611dfb1cfde71c505f797e5b470	refs/heads/feature/phase1-container-secret-store
7cb3ddafdec6284715a077a70d71cc0ef5f6dad7	refs/heads/main
```

The branch is clean, the Phase 1 tip is pushed to `origin`, and the local branch is level with its
upstream. The three Phase 1 commits are not on `main` or `origin/main`.

### Tests, lint, graph

```text
$ .venv/bin/python -m pytest -q
258 passed, 2 skipped, 1 warning in 311.92s (0:05:11)

$ .venv/bin/ruff check caliper tests scripts
All checks passed!

$ .venv/bin/python scripts/graph_check.py
graph-check: OK: 29 nodes, 72 edges, 68 decisions
```

The two skips in the full run were identified separately:

```text
$ .venv/bin/python -m pytest -q -rs tests/test_container_integration.py tests/test_remote_tools.py tests/test_environments.py
SKIPPED [1] tests/test_container_integration.py:191: set CALIPER_CONTAINER_IMAGE to a built image to run container acceptance tests
SKIPPED [1] tests/test_container_integration.py:230: set CALIPER_CONTAINER_IMAGE to a built image to run container acceptance tests
16 passed, 2 skipped, 1 warning in 12.41s
```

The two skips are the opt-in container acceptance tests. The host suite therefore does **not**
exercise a real SSH server, a real deploy target, or the container image in this review.

The prior Phase 1 canonical report records the separate container acceptance run as PASS, but it
was not rerun here; no Docker volume or container was touched in this review.

### Code-presence checks

```text
$ test ! -e caliper/deploy.py && echo 'no caliper/deploy.py'
no caliper/deploy.py
$ test ! -e caliper/environments.py && echo 'no caliper/environments.py'
no caliper/environments.py
$ grep -RIl "deploy.yaml" caliper tests || echo 'no deploy.yaml reference in code/tests'
no deploy.yaml reference in code/tests
```

`.caliper/deploy.yaml` is mentioned in `docs/PLAN_P1.md` and the README, but is not read, parsed,
validated, or tested anywhere in the application or its tests.

## Verified facts

The following are supported by the current files and the commands above, not by the plan's
description.

### Repository and process

- The working tree was clean at the start of the review.
- `feature/phase1-container-secret-store` is at `52c96fe`, and the remote branch is at the same
  commit. `main` and `origin/main` are at `7cb3dda`.
- Phase 1 is not merged to `main`; continuing on the feature branch or merging first is a human
  decision.
- The full host suite passes: 258 passed, 2 skipped. Lint passes. `graph-check` passes with
  29 nodes, 72 edges, and 68 decisions.
- No `.caliper/DECISIONS.md` or graph change was made by this review. No structural decision was
  made merely to record the review.

### Phase 0c / factory loop

- `docs/PLAN_P1.md` section 2c lists a real-Git integration test and a human-present real-model
  manual milestone (`docs/PLAN_P1.md:81-84`).
- `tests/test_factory_loop.py` uses a real bare Git remote and real worktrees. The successful
  path test is `test_a_request_is_committed_tested_reviewed_merged_and_pushed`
  (`tests/test_factory_loop.py:98`), and the real-HTTP-transport test is
  `test_the_http_adapters_run_the_full_loop_over_real_http`
  (`tests/test_factory_loop.py:441`).
- The real-HTTP test builds the three roles with `make_http_adapters` against a local
  `ThreadingHTTPServer` (`tests/test_factory_loop.py:480-491`). The handler returns scripted
  responses; it is not a language model. This verifies the HTTP transport and the loop, not a real
  model call.
- No Phase 0c canonical report exists under `docs/reports/` (only the Phase 1 report is present).
- `docs/AUDIT.md:78` states there was "no evidence of a real end-to-end model run."
  `docs/PHASE1_REPORT.md:173-174` says "No real model calls are part of this phase."
- `README.md:309-313` claims the workflow runs end-to-end "with configured real models." The
  repository contains no recorded command/output that supports that claim. This is a documentation
  claim, not a verified run.

### Deploy behavior

- `create_app` builds `Workflow` without a `deploy` argument (`caliper/main.py:111-123`).
- `Workflow.deploy_configured` returns false when no adapter is supplied
  (`caliper/workflow.py:104-105`).
- `approve_merge` performs the real Git merge and push, sets `merged`, and stops there when no
  deploy adapter is configured (`caliper/workflow.py:418-449`).
- `deploy_dev` and `approve_prod` call `_require_deploy`, which raises
  `DeploymentNotConfigured` with "Deployment is not configured" (`caliper/workflow.py:471-492`).
- The only deploy-adapter implementation in the repository is `RecordingDeployAdapter` under
  `tests/` (`tests/mock_models.py:41-49`). Runtime does not select it; `D-036` and
  `tests/test_workflow.py:269-272` enforce that no mock deploy adapter exists in the runtime.
- There is no `RemoteExecutor`, no release directory (`releases/<commit>`, `current`), no automatic
  rollback, no health-check execution, no deploy log streaming, and no runtime deploy record.
  Searches for `deploy.yaml`, `RemoteExecutor`, `rollback`, and `releases/` found none in
  application code or tests. `caliper/remote.py` only tests SSH/Compose and can stop an existing
  Compose deployment (`stop_environment`, `caliper/remote.py:312-334`).

### Environment model and credentials

- The stored environment fields are host, user, deploy path, compose file, health URL, verification
  state, and host-key fingerprint (`caliper/registry.py:33-45`). There is no port, auth method,
  credential reference, imported key, password bootstrap, or prepare-host state.
- `save_environment` accepts only `host`, `user`, `deploy_path`, `compose_file`, and `health_url`,
  and requires deploy path, compose file, and health URL (`caliper/registry.py:396-409`). The
  Phase 2 plan makes the compose file optional/auto-detected and the health check optional; the
  current schema contradicts both.
- The project page still renders that Phase 0c form: Host, SSH user, Deploy path, Compose file,
  Health URL (`caliper/templates/project_detail.html:123-140`). It has no port, auth-method,
  credential, deploy-spec, or app-secret controls.
- The JSON API mirrors the same fields (`caliper/main.py:945-983`).
- Generated SSH keys are implemented: `ensure_environment_key` creates an ed25519 pair and stores
  the private key encrypted under `env/<project>/<env>`; only the public key is cached as a file
  (`caliper/registry.py:374-394`, `tests/test_environments.py:80-105`).
- There is no imported-key path, no password bootstrap, and no prepare-host flow. The only password
  test proves that an unknown `password` form field is not stored
  (`tests/test_environments.py:120-125`); it does not implement or test bootstrap.
- `caliper/remote.py` accepts a `port` argument, but the registry does not store or pass one
  (`caliper/registry.py:513-532`), `test_environment` does not add `-p` to the SSH command
  (`caliper/remote.py:233-241`), and `stop_environment` has no port argument at all
  (`caliper/remote.py:312-334`). Non-22 SSH is therefore unsupported through the application, even
  though the low-level host-key scanner has a port parameter.

### SSH host-key verification

- The implementation exists: `scan_host_key` uses `ssh-keyscan`, `pin_host_key` writes a
  per-environment `known_hosts`, SHA256 fingerprints are recorded, and the SSH base uses
  `StrictHostKeyChecking=yes` with `UserKnownHostsFile`
  (`caliper/remote.py:173-241`; tests at `tests/test_environments.py:161-213`).
- The tests are synthetic. `FakeRunner` replaces the process runner
  (`tests/test_environments.py:35-54`), and `test_test_connection_runs_batchmode_docker_compose_and_the_deploy_path`
  monkeypatches `caliper.remote._run` (`tests/test_environments.py:135-158`). There is no real
  `sshd` container, no real key-authenticated login, and no real changed-host-key handshake.
- `tests/test_remote_tools.py` does exercise the real `ssh-keygen` and a real `ssh-keyscan` against
  an unreachable port. That proves tool availability and the unreachable-host error path, not a
  successful SSH authentication or a host-key change against a live server.
- The Phase 2 acceptance criteria require a real `sshd` container for key auth, imported key,
  password bootstrap, and host-key change (`docs/PLAN_P1.md:172-176`). That test environment does
  not exist in `compose.yaml` or the test suite.

### Secret store and app environment variables

- The encrypted store has the required `env/<project>/<env>` scope and AES-256-GCM storage
  (`caliper/secrets.py:28-42`, `caliper/secrets.py:212-280`; `D-054`).
- The only values currently stored in that scope are the environment SSH key pair
  (`ssh_key`, `ssh_key.pub`) and the host-key files. No application environment-variable API or
  value model exists. `README.md:114` itself says "environment keys and later app values."
- Therefore the Phase 2 requirement for deployed app secrets (env var names in
  `.caliper/deploy.yaml`, values in `env/<project>/<env>`, injection at deploy time, 0600 on the
  target) is **not implemented**. The foundation exists; the feature does not.

### Test connection checklist

- The current check does `ssh ... true`, `docker compose version`, and a deploy-path write test
  (`caliper/remote.py:266-309`). It pins the host key when `known_hosts` is supplied.
- The Phase 2 checklist additionally requires TCP reachability, all authentication methods,
  Docker permission, the Compose plugin only when needed, free disk space, and a plain
  pass/fail line with an exact fix command (`docs/PLAN_P1.md:169-170`). Those are absent.
- Compose and health URL are mandatory in the current save path, so the "Dockerfile-only project"
  and "optional health check" cases from Phase 2 cannot be configured.

### UI / API

- The current UI is the transitional Jinja UI. `D-044` and `docs/PLAN_P1.md:36` say this is not
  the target UI and Phase 2 should deliver API + tests rather than new Jinja pages if the React
  port is not done.
- There is no Phase 2 UI/API for `.caliper/deploy.yaml`, strategy selection, auth-method
  selection, imported keys, password bootstrap, app env values, release history, rollback, or
  deploy logs.
- The existing environment API does return public keys, fingerprints, and `ssh-copy-id` commands;
  that is a usable Phase 0c surface, not a Phase 2 surface.

### Deletion/stop interaction

- Optional project-deletion undeploy currently calls `stop_environment` with
  `host`, `user`, `deploy_path`, `compose_file`, and the key (`caliper/deletion.py:135-158`).
- `stop_environment` only runs `docker compose -f <compose_file> down`; it has no port, no
  strategy, no release/current handling, and no imported-key/password path
  (`caliper/remote.py:312-334`).
- Phase 2 adds `image`, `dockerfile`, and `script` strategies. The plan does not define what
  "stop the Dev/Prod deployment" means for those strategies. This is a real design question for
  the human before deletion is extended, not a parser detail.

## Readiness matrix

### Start-gate criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| G1 | Current branch is clean and Phase 1 is pushed | **READY** | Clean tree; HEAD and `origin/feature/...` both `52c96fe`; `git rev-list --left-right --count` = `0 0`. |
| G2 | Phase 1 is integrated into `main` | **NEEDS HUMAN DECISION** | `main` and `origin/main` are `7cb3dda`; the feature branch is three commits ahead. No merge; the review was forbidden to merge. Decide whether Phase 2 stacks on the feature branch or waits for a Phase 1 merge. |
| G3 | Phase 0c real factory loop is implemented and tested | **READY** | Full suite passes; `tests/test_factory_loop.py:98` uses a real bare Git remote/worktree; `:441` uses real HTTP transport. |
| G4 | Phase 0c manual real-model milestone is recorded | **NEEDS HUMAN DECISION** | `docs/PLAN_P1.md:84` requires it; no Phase 0c report exists under `docs/reports/`; `docs/AUDIT.md:78` says no real end-to-end run was evidenced; the Phase 1 report says no real model calls (`docs/PHASE1_REPORT.md:173-174`). Only the human can witness/accept this. |
| G5 | No runtime deploy mock; blocked state is honest | **READY** | `caliper/main.py:111-123` builds no deploy adapter; `caliper/workflow.py:471-492` raises `DeploymentNotConfigured`; `D-036`; `tests/test_workflow.py:224-272`. |

### Phase 2 capability and dependencies

| # | Phase 2 requirement | Status | Evidence |
|---|---|---|---|
| P1 | Split connection data into host, port, user, auth method, credential reference, pinned fingerprint | **NOT READY** | `caliper/registry.py:33-45` has host/user/deploy path/compose/health/fingerprint only; no port, auth method, or credential reference. |
| P2 | Generated-key authentication per environment | **READY** | `ensure_environment_key` and encrypted store materialization (`caliper/registry.py:374-394`); `tests/test_environments.py:80-105`. |
| P3 | Imported key, password bootstrap, optional prepare-host | **NOT READY** | No fields, API, or implementation. `tests/test_environments.py:120-125` only proves an unknown password field is not stored. |
| P4 | SSH host-key pinning and change detection | **READY** (implementation + synthetic tests) | `caliper/remote.py:173-241`; `tests/test_environments.py:161-213`. |
| P5 | Real SSH authentication and host-key-change integration against a real `sshd` | **NOT READY** | No real SSH server in `compose.yaml` or tests; all environment tests use `FakeRunner`/monkeypatched `_run` except the real unreachable `ssh-keyscan` check (`tests/test_remote_tools.py:20`). |
| P6 | `.caliper/deploy.yaml` is the Git source of truth, parsed and validated | **NOT READY** | No `deploy.yaml` reference in `caliper/` or `tests/`; the old local `compose_file`/`health_url` fields remain. |
| P7 | Compose file optional, auto-detection, strategy model | **NOT READY** | No strategy/discovery code; `save_environment` requires `compose_file` and `health_url` (`caliper/registry.py:406-409`). |
| P8 | Deploy execution: RemoteExecutor, releases/current, keep last 3, health, automatic rollback, logs | **NOT READY** | No runtime deploy adapter; `create_app` supplies none; `deploy_dev`/`approve_prod` only call an injected adapter (`caliper/workflow.py:478-492`). |
| P9 | Runtime Dev/Prod transitions through a real adapter | **NOT READY** | `approve_merge` stops at `merged` when `deploy_configured` is false (`caliper/workflow.py:447-449`); `deploy_dev`/`approve_prod` raise `DeploymentNotConfigured`. |
| P10a | Secret store can hold env-scoped values | **READY** | `env/<project>/<env>` scope in `caliper/secrets.py:28-42`; AES-GCM tests and Phase 1 suite. |
| P10b | App env values API and injection at deploy time | **NOT READY** | Only `ssh_key`/`ssh_key.pub` are stored in the env scope; no app value API or 0600 target file. `README.md:114` says "later app values." |
| P11 | Connection checklist: TCP, auth, Docker permission, disk, strategy-specific checks | **NOT READY** | `caliper/remote.py:266-309` checks only `ssh true`, `docker compose version`, and deploy-path write. |
| P12 | UI/API for new environment/deploy fields and app secrets | **NOT READY** | Jinja form has only the old five fields (`caliper/templates/project_detail.html:123-140`); the JSON API mirrors them (`caliper/main.py:945-983`). |
| P13 | Real-component acceptance tests (sshd, each strategy, rollback, env secrets) | **NOT READY** | No real `sshd` test service; no strategy tests; container acceptance tests are skipped in this run (`CALIPER_CONTAINER_IMAGE` unset). |
| P14 | Stop/remove behavior for non-Compose strategies | **NEEDS HUMAN DECISION** | `stop_environment` only supports `docker compose ... down` (`caliper/remote.py:312-334`); Phase 2 adds `image`, `dockerfile`, and `script` but the plan does not define how to stop them. |

## What is genuinely ready

- The encrypted store, master-key handling, tmpfs key materialization, and env scope are already
  present and covered by the Phase 1 suite.
- Generated per-environment ed25519 keys and host-key pinning are implemented at the low level,
  with synthetic behavior tests and a real `ssh-keyscan` unreachable-host test.
- The Phase 0c workflow performs real Git worktree work, real test gating, real review of the
  authoritative diff, real merge/push, and an honest blocked deployment state.
- The repository is clean; Phase 1 is pushed; full host tests, lint, and graph-check pass.

## What is not ready

- A human-observed real-model run for Phase 0c is not recorded.
- Phase 1 is not on `main`; the branch strategy for Phase 2 is undecided.
- Phase 2's connection/auth model, deploy-spec source of truth, strategies, real deploy execution,
  app secrets, test checklist, and real SSH integration tests do not exist yet.
- The current UI/form and `stop_environment` are tied to the old mandatory Compose/health model.

## Recommended next implementation step

**Gate first:** do not begin the code step until the human records/accepts G4 and decides G2.
If the human waives the G4 milestone or accepts it outside the repository, record that decision
before starting Phase 2.

**One smallest implementation step: a read-only `.caliper/deploy.yaml` loader and strategy
detector for the Compose path, with no deployment.**

Scope:

- Add one module that reads `.caliper/deploy.yaml` from the project's Git default branch (not from
  the local runtime manifest), validates `version: 1`, the `dev`/`prod` environment keys, the
  four strategy names, optional compose settings, and optional health settings.
- Resolve `compose_file: auto` by checking `compose.yaml`, `compose.yml`, `docker-compose.yml`,
  `docker-compose.yaml` at the repository root; return a plain diagnostic when none exists.
- When the spec is absent, detect a propose-only strategy from the same repository: Compose file
  first, then `Dockerfile`, then `.caliper/deploy.sh`; return `missing`/`unconfigured` when none
  matches. Do not write or commit anything automatically.
- Expose the result through one read-only API endpoint, for example
  `GET /api/projects/{id}/deploy-spec`, returning the source (repo/auto-detected/missing), the
  resolved spec, the source commit, and diagnostics. It must never return secret values.
- Do not add a deploy adapter, SSH calls, a UI deploy button, or a Jinja page. The Phase 2 plan
  permits API + tests and a list of screens for the future React port.

Acceptance criteria:

1. A project with a valid Compose spec returns the parsed dev/prod strategy and the resolved
   compose path, read from the Git default branch, with no local-manifest fallback.
2. `compose_file: auto` resolves the first existing file in the documented order; if none exists,
   the response says the strategy cannot be Compose and names the missing file.
3. A project without `.caliper/deploy.yaml` gets a proposal based on repository contents, but no
   file is written and no deployment is attempted.
4. Invalid version, unknown environment, unknown strategy, invalid health type, or a missing
   required field returns 422 with a plain message and no traceback.
5. Output contains env var **names** only; a value placed in the spec never appears in the API
   response, logs, or an exception.
6. Tests use a real temporary Git remote/clone (not a mocked filesystem), cover the valid spec,
   auto-detection precedence, missing Compose, invalid schema, secret-free output, and no deploy
   call. The existing 258-test suite remains green.
7. The new component and its reason are recorded in `.caliper/DECISIONS.md`, and
   `.caliper/graph.yaml` / `.caliper/GRAPH.md` are updated in the same commit.

Out of scope for this step: SSH connections, port/auth-method fields, imported keys, password
bootstrap, RemoteExecutor, releases/current, health execution, rollback, app env values, deploy
logs, and the React UI.

## Assumptions and limits

- No real host was contacted. No deployment was attempted. No Docker volume, master key,
  credential, or user data was read, modified, or deleted.
- The container acceptance tests were **NOT RUN** in this review because `CALIPER_CONTAINER_IMAGE`
  was not set. The prior Phase 1 canonical report's container results are historical evidence, not
  a result produced by this review.
- The Phase 0c real-HTTP test uses a scripted local HTTP handler, not a real language model. The
  test result supports the transport/loop claim, not a real-model claim.
- The README's real-model status claim was not independently reproducible from the repository and
  is therefore treated as unverified, not as a failure of the code.
- The review did not establish whether `main` or the feature branch is the intended Phase 2 base;
  that is a human decision.
- No application code or test was edited. No commit, merge, push, or branch change was made.
- No decision or graph entry was added for this review, because the review itself made no
  structural or architectural change. The recommended step, if accepted, will require its own
  decision and graph update.

## Stop point

Review complete. **Verdict: NOT READY to start Phase 2 until G2/G4 are resolved by the human.**
The technical foundation is in place, but the Phase 2 implementation surface is not, and the
prerequisite evidence is incomplete. No code, test, branch, merge, push, volume, key, or credential
was changed.

The append-only log entry is in [`docs/WORK_LOG.md`](../WORK_LOG.md).
