# Report — Phase 0b (items 1–5)

Date: 2026-10-22
Branch: `main` (5 commits ahead of `origin/main`, not pushed)

## Merge commit

`7333c8be1a745e376afa341e21443081df848cd7` — "Merge provider-aware models and patch-safety primitives"

- Mode: `git merge --no-ff feature/opencode-ollama-adapters`
- Parents: `c54d814` (origin/main) and `691feda` (feature branch tip)
- Date: 2026-10-22 06:58:18 +0300
- Pushed to `origin/main` before the Phase 0b work (`c54d814..7333c8b`), without force and without rebase.

## pytest result

```
$ .venv/bin/python -m pytest -q
........................................................................ [ 38%]
........................................................................ [ 77%]
..........................................                               [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  ... StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
186 passed, 1 warning in 165.67s (0:02:45)
```

Run on the merged tree after Phase 0b items 1–5. No code changed after this run.

## `git log --oneline -8`

```
d95c800 0b-5: remove the runtime deploy mock; report Deployment is not configured
64af42c 0b-4: persist the OpenCode executable and create secret files at 0600 atomically
71f35ca 0b-3: declare httpx and python-multipart as runtime dependencies
da520d1 0b-2: require exact per-rule review coverage and approved rule IDs in plans
3a8a7c0 0b-1: re-run Execution on a failed review and on request_changes
7333c8b Merge provider-aware models and patch-safety primitives
691feda Merge branch 'feature/opencode-ollama-adapters' of https://github.com/HiraTechLabs/caliper into feature/opencode-ollama-adapters
8ba1ca0 Update PLAN_P1.md
```

## Working tree

Clean except one untracked user file, `RUN_LOCAL.md` (left untouched).
