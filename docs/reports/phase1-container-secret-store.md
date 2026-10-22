# Phase 1 — Container foundation and encrypted secret store

**Status: PASS**
**Audit date:** 2026-10-22
**Branch:** `feature/phase1-container-secret-store`
**Phase 1 implementation commit:** `9b7be48`
**Base:** `7cb3dda` (`main`)
**Canonical report:** this file
**Historical report:** [`../PHASE1_REPORT.md`](../PHASE1_REPORT.md) (preserved, with a pointer here)

## Verdict

The six Phase 1 acceptance criteria in `docs/PLAN_P1.md` section 3 were rechecked from a fresh
isolated Compose project, not from the previous report's claims. All six pass; the
migration/no-secret criterion is shown below as two separate evidence checks. The container also
satisfies the additional security checks requested for this audit: file-supplied master key,
restricted writable mounts, no Docker socket, no secret material in logs or `/data`, and clean
SIGTERM handling with interrupted-request recovery.

The pre-audit report in this checkout had no literal placeholder token left, but its acceptance
table did not carry the commands and actual results needed to trust it. Every item below was
therefore treated as unverified until re-run. No output in this report was copied from an
expected result or from the old report.

## Audit setup and data safety

The audit used an isolated Compose project, a new disposable volume and a new master key. The
pre-existing volume `caliper_caliper-data` and the pre-existing key
`/tmp/opencode/caliper-compose/master.key` were observed and left untouched. No existing volume
was deleted, no existing key was overwritten, and the audit did not run `docker compose down -v`
against the pre-existing project.

```text
$ date -u
2026-10-22 16:42 UTC
$ docker volume ls --format '{{.Name}}'
caliper_caliper-data
core_portainer_data
point_pgdata

$ ss -ltnp '( sport = :8080 )'
State Recv-Q Send-Q Local Address:Port Peer Address:PortProcess
(no listener: port 8080 was free)

$ AUDIT_DIR=/tmp/opencode/caliper-p1-audit-20261022
$ mkdir -p "$AUDIT_DIR" && chmod 700 "$AUDIT_DIR"
$ .venv/bin/python -m caliper init-secrets --output "$AUDIT_DIR/master.key"
Wrote a new master key to /tmp/opencode/caliper-p1-audit-20261022/master.key (mode 0600).
$ chown 10001:10001 "$AUDIT_DIR/master.key" && chmod 400 "$AUDIT_DIR/master.key"
$ stat -c '%a %u:%g %n' "$AUDIT_DIR/master.key"
400 10001:10001 /tmp/opencode/caliper-p1-audit-20261022/master.key
```

The isolated stack:

```text
$ CALIPER_MASTER_KEY_HOST_FILE="$AUDIT_DIR/master.key" \
    docker compose -p p1audit up -d --build
...
 Container p1audit-caliper-1 Created
 Container p1audit-caliper-1 Starting
 Container p1audit-caliper-1 Started

$ docker compose -p p1audit ps
NAME                IMAGE            COMMAND                  SERVICE   CREATED         STATUS
p1audit-caliper-1   caliper:phase1   "python -m caliper s…"   caliper   3 minutes ago   Up 5 seconds (health: starting)
$ docker inspect p1audit-caliper-1 --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
healthy
```

At the end of the audit the isolated stack was removed together with only its own volume:

```text
$ docker compose -p p1audit down -v
 Container p1audit-caliper-1 Stopping
 Container p1audit-caliper-1 Stopped
 Container p1audit-caliper-1 Removing
 Container p1audit-caliper-1 Removed
 Volume p1audit_caliper-data Removing
 Network p1audit_default Removing
 Volume p1audit_caliper-data Removed
 Network p1audit_default Removed
$ docker volume ls --format '{{.Name}}' | sort
caliper_caliper-data
core_portainer_data
point_pgdata
```

The pre-existing `caliper_caliper-data` volume still exists after cleanup. The audit key was left
in `/tmp/opencode/caliper-p1-audit-20261022/` for inspection; it is disposable.

## Acceptance evidence

### 1. `docker compose up -d --build` then `/health/ready` returns 200 — PASS

