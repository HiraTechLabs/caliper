# Caliper

Caliper is a private software factory: a single fixed workflow through which every change
(feature or bugfix) to a managed project must pass.

```
Request (feature | bugfix)
  -> Planning Model  -> plan
  -> Execution Model -> change on a branch (Dev env)
  -> Review Model    -> verdict against the project's Principles
  -> Human approval  -> merge -> Prod env
```

One Caliper hosts many **Projects**. It is not a build or supply-chain pipeline. AI roles are
disabled until human-approved adapters are enabled; human approval is mandatory for merge to
Dev and deploy to Prod.

## Run with Docker (recommended)

Prerequisites: Docker Engine with the Compose plugin. All setup works over SSH; no desktop
session is needed.

1. Create the master key **once**, outside the data volume:

   ```
   python -m caliper init-secrets --output secrets/master.key
   sudo chown 10001:10001 secrets/master.key
   chmod 400 secrets/master.key
   ```

   The container runs as UID 10001 and reads this file as a Compose secret. It cannot read a
   root-owned 0600 file, so the `chown` is required. If you already have a Docker/Kubernetes
   secret, point `CALIPER_MASTER_KEY_HOST_FILE` at a file that UID 10001 can read instead.
   `python -m caliper` is available after `pip install -e .`; you can also run the command in a
   throwaway virtualenv. Never commit the key or store it inside the data volume.

2. Start Caliper:

   ```
   docker compose up -d --build
   ```

3. Check readiness and open the UI through an SSH tunnel:

   ```
   curl http://127.0.0.1:8080/health/ready
   ssh -L 8080:localhost:8080 <laptop-or-server>
   # then open http://localhost:8080 in your browser
   ```

The port is published only on `127.0.0.1`; no auth is exposed to the network. The Compose file
uses a read-only root filesystem, a non-root UID, a named `caliper-data` volume, a master-key
secret, `no-new-privileges`, `cap_drop: ALL` and a 2 GB memory limit. The host Docker socket is
never mounted.

### Optional local models (Ollama)

```
docker compose --profile ollama up -d
```

Ollama is off by default, has no published port, and stores models in the `ollama-models`
volume. Inside the Compose network Caliper reaches it at `http://ollama:11434/v1`, **not**
`http://127.0.0.1:11434/v1` (inside a container that address means the container itself). To
reach a model on the Docker host, use `http://host.docker.internal:<port>/v1`; the Compose
service already maps that hostname.

## Run without Docker (development)

```
pip install -e ".[dev]"
python -m caliper init-secrets --output secrets/master.key
CALIPER_DATA_DIR=./data \
CALIPER_RUNTIME_DIR=/tmp/caliper-run \
CALIPER_MASTER_KEY_FILE=./secrets/master.key \
python -m caliper
```

The bare-metal defaults are still `/data` and `/run/caliper`, so a development run should set
the two directories explicitly. `python -m caliper serve --host/--port` overrides the bind
address for one run.

## Configuration

Every setting comes from a `CALIPER_*` environment variable. Secret-like settings also accept a
`<NAME>_FILE` variant (for example `CALIPER_MASTER_KEY_FILE`) and refuse to fall back when that
file is configured but unreadable.

| Variable | Default | Meaning |
|---|---|---|
| `CALIPER_DATA_DIR` | `/data` | The only persistent writable tree |
| `CALIPER_DB_PATH` | `<data>/caliper.db` | SQLite run state |
| `CALIPER_PROJECTS_ROOT` | `<data>/projects` | Project records and local clones |
| `CALIPER_WORK_ROOT` | `<data>/work` | One request worktree per project/request |
| `CALIPER_SECRETS_DIR` | `<data>/secrets` | Encrypted store, public keys, known_hosts |
| `CALIPER_MODELS_CONFIG` | `<data>/models.yaml` | Non-secret model settings |
| `CALIPER_RUNTIME_DIR` | `/run/caliper` | Ephemeral tmpfs; materialized private keys only |
| `CALIPER_MASTER_KEY_FILE` | — | File holding the 32-byte master key |
| `CALIPER_MASTER_KEY` | — | The same key as a value (less safe; visible in inspect) |
| `CALIPER_HOST` / `CALIPER_PORT` | `127.0.0.1` / `8080` | Bind address (Compose sets `0.0.0.0`) |
| `CALIPER_LOG_LEVEL` | `INFO` | JSON log level |
| `CALIPER_SHUTDOWN_GRACE_SECONDS` | `10` | Grace period before exit |

`data/`, `projects/` and `work/` are ignored by Git and are not part of the image.

## Secret store and keys

