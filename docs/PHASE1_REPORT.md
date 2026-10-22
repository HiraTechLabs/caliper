# Report — Phase 1: container foundation and encrypted secret store

Date: 2026-10-22
Branch: `feature/phase1-container-secret-store`
Phase 1 commit: `9b7be48` (`Phase 1: Container Foundation and Secret Store`)
Base: `7cb3dda` (`main`)
Scope: `docs/PLAN_P1.md` section 3 only. Phase 2 (environments, SSH/deploy redesign) was not
started.

## Result

Phase 1 is implemented and verified on this branch. Caliper now runs as a non-root container
with a read-only root filesystem, keeps every writable path under `/data`, encrypts all
secret material at rest with AES-256-GCM, and shuts down without pretending an interrupted run
failed.

- Host suite: `258 passed, 2 skipped` (423.11s)
- In-image suite (`--target test`): `258 passed, 2 skipped` (398.69s)
- Container acceptance (`CALIPER_CONTAINER_IMAGE=caliper:phase1`): `2 passed` (21.58s)
- `ruff check caliper tests scripts`: `All checks passed!`
- `python scripts/graph_check.py`: `OK: 28 nodes, 71 edges, 67 decisions`
- Runtime image: 95,741,952 bytes via `docker save` (91.3 MiB); 393 MB uncompressed on disk

## What was built

| Area | Implementation | Evidence |
|---|---|---|
| Configuration | `caliper/config.py`: `CALIPER_*` variables, `<NAME>_FILE` for secret-like values, fixed `<data>/{caliper.db,projects,work,secrets,models.yaml}` and `/run/caliper` | `tests/test_config.py` |
| Secret store | `caliper/secrets.py`: AES-256-GCM, random 96-bit nonce per write, scope/name as AAD, names-only `list`, scoped `put/get/delete/exists/delete_scope`, key-check marker | `tests/test_secrets.py` |
| Master key | `python -m caliper init-secrets` writes an atomic 0600 key; startup refuses a missing, malformed, or non-matching key | `tests/test_config.py`, `tests/test_health_lifecycle.py` |
| Migration | `caliper/migrate.py`: copies legacy SQLite, verifies an encrypted read before deleting each plaintext key, safe reruns, never overwrites a different value | `tests/test_migration.py` |
| SSH materialization | `caliper/secret_files.py`: private keys only in `/run/caliper`, mode 0600, zero-overwrite and unlink on all paths; project/environment keys are encrypted in the store | `tests/test_secrets.py`, `tests/test_environments.py`, `tests/test_setup.py` |
| Health | `/health/live`, `/health/ready` (DB, writable data dir, unlocked store, startup complete), `/health` alias | `tests/test_health_lifecycle.py` |
| Lifecycle | SIGTERM refuses new runs, stops at phase boundaries, marks in-flight requests `interrupted` (resumable), startup recovery does the same; JSON stdout logs with redaction | `tests/test_health_lifecycle.py`, `tests/test_secrets.py` |
| Container | Multi-stage `Dockerfile`, UID 10001, read-only rootfs, `/data` volume, `/tmp` and `/run/caliper` tmpfs, pinned base digest and Debian packages, `git` + `openssh-client`, healthcheck | `tests/test_container_assets.py`, `tests/test_container_integration.py` |
| Compose | Loopback-only `127.0.0.1:8080:8080`, named `caliper-data` volume, master-key secret, `no-new-privileges`, `cap_drop: ALL`, 2 GB limit, no Docker socket, opt-in `ollama` profile | `compose.yaml`, `tests/test_container_assets.py` |
| Docs | `README.md` run/upgrade/backup/tunnel instructions; `.caliper/DECISIONS.md` D-053–D-067; graph expanded to 28 nodes / 71 edges | `README.md`, `.caliper/` |

## Acceptance checklist (PLAN_P1 section 3)

| Requirement | Status | How it was checked |
|---|---|---|
| `docker compose up -d --build`, then `/health/ready` returns 200 | PASS | ready 200 with `database`, `data_dir`, `secret_store` all `ok` |
| `docker compose restart caliper` keeps projects, setup state and secrets | PASS | same public deploy key and repository URL after restart; ready 200 |
| `docker inspect` shows non-root user, read-only rootfs, no socket mount | PASS | `User=10001:10001 ReadonlyRootfs=true CapDrop=[ALL] Memory=2147483648`; only the key file and `caliper-data` volume are mounted |
| Plaintext secrets migrated; no plaintext remains | PASS | `tests/test_migration.py` asserts the source files are gone and the encrypted values read back |
| No secret in logs or API responses | PASS | JSON redaction test, container log/SQLite scan, API returns public keys only |
| Full test suite passes inside the container | PASS | `258 passed, 2 skipped` in the `test` target |
| Image size reported | PASS | 95,741,952 bytes (91.3 MiB) compressed; 393 MB uncompressed |

## Commands and output

### Host test suite

```
$ .venv/bin/python -m pytest -q
........................ss.............................................. [ 27%]
........................................................................ [ 55%]
........................................................................ [ 83%]
............................................                             [100%]
258 passed, 2 skipped, 1 warning in 423.11s (0:07:03)
```

### Lint and graph

```
$ .venv/bin/ruff check caliper tests scripts
All checks passed!
$ .venv/bin/python scripts/graph_check.py
graph-check: OK: 28 nodes, 71 edges, 67 decisions
```

### Full suite inside the runtime base (`test` target)

```
$ docker build --target test -t caliper:phase1-test .
...
#21 [test 7/7] RUN python -m pytest -q
#21 14.61 ........................ss.............................................. [ 27%]
#21 200.4 ........................................................................ [ 55%]
#21 225.5 ........................................................................ [ 83%]
#21 315.1 ............................................                             [100%]
#21 404.7 258 passed, 2 skipped, 1 warning in 398.69s (0:06:38)
```