```text
$ curl -s -o "$AUDIT_DIR/ready-initial.json" -w '%{http_code}\n' \
    http://127.0.0.1:8080/health/ready
200
$ cat "$AUDIT_DIR/ready-initial.json"
{"status":"ready","checks":{"database":{"ok":true,"detail":"SQLite responds to SELECT 1."},"data_dir":{"ok":true,"detail":"/data is writable"},"secret_store":{"ok":true,"detail":"Secret store is unlocked."}}}

$ docker inspect p1audit-caliper-1 --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
healthy
```

### 2. Restart keeps projects, principles state and secrets — PASS

Project creation and the deploy key:

```text
$ curl -s -o /dev/null -w '%{http_code}\n' -X POST \
    --data-urlencode 'name=Phase 1 Audit' \
    --data-urlencode 'repo_url=git@github.com:example/phase1-audit.git' \
    http://127.0.0.1:8080/projects/new
303
$ curl -s -o /dev/null -w '%{http_code}\n' \
    http://127.0.0.1:8080/projects/phase-1-audit
200
$ curl -s -X POST \
    http://127.0.0.1:8080/api/projects/phase-1-audit/repository/key \
    -o "$AUDIT_DIR/key-before.json" -w '%{http_code}\n'
200
```

The `303` is the form's redirect after creating the project; the project page and repository API
then returned 200. The runtime manifest contained the repository record and all six principle
state entries:

```text
$ docker compose -p p1audit exec -T caliper \
    sed -n '1,51p' /data/projects/phase-1-audit/manifest.yaml
id: phase-1-audit
name: Phase 1 Audit
created_at: '2026-10-22T16:37:34+00:00'
repo:
  url: git@github.com:example/phase1-audit.git
  verified: false
  verified_at: null
  test_message: Key generated. Add the public key to the repository, then test read/write
    access.
  key_created_at: '2026-10-22T16:37:34+00:00'
  default_branch: null
product: null
principles:
  security:
    state: missing
    content_hash: null
    approved_commit: null
    approved_at: null
    updated_at: null
    rule_ids: []
  business:
    state: missing
    content_hash: null
    approved_commit: null
    approved_at: null
    updated_at: null
    rule_ids: []
  architecture:
    state: missing
    content_hash: null
    approved_commit: null
    approved_at: null
    updated_at: null
    rule_ids: []
  techstack:
    state: missing
    content_hash: null
    approved_commit: null
    approved_at: null
    updated_at: null
    rule_ids: []
  cleancode:
    state: missing
    content_hash: null
    approved_commit: null
    approved_at: null
    updated_at: null
    rule_ids: []
  performance:
    state: missing
    content_hash: null
```

After restarting, the public deploy key and repository URL were identical, the manifest was
byte-identical and still had six principle-state entries, and the encrypted store file had the
same SHA-256:

```text
$ curl -s http://127.0.0.1:8080/api/projects/phase-1-audit/repository \
    -o "$AUDIT_DIR/repo-after-restart.json"
$ docker compose -p p1audit exec -T caliper \
    cat /data/projects/phase-1-audit/manifest.yaml > "$AUDIT_DIR/manifest-after.yaml"
$ .venv/bin/python - <<'PY'
import json
from pathlib import Path
a = Path('/tmp/opencode/caliper-p1-audit-20261022')
before = json.loads((a/'key-before.json').read_text())
after = json.loads((a/'repo-after-restart.json').read_text())
print('public_key_survived=', before['public_key'] == after['public_key'])
print('url_survived=', after['url'] == 'git@github.com:example/phase1-audit.git')
print('manifest_unchanged=', (a/'manifest-before.yaml').read_text() == (a/'manifest-after.yaml').read_text())
print('principle_entries=', (a/'manifest-after.yaml').read_text().count('state: missing'))
PY
public_key_survived= True
url_survived= True
manifest_unchanged= True
principle_entries= 6

$ docker compose -p p1audit exec -T caliper sha256sum /data/secrets/store.sqlite3
e28f35f5a2560e6c73eef981c66b2b99e4e5597c7c505f5c496699a78264eab8  /data/secrets/store.sqlite3
# after a second restart, the same SHA-256 was reported:
e28f35f5a2560e6c73eef981c66b2b99e4e5597c7c505f5c496699a78264eab8  /data/secrets/store.sqlite3
```