Secrets are encrypted at rest with AES-256-GCM (`cryptography`) in
`<data>/secrets/store.sqlite3`. Every write gets a fresh nonce; the scope and name are
authenticated, so a ciphertext cannot be moved between projects or roles. `list` returns names
only, and no secret is returned by a list endpoint, written to a log, or included in a model
prompt.

Scopes are `project/<id>` (deploy key), `env/<project>/{dev,prod}` (environment keys and later
app values) and `model/{planning,execution,review}` (API keys). Private SSH keys are decrypted
only for one operation into `/run/caliper` (tmpfs) with mode 0600, then zero-overwritten and
removed. The master key is never stored in SQLite or in the data volume.

On first start, Phase 0c state is migrated one way: `.caliper-state/state.sqlite3` is copied
(the old file is kept), and plaintext model/deploy/environment keys are written to the store and
read back before their files are removed. A different existing value or a wrong master key stops
the migration and leaves the plaintext in place. Reruns are safe. The store also records a
non-secret key-check marker, so a wrong 32-byte key fails startup instead of looking healthy
until the first secret is read.

**Back up the master key separately.** A backup of `/data` without the key cannot be decrypted;
a leaked key plus the data volume exposes every secret.

## Health and shutdown

- `GET /health/live` — the process is up.
- `GET /health/ready` — SQLite responds, the data directory is writable, the secret store is
  unlocked and startup checks finished. Returns 503 with the failing check otherwise. The
  container `HEALTHCHECK` uses this endpoint.
- `GET /health` — backward-compatible liveness alias.

Logs are one JSON object per line on stdout. On SIGTERM Caliper stops accepting new runs, asks the
workflow to stop at a phase boundary, marks in-flight requests `interrupted` (resumable, not
`failed`), waits for the grace period and exits cleanly. Startup recovery does the same for rows
left in planning/execution/review.

## Upgrade

```
git pull
docker compose build
docker compose up -d
```

The schema and legacy migration run at startup. Take a backup first when the release notes mention
a data change.

## Backup and restore

The named volume is `caliper_caliper-data`. With Caliper stopped:

```
docker compose stop caliper
mkdir -p backup
docker run --rm -v caliper_caliper-data:/data -v "$PWD/backup:/backup" \
  caliper:phase1 tar czf /backup/caliper-data.tgz -C /data .
install -m 400 secrets/master.key backup/master.key
docker compose start caliper
```

Restore into an empty volume with the same command in reverse (extract the archive as UID 10001),
then start Caliper. Never restore a data archive without its matching master key.

## Single-instance limitation

Caliper is one container with one `caliper-data` volume. SQLite plus the encrypted store are
single-writer; do not run replicas against the same volume. Postgres, Kubernetes and a split
API/worker are deliberately deferred (D-066).

## Set up a project (principles and environments)

The principle files of a project live in **its Git repository** (`.caliper/principles/*.md`) and
are the source of truth. Caliper reads them from the default branch, commits what you write
back to that branch, and never invents a rule: a new principle file is a heading and one
instruction line. Each principle has a state: **Missing** (no file in the repository),
**Draft** (present or edited, not approved) and **Approved** (approved at a recorded content
hash and commit). Editing an approved file resets it to Draft. A rule is a list item whose text
starts with an ID like `SEC-001`; approval needs at least one rule and unique IDs.

The environment (Dev, Prod) has host, user, deploy path, compose file and health URL. Caliper
generates one ed25519 key per environment, keeps the private key encrypted and shows only the
public key plus the exact `ssh-copy-id` command. It never stores a password. The host key is
pinned on the first successful connection and shown as a fingerprint; a change fails loudly.
`Test connection` uses `BatchMode` and checks `ssh <host> true`, `docker compose version` and
that the deploy path exists and is writable; every failure comes back as a plain reason with
the exact fix command.

The repository itself uses a per-project deploy key (ed25519) and a read/write probe
(`git ls-remote` plus a push dry-run).

The project page shows a setup checklist: Repository access, Principles (6), Dev environment,
Prod environment. Requests stay disabled until all four are complete, and the button names the
missing item.

## Project docs (manifest, graph, decisions)

Every project repository also carries its own docs under `.caliper/`:

- `.caliper/manifest.yaml` — portable project metadata (id, name, product, repository URL)
- `.caliper/graph.yaml` — project components (`nodes`) and relations (`edges`)
- `.caliper/decisions/*.md` — append-only decision records

The project page's **Docs** section reads these from the local clone and has three actions:
**Sync from repository**, **Create missing docs** (write only missing files; existing files are
never overwritten) and **Add decision** (append one new Markdown decision; old decisions are
never edited). The graph and decisions are included in Planning, Execution and Review prompts.
The local `<data>/projects/<id>/manifest.yaml` remains Caliper's runtime/secret record (keys,
connection state, approval hashes); it is not the portable project manifest.