### Container acceptance

```
$ CALIPER_CONTAINER_IMAGE=caliper:phase1 .venv/bin/python -m pytest tests/test_container_integration.py -q
..                                                                       [100%]
2 passed in 21.58s
```

The acceptance test starts the image with the same restrictions as Compose, creates a project,
generates a deploy key, restarts the container, verifies the project and encrypted store
survive, scans logs and the SQLite file for private material, checks the runtime tmpfs is empty,
and stops with SIGTERM expecting exit code 0.

### Compose

```
$ CALIPER_MASTER_KEY_HOST_FILE=/tmp/opencode/caliper-compose/master.key docker compose up -d --build
...
 Container caliper-caliper-1 Created
 Container caliper-caliper-1 Starting
 Container caliper-caliper-1 Started
$ curl -fsS http://127.0.0.1:8080/health/ready
{"status":"ready","checks":{"database":{"ok":true,"detail":"SQLite responds to SELECT 1."},"data_dir":{"ok":true,"detail":"/data is writable"},"secret_store":{"ok":true,"detail":"Secret store is unlocked."}}}
$ CALIPER_MASTER_KEY_HOST_FILE=/tmp/opencode/caliper-compose/master.key docker compose restart caliper
$ curl -fsS http://127.0.0.1:8080/health/ready
{"status":"ready","checks":{"database":{"ok":true,"detail":"SQLite responds to SELECT 1."},"data_dir":{"ok":true,"detail":"/data is writable"},"secret_store":{"ok":true,"detail":"Secret store is unlocked."}}}
```

After the restart, `GET /api/projects/compose-acceptance/repository` returned the same
`ssh-ed25519 ...` public key and the same repository URL; the encrypted store and project data
survived in the `caliper-data` volume.

```
$ docker inspect caliper-caliper-1 --format 'User={{.Config.User}} ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}} CapDrop={{.HostConfig.CapDrop}} Memory={{.HostConfig.Memory}}'
User=10001:10001 ReadonlyRootfs=true CapDrop=[ALL] Memory=2147483648
$ docker inspect caliper-caliper-1 --format 'Mounts={{range .Mounts}}{{.Source}},{{end}}'
Mounts=/tmp/opencode/caliper-compose/master.key,/var/lib/docker/volumes/caliper_caliper-data/_data,
```

### Image size

```
$ docker save caliper:phase1 | wc -c
95741952
$ docker image inspect caliper:phase1 --format '{{.Size}}'
95722314
$ docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep caliper
caliper:phase1       393MB
caliper:phase1-test  561MB
```

`docker save` and the inspect size report the compressed image content. The `docker images` size
is the uncompressed on-disk root filesystem (base image, venv and Debian packages). Both are
reported so "size" is unambiguous.

## Decisions and graph

- D-053 runtime `CALIPER_*` config and `/data` layout
- D-054 AES-256-GCM secret store
- D-055 master key, `init-secrets`, startup refusal
- D-056 verified one-way legacy migration
- D-057 tmpfs-only private key files
- D-058 readiness separate from liveness
- D-059 `interrupted` shutdown/recovery state
- D-060 JSON logs and redaction
- D-061 worktrees under `<data>/work`
- D-062 non-root, read-only container
- D-063 loopback Compose, secret file, opt-in Ollama
- D-064 gated container acceptance tests
- D-065 model settings in `/data/models.yaml`, keys in the store
- D-066 single-instance limitation
- D-067 key-check marker rejects a wrong 32-byte key at startup

`graph.yaml`/`GRAPH.md` were updated in the same commit as the structural changes.
`scripts/graph_check.py` confirms 28 nodes, 71 edges and 67 decisions.

## What does not work / limitations

1. **Environments and deployment are still Phase 2.** There is no real Dev/Prod deploy adapter;
   approval merges and pushes the branch, and deployment reports that it is not configured.
2. **No real model calls are part of this phase.** The three roles still need configuration in
   Settings > Models; the Phase 1 tests use the existing test adapters only.
3. **The UI is still the transitional Jinja/Tailwind UI.** No React pages were added in Phase 1;
   that is Phase 3 work.
4. **Single instance only.** SQLite and the encrypted store are single-writer. Do not run
   replicas against the same `caliper-data` volume (D-066).
5. **The master-key file must be readable by UID 10001.** Compose uses a file secret; a
   root-owned 0600 file fails with a clear error. The README documents the required `chown` and
   `chmod`.
6. **The container acceptance test is opt-in.** It skips unless `CALIPER_CONTAINER_IMAGE` names a
   built image, so a plain `pytest` run does not require Docker.
7. **Pinned Debian package versions can disappear from the archive.** The base image is pinned by
   digest and `git`/`openssh-client` by version; a future rebuild may need a deliberate version
   bump.
8. **`interrupted` is resumable, not a checkpoint.** Resuming starts the workflow again from a
   fresh worktree at the default branch; it does not continue a half-finished model call.
9. **The legacy database is copied, not moved.** The old `.caliper-state/state.sqlite3` stays in
   place, so an operator must remove it deliberately after verifying the new data directory.
10. **Known-hosts migration keeps an existing target.** A non-secret `known_hosts` file is copied
    only when no target exists; a conflict leaves the legacy file in place rather than guessing.

## Stop point

Phase 1 is complete. Phase 2 (environment connection/deploy split, real Dev/Prod) is not
started. Work is committed on `feature/phase1-container-secret-store` and not merged to `main`.