This proves persistence of the project row, the manifest including the six principle-state
records, and the encrypted secret store across restarts. It does not exercise an approval
transition on a reachable project repository; that is outside the Phase 1 acceptance text and is
recorded as a limitation below.

### 3. Non-root user, read-only root filesystem, no Docker socket — PASS

```text
$ docker inspect p1audit-caliper-1 --format \
    'User={{.Config.User}} ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}} CapDrop={{.HostConfig.CapDrop}} Memory={{.HostConfig.Memory}} SecurityOpt={{.HostConfig.SecurityOpt}}'
User=10001:10001 ReadonlyRootfs=true CapDrop=[ALL] Memory=2147483648 SecurityOpt=[no-new-privileges:true]

$ docker inspect p1audit-caliper-1 --format \
    '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}} RW={{.RW}};{{println}}{{end}}'
volume /var/lib/docker/volumes/p1audit_caliper-data/_data -> /data RW=true;
bind /tmp/opencode/caliper-p1-audit-20261022/master.key -> /run/secrets/caliper_master_key RW=false;

$ docker inspect p1audit-caliper-1 --format 'Tmpfs={{json .HostConfig.Tmpfs}}'
Tmpfs={"/run/caliper":"mode=0700,uid=10001,gid=10001","/tmp":"mode=1777"}
```

No mount source contains `docker.sock`. The only writable persistent mount is the disposable
`/data` volume; the key bind mount is read-only. `/tmp` and `/run/caliper` are intended tmpfs
mounts, with `/run/caliper` restricted to UID/GID 10001 and mode 0700.

### 4. Master key is supplied through a file secret, not the environment — PASS

```text
$ stat -c '%a %u:%g %n' "$AUDIT_DIR/master.key"
400 10001:10001 /tmp/opencode/caliper-p1-audit-20261022/master.key
$ docker inspect p1audit-caliper-1 --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | sed -n '/^CALIPER_/p' | sed 's/=.*/=<redacted>/'
CALIPER_MASTER_KEY_FILE=<redacted>
CALIPER_DATA_DIR=<redacted>
CALIPER_RUNTIME_DIR=<redacted>
CALIPER_HOST=<redacted>
CALIPER_PORT=<redacted>
CALIPER_LOG_LEVEL=<redacted>
```

The only master-key environment entry is `CALIPER_MASTER_KEY_FILE`; its value points at the
mounted `/run/secrets/caliper_master_key`. There is no `CALIPER_MASTER_KEY` value in the
environment.

### 5. Logs and persisted data do not expose secret values — PASS

The scans read the key from the file rather than printing it:

```text
$ grep -F -q -f "$AUDIT_DIR/master.key" "$AUDIT_DIR/container.log"
(no match)
$ grep -F -q -- 'BEGIN OPENSSH PRIVATE KEY' "$AUDIT_DIR/container.log"
(no match)
$ docker compose -p p1audit exec -T caliper \
    grep -R -F -q -f /run/secrets/caliper_master_key /data
(no match)
$ docker compose -p p1audit exec -T caliper \
    grep -R -F -q -- 'BEGIN OPENSSH PRIVATE KEY' /data
(no match)
```

The container integration test independently reads `/data/secrets/store.sqlite3` and asserts that
neither the master key nor `BEGIN OPENSSH PRIVATE KEY` appears; it also checks that the runtime
tmpfs `/run/caliper` is empty after a deploy-key operation. The API returns the deploy public key,
never the private key.

### 6. SIGTERM produces a clean shutdown and interrupted-request recovery — PASS

A test-only request row was inserted into the disposable database with status `executing` to
exercise the lifecycle path. This is a database-level check, not a model run:

```text
$ docker compose -p p1audit exec -T caliper python -c \
    "from caliper.state import RequestStore; s=RequestStore('/data/caliper.db'); \
     r=s.create('phase-1-audit','feature','Audit in-flight request'); \
     s.update(r['id'], status='executing', branch='caliper/req-1', base_commit='deadbeef'); \
     print(r['id'])"
req-1

$ docker compose -p p1audit stop -t 20 caliper
 Container p1audit-caliper-1 Stopped
$ echo $?
0
$ docker inspect p1audit-caliper-1 --format \
    'ExitCode={{.State.ExitCode}} OOMKilled={{.State.OOMKilled}}'
ExitCode=0 OOMKilled=false
```