## Models

AI roles are disabled until Settings > Models has three distinct models (Planning, Execution,
Review). Each role picks a **provider**:

- **OpenAI-compatible HTTP** (default): a `base_url` ending in `/v1` plus the model id, and an
  optional API key stored encrypted under `model/<role>` (never in `models.yaml`). A local Ollama
  works as-is, for example `http://ollama:11434/v1` from Compose or
  `http://host.docker.internal:11434/v1` for a host runtime.
- **OpenCode CLI**: only a model id in `provider/model` form (`opencode models` lists what is
  installed). Caliper runs `opencode run --format json` with a bounded timeout and never passes
  `--auto`; OpenCode keeps its own credentials, so no Caliper-stored API key is used.

The model id is the identity: no two roles may resolve to the same model even across providers.
Check one role against its real backend without secrets:

```
python -m caliper.smoke planning|execution|review [--timeout N]
```

## Remove a project

`DELETE /api/projects/{id}?stop_dev=false&stop_prod=false` removes the project from Caliper:
active requests are cancelled, run history, local files, worktrees and encrypted keys are
removed, and an audit event is kept. The GitHub repository and its `.caliper/` files are never
deleted. Optional Dev/Prod stop runs before any deletion and aborts everything if it fails.
After deletion, remove the deploy key from the GitHub repository settings yourself.

A single finished request can be deleted from its **Danger zone** on the request page; this also
removes its plan, diff and review artifacts. Requests that are planning, executing or reviewing
are protected until they stop.

## Run tests (one command)

```
pytest
```

The container acceptance test is optional and needs a built image:

```
docker build --target runtime -t caliper:phase1 .
CALIPER_CONTAINER_IMAGE=caliper:phase1 pytest tests/test_container_integration.py -q
```

The full suite can also run inside the image:

```
docker build --target test -t caliper:phase1-test .
```

## Remote (headless) access

Caliper binds to `127.0.0.1` by default (Compose publishes `127.0.0.1:8080` on the host) and is
meant to be used over an SSH tunnel from the machine you work on:

```
ssh -L 8080:localhost:8080 <laptop-or-server>
```

then open `http://localhost:8080` in your browser on your local machine. No auth is exposed to
the network; all setup works without a desktop session.

## Repo layout

```
AGENTS.md    repo rules (read first)
.caliper/    Caliper's own decisions (DECISIONS.md), graph (GRAPH.md + graph.yaml) and manifest
docs/        human-facing documents: UI_SYSTEM.md, AUDIT.md, PLAN_P1.md and reports
caliper/     application package: FastAPI app, registry, secret store, CLI, templates + static CSS
data/        runtime state on a bare-metal run (ignored; /data in the container)
projects/    runtime project records/clones (ignored; <data>/projects)
work/        runtime request worktrees (ignored; <data>/work)
scripts/     repository checks (graph_check.py, the graph-check CI job)
tests/       tests (unit, integration, real Git remotes, optional real container)
Dockerfile   multi-stage runtime + test targets
compose.yaml Caliper + optional Ollama profile
```

## CI

CI has exactly three jobs in `.github/workflows/ci.yml`:

- `test` — installs the package into a clean virtualenv and runs the full pytest suite
  (unit tests plus the clean-install end-to-end test).
- `lint` — runs `ruff` 0.17.1 over `caliper/`, `tests/` and `scripts/`.
- `graph-check` — runs `scripts/graph_check.py`: graph paths and edges, every `caliper/`
  module represented, decision index/entry agreement, and on pull requests that code
  changes also update the decision log and the graph.

Container acceptance tests are not part of those three jobs yet (Phase 4 adds the image job).

## Status

The workflow runs end-to-end with configured real models: project setup checklist, request
intake, planning, execution inside one isolated worktree, the project's test command as a gate,
review of the authoritative Git diff, a 3-attempt loop, human approval bound to the reviewed
commit and diff hash, a real `git merge --no-ff` and push, project docs read from the repository,
safe project deletion, and SSE request updates.

Phase 1 adds the container foundation and encrypted secret store: `CALIPER_*` configuration,
AES-256-GCM storage, one-way migration from Phase 0c, tmpfs key materialization, health and
interrupted shutdown, JSON logs, the non-root read-only image and the Compose topology. Phase 2
(real Dev/Prod deploys and SSH credential handling), Phase 3 (login-based model access and the
agent runner) and Phase 4 (image CI) are not built. See `.caliper/DECISIONS.md` D-053..D-066 for
the Phase 1 decisions and D-046..D-052 for the real Git loop.