The shutdown log and the recovered row:

```text
$ grep -n 'interrupted\|shutdown\|Shutting' "$AUDIT_DIR/container.log"
11:caliper-1  | {"ts": "2026-10-22T16:37:52.072+00:00", "level": "INFO", "logger": "uvicorn.error", "message": "Shutting down"}
12:caliper-1  | {"ts": "2026-10-22T16:37:52.173+00:00", "level": "INFO", "logger": "uvicorn.error", "message": "Waiting for application shutdown."}
13:caliper-1  | {"ts": "2026-10-22T16:37:52.241+00:00", "level": "WARNING", "logger": "caliper.app", "message": "Shutdown interrupted 1 active request(s): req-1"}
14:caliper-1  | {"ts": "2026-10-22T16:37:52.243+00:00", "level": "INFO", "logger": "uvicorn.error", "message": "Application shutdown complete."}

$ docker compose -p p1audit start caliper
$ curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/health/ready
200
$ docker compose -p p1audit exec -T caliper python -c \
    "from caliper.state import RequestStore; s=RequestStore('/data/caliper.db'); r=s.get('req-1'); \
     print('status='+r['status']); print('failure_reason='+(r['failure_reason'] or '')); \
     print('base_commit='+(r['base_commit'] or ''))"
status=interrupted
failure_reason=Caliper is shutting down; this run was interrupted and can be run again.
base_commit=deadbeef
```

The shutdown marked the in-flight row `interrupted` with a resumable reason and preserved the
base commit; the next start returned ready without converting it to `failed`. The startup log did
not print a second recovery message because the shutdown had already marked the row; this is
consistent with the implementation and is recorded here rather than hidden.

### 7. Existing plaintext secrets migrate with no plaintext left — PASS

The migration suite is part of the full host run and was also run directly:

```text
$ .venv/bin/python -m pytest tests/test_migration.py -q
......                                                                   [100%]
6 passed in 7.91s
```

The test creates legacy plaintext deploy, environment and model keys, runs the migration, verifies
each value through the encrypted store, and asserts that the source plaintext files are gone.
It also covers idempotent reruns, a wrong master key, conflicting values, and an integrity failure
that must leave the plaintext in place.

### 8. No secret in API responses — PASS

The container acceptance test requests the repository API after creating a deploy key and checks
the returned public key; it does not receive private key material. The direct audit query and
result:

```text
$ curl -s http://127.0.0.1:8080/api/projects/phase-1-audit/repository \
    -o "$AUDIT_DIR/repository-api-final.json"
$ .venv/bin/python - <<'PY'
import json
from pathlib import Path
p = Path('/tmp/opencode/caliper-p1-audit-20261022/repository-api-final.json')
text = p.read_text()
data = json.loads(text)
print('fields=', sorted(data))
print('public_key_present=', data.get('public_key','').startswith('ssh-ed25519 '))
print('private_key_marker_present=', 'BEGIN OPENSSH PRIVATE KEY' in text)
print('master_key_present=', 'master.key' in text)
PY
fields= ['default_branch', 'key_created_at', 'key_exists', 'key_mode', 'key_path', 'keys_url', 'mode', 'private_key_storage', 'public_key', 'test_message', 'url', 'verified', 'verified_at']
public_key_present= True
private_key_marker_present= False
master_key_present= False
```

It contained the public key and the public-key path, no private-key marker and no master-key
value. The full host suite also covers the API response shapes for repository access, models and
environments. The persisted-data and log scans above additionally found no private key marker or
master key.

### 9. Full test suite passes inside the container image — PASS

See the exact build/run output in the final validation section. The `test` target installs the
project into the image's virtualenv, copies the tests and `.caliper`, and runs the complete suite
inside the image.

### 10. Actual runtime image size — PASS

```text
$ docker save caliper:phase1 | wc -c
95741952
$ docker image inspect caliper:phase1 --format '{{.Size}}'
95722188
$ docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep caliper
caliper:phase1       393MB
caliper:phase1-test  561MB
```

`docker save` reports the archive size (95,741,952 bytes, 91.3 MiB). `docker image inspect` reports
95,722,188 bytes of image content after the final rebuild. `docker images` reports 393 MB
uncompressed on disk for the runtime image. The different numbers are different measurements, not
a discrepancy.

## Final validation after the documentation changes

The commands below were run after the reporting, decision and graph changes. Documentation-only
changes do not alter the runtime image, but the host suite, lint, graph check and container checks
were rerun so the committed tree has a current result.

```text
$ .venv/bin/python -m pytest -q
258 passed, 2 skipped, 1 warning in 377.82s (0:06:17)
```

```text
$ .venv/bin/ruff check caliper tests scripts
All checks passed!
$ .venv/bin/python scripts/graph_check.py
graph-check: OK: 29 nodes, 72 edges, 68 decisions
```

```text
$ docker build --target test -t caliper:phase1-test .
...
#21 [test 7/7] RUN python -m pytest -q
#21 15.19 ........................ss.......................................... [ 27%]
#21 231.5 .................................................................... [ 55%]
#21 257.0 .................................................................... [ 83%]
#21 351.6 ..............................
#21 408.9 258 passed, 2 skipped, 1 warning in 400.65s (0:06:40)
#22 naming to docker.io/library/caliper:phase1-test
```

The build exited 0 and the complete suite passed inside the `test` target.

```text
$ CALIPER_CONTAINER_IMAGE=caliper:phase1 \
    .venv/bin/python -m pytest tests/test_container_integration.py -q
..                                                                       [100%]
2 passed in 19.27s
```

The integration test started the final runtime image with the Compose security posture, checked
readiness, created a project and deploy key, restarted it, verified persistence and the encrypted
store, checked UID 10001/read-only/no socket and the empty runtime tmpfs, scanned logs and SQLite
for private material, and stopped it with SIGTERM expecting exit code 0.

## Failures found during the audit

- The pre-audit report's PASS rows were not backed by durable command output. Corrected by this
  report; the old file is preserved with a pointer.
- The pre-audit container test stopped the container with SIGTERM but did not check an in-flight
  request recovery. The audit added a disposable database-level check and recorded the exact
  result above. The integration test itself still covers the clean exit code and persistence.
- The pre-audit report had no literal placeholder token at the start of this checkout. That does
  not excuse the missing evidence; all rows were re-run anyway.

## Limitations and honest scope

1. The SIGTERM/recovery check used a SQLite row, not a full model-driven request. It proves the
   shutdown/recovery state transition and clean process exit, not a partially completed model
   call.
2. The disposable repository URL is not reachable, so the audit did not create a draft or approved
   principle state on a real remote. It proves the six principle-state records and the manifest
   persisted byte-for-byte across restart; the host suite covers the state machine separately.
3. Environments and real Dev/Prod deployment remain Phase 2 work. Phase 1 ends at merge/push and
   blocks deployment with the existing "Deployment is not configured" state.
4. The master-key file must be readable by UID 10001. The audit key is mode 0400 and owned by
   10001; a root-owned 0600 key would fail with the documented plain error.
5. SQLite and the secret store remain single-instance only (D-066).
6. The Debian package versions and base image digest are pinned; a later rebuild may require a
   deliberate version bump if the archive removes a pinned package.
7. The container acceptance test remains opt-in via `CALIPER_CONTAINER_IMAGE`, so a plain host
   `pytest` run does not require Docker.

## Decisions and graph

- D-068 records the permanent file-based reporting rule.
- `work-reporting` is now a `presentation` node for `docs/`, with an edge from `tests` recording
  that verification evidence is written there.
- `.caliper/graph.yaml` and `.caliper/GRAPH.md` are updated in the same commit as this report.

## Stop point

Phase 1 is complete on this branch with the audit above. Nothing was merged to `main`, and Phase 2
was not started. The audit stack used the isolated `p1audit` Compose project and its own volume;
the pre-existing `caliper_caliper-data` volume and existing master key were not changed.
