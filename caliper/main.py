import asyncio
import json
import logging
import os
import queue
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from caliper.config import (
    CaliperConfig,
    ConfigError,
    check_writable,
)
from caliper.deletion import ProjectDeleteError, ProjectDeleter
from caliper.events import EventBus
from caliper.lifecycle import Lifecycle, ShuttingDown
from caliper.logging_setup import configure_logging
from caliper.migrate import MigrationError, migrate_legacy
from caliper.models_config import ROLE_ORDER, ModelsConfigError, ModelsSettings, probe_role
from caliper.principles import PrinciplesError, PrinciplesService, parse_rules
from caliper.project_docs import ProjectDocsError, ProjectDocsService, manifest_workspace
from caliper.registry import ENVIRONMENT_ORDER, ProjectError, ProjectRegistry
from caliper.secrets import SecretStore
from caliper.state import ACTIVE_STATUSES, IN_FLIGHT_STATUSES, VALID_KINDS, RequestStore

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger("caliper.app")

RESTART_REASON = (
    "Caliper restarted while this request was running. Its in-flight state is unknown; "
    "the request was marked interrupted and can be run again from its recorded base commit."
)


class RequestDeleteError(Exception):
    pass


def _config_for(registry: ProjectRegistry | None) -> CaliperConfig:
    """Runtime config; an injected registry (tests) keeps its own paths."""
    config = CaliperConfig.from_env()
    if registry is None:
        return config
    data_dir = Path(registry.projects_root).parent
    return CaliperConfig(
        data_dir=data_dir,
        db_path=Path(os.environ.get("CALIPER_DB_PATH") or data_dir / "caliper.db"),
        projects_root=Path(registry.projects_root),
        work_root=Path(registry.work_root),
        secrets_dir=Path(registry.secrets_root),
        models_config=Path(os.environ.get("CALIPER_MODELS_CONFIG") or data_dir / "models.yaml"),
        runtime_dir=Path(registry.runtime_dir),
        host=config.host,
        port=config.port,
        log_level=config.log_level,
        shutdown_grace_seconds=config.shutdown_grace_seconds,
    )


def create_app(registry: ProjectRegistry | None = None, requests: RequestStore | None = None,
               workflow=None, principles: PrinciplesService | None = None,
               project_docs: ProjectDocsService | None = None,
               models_settings: ModelsSettings | None = None, probe=None,
               project_deleter: ProjectDeleter | None = None,
               config: CaliperConfig | None = None,
               secret_store: SecretStore | None = None,
               lifecycle: Lifecycle | None = None) -> FastAPI:
    config = config or _config_for(registry)
    if secret_store is None and registry is not None:
        secret_store = registry.secret_store
    if registry is None:
        registry = ProjectRegistry(
            config.projects_root,
            secrets_dir=config.secrets_dir,
            secret_store=secret_store,
            runtime_dir=config.runtime_dir,
            work_root=config.work_root,
        )
        secret_store = registry.secret_store
    if secret_store is None:
        secret_store = registry.secret_store
    if requests is None:
        requests = RequestStore(config.db_path)
    if principles is None:
        principles = PrinciplesService(registry)
    if project_docs is None:
        project_docs = ProjectDocsService(registry)
    bus = EventBus()
    requests.on_change = lambda change, req: bus.publish({"change": change, "id": req["id"], "request": req})
    if project_deleter is None:
        project_deleter = ProjectDeleter(registry, requests, config.data_dir)
    lifecycle = lifecycle or Lifecycle(requests)

    from caliper.workflow import Workflow
    settings = models_settings or ModelsSettings(
        config.models_config, config.secrets_dir, store=secret_store
    )
    probe_model = probe or probe_role
    artifacts_root_override = os.environ.get("CALIPER_ARTIFACTS_ROOT")
    artifacts_root = Path(artifacts_root_override) if artifacts_root_override else Path(registry.projects_root)
    workflow_injected = workflow is not None

    def build_workflow():
        if settings.configured():
            adapters = settings.make_adapters()
            return Workflow(
                requests, registry, artifacts_root,
                planning=adapters["planning"], execution=adapters["execution"],
                review=adapters["review"], docs=project_docs,
                stop_event=lifecycle.stop_event,
            )
        return Workflow(
            requests, registry, artifacts_root, docs=project_docs,
            stop_event=lifecycle.stop_event,
        )

    if workflow is None:
        workflow = build_workflow()
    elif getattr(workflow, "stop_event", None) is None:
        workflow.stop_event = lifecycle.stop_event

    def startup() -> None:
        """Validate configuration, migrate legacy state and recover interrupted runs."""
        if app.state.startup_done:
            return
        config.ensure_directories()
        writable, message = check_writable(config.data_dir)
        if not writable:
            raise ConfigError(message)
        writable, message = check_writable(config.runtime_dir)
        if not writable:
            raise ConfigError(message)
        if not secret_store.unlocked:
            # Raises the exact init-secrets command when no key is configured.
            config.load_master_key()
            raise ConfigError(
                f"The secret store at {secret_store.path} is locked: {secret_store.locked_reason} "
                "Restart Caliper with a valid CALIPER_MASTER_KEY_FILE."
            )
        try:
            report = migrate_legacy(config, secret_store, logger)
        except MigrationError as exc:
            raise ConfigError(
                f"{exc}\nCaliper did not start, so no legacy file was destroyed. "
                "Fix the item above, then start Caliper again."
            ) from exc
        if report.get("database") or report.get("models_config"):
            logger.info("Legacy Phase 0c data migrated into %s.", config.data_dir)
        recovered = requests.mark_interrupted(RESTART_REASON, status="interrupted")
        if recovered:
            logger.warning(
                "Marked %d request(s) interrupted after restart: %s",
                len(recovered), ", ".join(row["id"] for row in recovered),
            )
        app.state.startup_done = True

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        startup()
        try:
            yield
        finally:
            lifecycle.start_shutdown()
            interrupted = lifecycle.shutdown()
            if interrupted:
                logger.warning(
                    "Shutdown interrupted %d active request(s): %s",
                    len(interrupted), ", ".join(row["id"] for row in interrupted),
                )
            lifecycle.wait_idle(config.shutdown_grace_seconds)
            secret_store.close()

    app = FastAPI(title="Caliper", version="0.1.0", lifespan=lifespan)
    app.state.caliper_config = config
    app.state.secret_store = secret_store
    app.state.lifecycle = lifecycle
    app.state.startup_done = False
    app.state.startup = startup
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    static_dir = BASE_DIR / "static"

    def _asset_version() -> str:
        try:
            return str(int(max((static_dir / name).stat().st_mtime for name in ("style.css", "app.js"))))
        except OSError:
            return "1"

    def _ui_ctx(**extra):
        projects = registry.list_projects()
        return {
            "projects": projects,
            "models_configured": workflow.models_configured,
            "models_missing": workflow.missing_models(),
            "deploy_configured": workflow.deploy_configured,
            "asset_version": _asset_version(),
            **extra,
        }

    def _request_payload(req: dict, include_artifacts: bool = False) -> dict:
        entry = {key: req[key] for key in (
            "id", "project_id", "kind", "text", "status", "loop_count",
            "review_note", "branch", "base_commit", "commit_sha", "merge_commit",
            "failure_reason", "created_at", "updated_at",
        )}
        try:
            artifact_dir = workflow.artifact_dir(req["id"])
        except Exception:
            artifact_dir = Path("__missing__")
        review = None
        review_file = artifact_dir / "review.json"
        if review_file.is_file():
            try:
                review = json.loads(review_file.read_text(encoding="utf-8"))["verdicts"]
            except Exception:
                review = None
        entry["review"] = review
        entry["pass_count"] = sum(1 for v in (review or {}).values() if v.get("status") == "pass")
        entry["fail_count"] = sum(1 for v in (review or {}).values() if v.get("status") == "fail")
        entry["has_plan"] = (artifact_dir / "plan.md").is_file()
        entry["has_diff"] = (artifact_dir / "diff.patch").is_file()
        if include_artifacts:
            for name, key in (("plan.md", "plan"), ("diff.patch", "diff")):
                path = artifact_dir / name
                entry[key] = path.read_text(encoding="utf-8") if path.is_file() else None
        return entry

    def _delete_request(req: dict) -> dict:
        if req["status"] in IN_FLIGHT_STATUSES:
            raise RequestDeleteError(
                f"Request {req['id']} is {req['status']}. Wait for it to stop before deleting it."
            )
        try:
            workflow.discard_workspace(req["id"])
        except Exception as exc:
            raise RequestDeleteError(
                f"Refusing to delete request {req['id']}: its worktree could not be removed: {exc}"
            )
        raw = workflow.artifact_dir(req["id"])
        if raw.is_symlink():
            raise RequestDeleteError("Refusing to delete: the request artifact directory is a symlink.")
        base = Path(workflow.artifacts_root).resolve()
        expected = (base / req["project_id"] / req["id"]).resolve()
        if raw.resolve() != expected:
            raise RequestDeleteError("Refusing to delete: the request artifact path is outside the project.")
        if raw.exists():
            shutil.rmtree(raw)
        deleted = requests.delete(req["id"])
        if deleted is None:
            raise RequestDeleteError(f"Request {req['id']!r} does not exist.")
        return deleted

    def _state_payload(request_id: str | None = None, override: dict | None = None) -> dict:
        if request_id:
            req = override if override and override["id"] == request_id else requests.get(request_id)
            rows = [_request_payload(req, include_artifacts=True)] if req else []
        else:
            rows = []
            for req in requests.list_all():
                if override and req["id"] == override["id"]:
                    req = override
                rows.append(_request_payload(req))
        return {"requests": rows}

    def _sse(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

    @app.get("/health")
    async def health():
        """Backward-compatible liveness alias."""
        return {"status": "ok", "app": "caliper", "version": "0.1.0"}

    @app.get("/health/live")
    async def health_live():
        return {"status": "ok", "check": "live", "app": "caliper", "version": "0.1.0"}

    @app.get("/health/ready")
    async def health_ready():
        checks: dict[str, dict] = {}
        try:
            requests.ping()
            checks["database"] = {"ok": True, "detail": "SQLite responds to SELECT 1."}
        except Exception as exc:
            checks["database"] = {"ok": False, "detail": f"SQLite is not reachable: {exc}"}
        writable, message = check_writable(config.data_dir)
        checks["data_dir"] = {"ok": writable, "detail": message}
        if secret_store.unlocked:
            checks["secret_store"] = {"ok": True, "detail": "Secret store is unlocked."}
        else:
            checks["secret_store"] = {
                "ok": False,
                "detail": f"Secret store is locked: {secret_store.locked_reason}",
            }
        ready = all(item["ok"] for item in checks.values()) and app.state.startup_done
        if not app.state.startup_done:
            checks["startup"] = {"ok": False, "detail": "Startup checks have not completed."}
            ready = False
        status_code = 200 if ready else 503
        return JSONResponse(
            {"status": "ready" if ready else "not_ready", "checks": checks},
            status_code=status_code,
        )

    def _read_settings_form(form):
        entries = {
            role: {
                "provider": str(form.get(f"{role}_provider", "http")).strip(),
                "base_url": str(form.get(f"{role}_base_url", "")),
                "model": str(form.get(f"{role}_model", "")),
            }
            for role in ROLE_ORDER
        }
        keys = {role: str(form.get(f"{role}_api_key", "")) for role in ROLE_ORDER}
        clear = {role: form.get(f"{role}_clear_key") == "on" for role in ROLE_ORDER}
        action = str(form.get("action", "save"))
        return entries, keys, clear, action

    @app.get("/settings/models", response_class=HTMLResponse)
    async def settings_models_page(request: Request, saved: int = 0):
        return templates.TemplateResponse(
            request,
            "settings_models.html",
            _ui_ctx(models=settings.status(), values={}, saved=bool(saved), error=None, test_result=None),
        )

    @app.post("/settings/models")
    async def settings_models_save(request: Request):
        nonlocal workflow
        form = await request.form()
        entries, keys, clear, _ = _read_settings_form(form)
        try:
            settings.save(entries, keys, clear)
        except ModelsConfigError as exc:
            return templates.TemplateResponse(
                request,
                "settings_models.html",
                _ui_ctx(models=settings.status(entries), values=entries, saved=False, error=str(exc), test_result=None),
                status_code=422,
            )
        if not workflow_injected:
            workflow = build_workflow()
        return RedirectResponse("/settings/models?saved=1", status_code=303)

    @app.post("/settings/models/test")
    async def settings_models_test(request: Request):
        form = await request.form()
        entries, keys, _, action = _read_settings_form(form)
        role = action.split(":", 1)[1] if action.startswith("test:") else ""
        if role not in ROLE_ORDER:
            return RedirectResponse("/settings/models", status_code=303)
        entry = entries[role]
        key = keys[role] or settings.api_key(role)
        try:
            reply = probe_model(
                role, entry["base_url"], entry["model"], key, provider=entry["provider"]
            )
            result = {"role": role, "ok": True, "message": f"Connected. Reply: {reply[:120]}"}
        except Exception as exc:
            result = {"role": role, "ok": False, "message": str(exc)}
        return templates.TemplateResponse(
            request,
            "settings_models.html",
            _ui_ctx(models=settings.status(entries), values=entries, saved=False, error=None, test_result=result),
        )

    @app.get("/projects", response_class=HTMLResponse)
    async def project_list(request: Request):
        return templates.TemplateResponse(request, "project_list.html", _ui_ctx())

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        needs, running, recent = [], [], []
        for req in requests.list_all():
            if req["status"] == "awaiting_approval":
                needs.append(req)
            elif req["status"] in ("submitted", "planning", "executing", "reviewing", "merged", "deployed_dev"):
                running.append(req)
            elif req["status"] in ("live", "failed", "interrupted", "cancelled"):
                recent.append(req)
        return templates.TemplateResponse(request, "dashboard.html", _ui_ctx(needs=needs, running=running, recent=recent))

    @app.get("/status")
    async def status_snapshot():
        rows = []
        for req in requests.list_all():
            counts = {"pass": 0, "fail": 0}
            review_file = workflow.artifact_dir(req["id"]) / "review.json"
            if review_file.is_file():
                try:
                    verdicts = json.loads(review_file.read_text(encoding="utf-8"))["verdicts"]
                    for v in verdicts.values():
                        counts[v["status"]] = counts.get(v["status"], 0) + 1
                except Exception:
                    pass
            rows.append({"id": req["id"], "status": req["status"], "fail": counts["fail"], "total": counts["fail"] + counts["pass"]})
        return {"requests": rows}

    @app.get("/requests/new", response_class=HTMLResponse)
    async def request_new_global(request: Request):
        projects = registry.list_projects()
        if not projects:
            return templates.TemplateResponse(request, "request_first_project.html", _ui_ctx())
        return templates.TemplateResponse(
            request,
            "request_new.html",
            _ui_ctx(error=None, values={}, form_action="/requests/new", project=None),
        )

    @app.post("/requests/new")
    async def request_new_global_submit(request: Request, project_id: str = Form(""), kind: str = Form("feature"), text: str = Form(""), extra: str = Form("")):
        try:
            manifest = registry.get_project(project_id)
            missing = registry.setup_missing(manifest)
            if missing:
                return templates.TemplateResponse(
                    request, "request_blocked.html", _ui_ctx(project=manifest, missing=missing), status_code=409
                )
            req = requests.create(project_id=project_id, kind=kind if kind in VALID_KINDS else "feature", text=text, extra=extra or None)
        except ValueError as exc:
            context = _ui_ctx(error=str(exc), values={"kind": kind, "text": text, "extra": extra, "project_id": project_id}, form_action="/requests/new", project=None)
            return templates.TemplateResponse(request, "request_new.html", context, status_code=422)
        except ProjectError:
            return templates.TemplateResponse(request, "project_missing.html", _ui_ctx(project_id=project_id), status_code=404)
        return RedirectResponse(f"/requests/{req['id']}", status_code=303)

    @app.get("/requests/{req_id}", response_class=HTMLResponse)
    async def request_detail(request: Request, req_id: str):
        req = requests.get(req_id)
        if req is None:
            return templates.TemplateResponse(request, "request_missing.html", _ui_ctx(req_id=req_id), status_code=404)
        artifacts = {}
        artifacts_dir = workflow.artifact_dir(req_id)
        for name, key in (
            ("plan.md", "plan"),
            ("diff.patch", "diff"),
            ("review.json", "review"),
            ("test-output.txt", "tests"),
            ("merge-commit.txt", "merge"),
        ):
            path = artifacts_dir / name
            if path.is_file():
                artifacts[key] = path.read_text(encoding="utf-8")
        failed, passed, verdicts = [], [], {}
        if "review" in artifacts:
            verdicts = json.loads(artifacts["review"])["verdicts"]
            failed = [rid for rid, v in verdicts.items() if v["status"] == "fail"]
            passed = [rid for rid, v in verdicts.items() if v["status"] == "pass"]
            artifacts.pop("review")
        review_error_path = artifacts_dir / "review-error.txt"
        review_error = (
            review_error_path.read_text(encoding="utf-8").strip()
            if review_error_path.is_file() else None
        )
        test_status_path = artifacts_dir / "test-status.txt"
        test_status = (
            test_status_path.read_text(encoding="utf-8").strip()
            if test_status_path.is_file() else None
        )
        return templates.TemplateResponse(request, "request_detail.html", _ui_ctx(
            req=req, artifacts=artifacts, failed=failed, passed=passed, verdicts=verdicts,
            review_error=review_error, test_status=test_status,
            delete_error=request.query_params.get("delete_error"),
        ))

    def _run_request(req_id: str):
        lifecycle.begin(req_id)
        try:
            return workflow.run(req_id)
        finally:
            lifecycle.end(req_id)

    @app.post("/requests/{req_id}/run")
    async def request_run(request: Request, req_id: str):
        try:
            if not lifecycle.accepting_runs:
                raise ShuttingDown(
                    "Caliper is shutting down and is not accepting new runs. "
                    "Start it again, then run the request."
                )
            await asyncio.to_thread(_run_request, req_id)
        except Exception as exc:
            return templates.TemplateResponse(request, "request_run_error.html", _ui_ctx(req_id=req_id, reason=str(exc)), status_code=422)
        return RedirectResponse(f"/requests/{req_id}", status_code=303)

    @app.post("/requests/{req_id}/approve")
    async def request_approve(request: Request, req_id: str):
        try:
            workflow.approve_merge(req_id)
        except Exception as exc:
            return templates.TemplateResponse(request, "request_run_error.html", _ui_ctx(req_id=req_id, reason=str(exc)), status_code=422)
        return RedirectResponse(f"/requests/{req_id}", status_code=303)

    @app.post("/requests/{req_id}/changes")
    async def request_changes(request: Request, req_id: str, note: str = Form("")):
        try:
            workflow.request_changes(req_id, note)
        except Exception as exc:
            return templates.TemplateResponse(request, "request_run_error.html", _ui_ctx(req_id=req_id, reason=str(exc)), status_code=422)
        return RedirectResponse(f"/requests/{req_id}", status_code=303)

    @app.post("/requests/{req_id}/prod")
    async def request_prod(request: Request, req_id: str):
        try:
            workflow.approve_prod(req_id)
        except Exception as exc:
            return templates.TemplateResponse(request, "request_run_error.html", _ui_ctx(req_id=req_id, reason=str(exc)), status_code=422)
        return RedirectResponse(f"/requests/{req_id}", status_code=303)

    @app.post("/requests/{req_id}/delete")
    async def request_delete_form(request: Request, req_id: str):
        req = requests.get(req_id)
        if req is None:
            return templates.TemplateResponse(request, "request_missing.html", _ui_ctx(req_id=req_id), status_code=404)
        try:
            _delete_request(req)
        except RequestDeleteError as exc:
            return RedirectResponse(f"/requests/{req_id}?delete_error={quote(str(exc))}", status_code=303)
        return RedirectResponse(f"/projects/{req['project_id']}#requests", status_code=303)

    @app.delete("/api/requests/{req_id}")
    async def request_delete_api(req_id: str):
        req = requests.get(req_id)
        if req is None:
            return JSONResponse({"error": f"Request '{req_id}' does not exist."}, status_code=404)
        try:
            _delete_request(req)
        except RequestDeleteError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return {"deleted": True, "id": req_id, "project_id": req["project_id"]}

    @app.get("/api/events")
    async def state_events(request: Request):
        request_id = request.query_params.get("request")
        once = request.query_params.get("once") == "1"
        subscriber = bus.subscribe()

        async def stream():
            try:
                yield _sse("snapshot", _state_payload(request_id))
                if once:
                    return
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.to_thread(subscriber.get, True, 5.0)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    yield _sse("update", _state_payload(request_id, event.get("request")))
            finally:
                bus.unsubscribe(subscriber)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/projects/new", response_class=HTMLResponse)
    async def project_new(request: Request):
        return templates.TemplateResponse(request, "project_new.html", _ui_ctx(error=None, values={}))

    @app.post("/projects/new")
    async def project_create(request: Request, name: str = Form(""), repo_url: str = Form("")):
        try:
            manifest = registry.create_project(name=name, repo_url=repo_url or None)
        except ProjectError as exc:
            context = _ui_ctx(error=str(exc), values={"name": name, "repo_url": repo_url})
            return templates.TemplateResponse(request, "project_new.html", context, status_code=422)
        return RedirectResponse(f"/projects/{manifest['id']}", status_code=303)

    def _render_project(request: Request, project_id: str, error: str | None = None, status_code: int = 200):
        try:
            manifest = registry.get_project(project_id)
        except ProjectError:
            return templates.TemplateResponse(request, "project_missing.html", _ui_ctx(project_id=project_id), status_code=404)
        view = request.query_params.get("view")
        if view not in ("principles", "manifest", "decisions", "graph"):
            view = "principles"
        setup = registry.setup_status(manifest)
        docs = project_docs.read_cached(project_id)
        project_requests = requests.list_for_project(project_id)
        stop_ready = {
            env: all(manifest["environments"][env].get(key) for key in (
                "host", "user", "deploy_path", "compose_file",
            )) and registry.environment_key_exists(project_id, env)
            for env in ("dev", "prod")
        }
        graph_data = None
        graph_warnings = []
        if view == "graph" and docs.get("graph") is not None:
            graph_view = docs["graph_view"]
            graph_warnings = ([docs["graph_error"]] if docs.get("graph_error") else []) + [
                warning for warning in (graph_view.get("warnings") or [])
            ]
            graph_data = json.dumps({
                "nodes": graph_view["nodes"],
                "edges": graph_view["edges"],
                "warnings": graph_warnings,
                "counts": graph_view.get("counts") or {},
            }).replace("</", "<\\/")
        return templates.TemplateResponse(
            request,
            "project_detail.html",
            _ui_ctx(
                project=manifest,
                requests=project_requests,
                request_count=len(project_requests),
                active_requests=[req for req in project_requests if req["status"] in ACTIVE_STATUSES],
                setup=setup,
                setup_missing=[item["label"] for item in setup if not item["ready"]],
                setup_ready=all(item["ready"] for item in setup),
                setup_error=error,
                docs=docs,
                docs_error=request.query_params.get("docs_error"),
                docs_message=request.query_params.get("docs_message"),
                stop_ready=stop_ready,
                view=view,
                graph_data=graph_data,
                graph_warnings=graph_warnings,
                workspace_manifest=manifest_workspace(docs.get("manifest") or {}),
                principle_rules={
                    principle["key"]: parse_rules(principle.get("content") or "")
                    for principle in manifest["principles"]
                },
            ),
            status_code=status_code,
        )

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    async def project_detail(request: Request, project_id: str):
        return _render_project(request, project_id)

    @app.delete("/api/projects/{project_id}")
    async def project_delete_api(project_id: str, stop_dev: bool = False, stop_prod: bool = False):
        try:
            return project_deleter.delete(project_id, stop_dev=stop_dev, stop_prod=stop_prod)
        except ProjectDeleteError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/projects/{project_id}/repository/key")
    async def repository_key_form(request: Request, project_id: str):
        try:
            registry.ensure_deploy_key(project_id)
        except ProjectError as exc:
            return _render_project(request, project_id, error=str(exc), status_code=422)
        return RedirectResponse(f"/projects/{project_id}#repository", status_code=303)

    @app.post("/projects/{project_id}/repository/test")
    async def repository_test_form(request: Request, project_id: str):
        try:
            ok, message = registry.test_repository_access(project_id)
        except ProjectError as exc:
            return _render_project(request, project_id, error=str(exc), status_code=422)
        if not ok:
            return _render_project(request, project_id, error=message, status_code=422)
        return RedirectResponse(f"/projects/{project_id}#repository", status_code=303)

    @app.post("/projects/{project_id}/environments/{env}")
    async def environment_save_form(
        request: Request,
        project_id: str,
        env: str,
        host: str = Form(""),
        user: str = Form(""),
        deploy_path: str = Form(""),
        compose_file: str = Form("docker-compose.yml"),
        health_url: str = Form(""),
        action: str = Form("save"),
    ):
        try:
            registry.save_environment(project_id, env, {
                "host": host, "user": user, "deploy_path": deploy_path,
                "compose_file": compose_file, "health_url": health_url,
            })
            if action == "test":
                ok, message = registry.test_environment_access(project_id, env)
                if not ok:
                    return _render_project(request, project_id, error=message, status_code=422)
        except ProjectError as exc:
            return _render_project(request, project_id, error=str(exc), status_code=422)
        return RedirectResponse(f"/projects/{project_id}#environment-{env}", status_code=303)

    @app.post("/projects/{project_id}/principles/{key}")
    async def principle_status_form(request: Request, project_id: str, key: str, action: str = Form("approve")):
        try:
            if action == "approve":
                principles.approve(project_id, key)
            else:
                principles.unapprove(project_id, key)
        except PrinciplesError as exc:
            return _render_project(request, project_id, error=str(exc), status_code=422)
        except ProjectError as exc:
            return _render_project(request, project_id, error=str(exc), status_code=422)
        return RedirectResponse(f"/projects/{project_id}#principles", status_code=303)

    @app.get("/projects/{project_id}/requests/new", response_class=HTMLResponse)
    async def request_new(request: Request, project_id: str):
        try:
            manifest = registry.get_project(project_id)
        except ProjectError:
            return templates.TemplateResponse(request, "project_missing.html", _ui_ctx(project_id=project_id), status_code=404)
        missing = registry.setup_missing(manifest)
        if missing:
            return templates.TemplateResponse(
                request, "request_blocked.html", _ui_ctx(project=manifest, missing=missing), status_code=409
            )
        return templates.TemplateResponse(
            request,
            "request_new.html",
            _ui_ctx(project=manifest, form_action=f"/projects/{project_id}/requests/new", error=None, values={}),
        )

    @app.post("/projects/{project_id}/requests/new")
    async def request_create(request: Request, project_id: str, kind: str = Form("feature"), text: str = Form(""), extra: str = Form("")):
        try:
            manifest = registry.get_project(project_id)
            missing = registry.setup_missing(manifest)
            if missing:
                return templates.TemplateResponse(
                    request, "request_blocked.html", _ui_ctx(project=manifest, missing=missing), status_code=409
                )
            req = requests.create(project_id=project_id, kind=kind if kind in VALID_KINDS else "feature", text=text, extra=extra or None)
        except ProjectError:
            return templates.TemplateResponse(request, "project_missing.html", _ui_ctx(project_id=project_id), status_code=404)
        except ValueError as exc:
            context = _ui_ctx(project=manifest, form_action=f"/projects/{project_id}/requests/new", error=str(exc), values={"kind": kind, "text": text, "extra": extra})
            return templates.TemplateResponse(request, "request_new.html", context, status_code=422)
        return RedirectResponse(f"/requests/{req['id']}", status_code=303)

    # --- Repository access (AGENTS.md 12.3) ----------------------------------------

    @app.get("/api/projects/{project_id}/repository")
    async def repository_status(project_id: str):
        try:
            return registry.repository_status(project_id)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/projects/{project_id}/repository/key")
    async def repository_key(project_id: str):
        try:
            registry.ensure_deploy_key(project_id)
            return registry.repository_status(project_id)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.post("/api/projects/{project_id}/repository/test")
    async def repository_test(project_id: str):
        try:
            ok, message = registry.test_repository_access(project_id)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        if not ok:
            return JSONResponse(
                {"error": message, "repository": registry.repository_status(project_id)},
                status_code=422,
            )
        return {"ok": True, "message": message, "repository": registry.repository_status(project_id)}

    @app.post("/api/projects/{project_id}/repository/key/replace")
    async def repository_key_replace(project_id: str):
        """Stage a replacement key pair. The active key keeps working until activation."""
        try:
            registry.generate_replacement_deploy_key(project_id)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return {"ok": True, "repository": registry.repository_status(project_id)}

    @app.post("/api/projects/{project_id}/repository/key/activate")
    async def repository_key_activate(project_id: str):
        """Verify the staged replacement against the remote, then switch the active key."""
        try:
            ok, message = registry.activate_replacement_deploy_key(project_id)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        status = registry.repository_status(project_id)
        if not ok:
            return JSONResponse(
                {"error": message, "repository": status},
                status_code=422,
            )
        return {"ok": True, "message": message, "repository": status}

    @app.post("/api/projects/{project_id}/repository/key/discard")
    async def repository_key_discard(project_id: str):
        """Drop the staged replacement; the current key stays active."""
        try:
            registry.discard_replacement_deploy_key(project_id)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return {"ok": True, "repository": registry.repository_status(project_id)}

    @app.delete("/api/projects/{project_id}/repository/key")
    async def repository_key_remove(project_id: str):
        """Remove the repository credentials. The project and its data stay."""
        try:
            message = registry.remove_deploy_key(project_id)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return {"ok": True, "message": message, "repository": registry.repository_status(project_id)}

    @app.post("/projects/{project_id}/repository/key/replace")
    async def repository_key_replace_form(request: Request, project_id: str):
        try:
            registry.generate_replacement_deploy_key(project_id)
        except ProjectError as exc:
            return _render_project(request, project_id, error=str(exc), status_code=422)
        return RedirectResponse(f"/projects/{project_id}?view=principles#repository", status_code=303)

    @app.post("/projects/{project_id}/repository/key/activate")
    async def repository_key_activate_form(request: Request, project_id: str):
        try:
            ok, message = registry.activate_replacement_deploy_key(project_id)
        except ProjectError as exc:
            return _render_project(request, project_id, error=str(exc), status_code=422)
        if not ok:
            return _render_project(request, project_id, error=message, status_code=422)
        return RedirectResponse(f"/projects/{project_id}#repository", status_code=303)

    @app.post("/projects/{project_id}/repository/key/discard")
    async def repository_key_discard_form(request: Request, project_id: str):
        try:
            registry.discard_replacement_deploy_key(project_id)
        except ProjectError as exc:
            return _render_project(request, project_id, error=str(exc), status_code=422)
        return RedirectResponse(f"/projects/{project_id}#repository", status_code=303)

    @app.post("/projects/{project_id}/repository/remove")
    async def repository_key_remove_form(request: Request, project_id: str):
        try:
            registry.remove_deploy_key(project_id)
        except ProjectError as exc:
            return _render_project(request, project_id, error=str(exc), status_code=422)
        return RedirectResponse(f"/projects/{project_id}#repository", status_code=303)

    # --- Principles (AGENTS.md 12.4 / 12.8) ----------------------------------------

    @app.get("/api/projects/{project_id}/principles")
    async def principles_list(project_id: str):
        try:
            return principles.list_principles(project_id)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/projects/{project_id}/principles/sync")
    async def principles_sync(project_id: str):
        try:
            return principles.sync(project_id)
        except PrinciplesError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.get("/api/projects/{project_id}/principles/{key}")
    async def principle_read(project_id: str, key: str):
        try:
            return principles.read_principle(project_id, key)
        except PrinciplesError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.put("/api/projects/{project_id}/principles/{key}")
    async def principle_write(request: Request, project_id: str, key: str):
        content = None
        body = await request.body()
        if body:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return JSONResponse(
                    {"error": "The request body must be JSON with a 'content' field."},
                    status_code=400,
                )
            if not isinstance(data, dict):
                return JSONResponse({"error": "The request body must be a JSON object."}, status_code=400)
            content = data.get("content")
            if content is not None and not isinstance(content, str):
                return JSONResponse({"error": "'content' must be a string."}, status_code=400)
        try:
            return principles.put(project_id, key, content)
        except PrinciplesError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/projects/{project_id}/principles/{key}/approve")
    async def principle_approve(project_id: str, key: str):
        try:
            return principles.approve(project_id, key)
        except PrinciplesError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/projects/{project_id}/principles/{key}/unapprove")
    async def principle_unapprove(project_id: str, key: str):
        try:
            return principles.unapprove(project_id, key)
        except PrinciplesError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    # --- Project docs (AGENTS.md 7b) ------------------------------------------------

    @app.get("/api/projects/{project_id}/docs")
    async def project_docs_read(project_id: str):
        try:
            return project_docs.read_cached(project_id)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/projects/{project_id}/docs/sync")
    async def project_docs_sync(project_id: str):
        try:
            return project_docs.sync(project_id)
        except ProjectDocsError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/projects/{project_id}/docs/init")
    async def project_docs_init(project_id: str):
        try:
            return project_docs.ensure_scaffold(project_id)
        except ProjectDocsError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/projects/{project_id}/decisions")
    async def project_decision_create(request: Request, project_id: str):
        body = await request.body()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return JSONResponse(
                {"error": "The request body must be JSON with title, decision and reason."},
                status_code=400,
            )
        if not isinstance(data, dict):
            return JSONResponse({"error": "The request body must be a JSON object."}, status_code=400)
        values = {}
        for field in ("title", "decision", "reason", "alternatives"):
            value = data.get(field, "")
            if not isinstance(value, str):
                return JSONResponse({"error": f"'{field}' must be a string."}, status_code=400)
            values[field] = value
        try:
            return project_docs.append_decision(
                project_id,
                title=values["title"],
                decision=values["decision"],
                reason=values["reason"],
                alternatives=values["alternatives"],
            )
        except ProjectDocsError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    def _docs_redirect(project_id: str, view: str, message_name: str, message: str) -> str:
        base = f"/projects/{project_id}"
        if view in ("principles", "manifest", "decisions", "graph"):
            separator = "&"
            base += f"?view={view}"
        else:
            separator = "?"
        return f"{base}{separator}{message_name}={quote(message)}#docs"

    @app.post("/projects/{project_id}/docs/sync")
    async def project_docs_sync_form(request: Request, project_id: str, view: str = Form("")):
        try:
            project_docs.sync(project_id)
        except (ProjectDocsError, ProjectError) as exc:
            return RedirectResponse(
                _docs_redirect(project_id, view, "docs_error", str(exc)), status_code=303
            )
        message = "Project docs synced from the repository."
        return RedirectResponse(
            _docs_redirect(project_id, view, "docs_message", message), status_code=303
        )

    @app.post("/projects/{project_id}/docs/init")
    async def project_docs_init_form(request: Request, project_id: str, view: str = Form("")):
        try:
            result = project_docs.ensure_scaffold(project_id)
        except (ProjectDocsError, ProjectError) as exc:
            return RedirectResponse(
                _docs_redirect(project_id, view, "docs_error", str(exc)), status_code=303
            )
        message = (
            "Created the missing project docs in the repository."
            if result.get("initialized")
            else "Project docs already exist; nothing was written."
        )
        return RedirectResponse(
            _docs_redirect(project_id, view, "docs_message", message), status_code=303
        )

    @app.post("/projects/{project_id}/decisions")
    async def project_decision_form(
        request: Request,
        project_id: str,
        title: str = Form(""),
        decision: str = Form(""),
        reason: str = Form(""),
        alternatives: str = Form(""),
        view: str = Form(""),
    ):
        try:
            result = project_docs.append_decision(
                project_id, title=title, decision=decision, reason=reason, alternatives=alternatives
            )
        except (ProjectDocsError, ProjectError) as exc:
            return RedirectResponse(
                _docs_redirect(project_id, view, "docs_error", str(exc)), status_code=303
            )
        message = f"Added {result['written']}."
        return RedirectResponse(
            _docs_redirect(project_id, view, "docs_message", message), status_code=303
        )

    # --- Environments (AGENTS.md 12.5 / 12.8) ---------------------------------------

    @app.get("/api/projects/{project_id}/environments")
    async def environments_list(project_id: str):
        try:
            return {
                "project_id": project_id,
                "environments": [
                    registry.environment_status(project_id, env) for env in ENVIRONMENT_ORDER
                ],
            }
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.put("/api/projects/{project_id}/environments/{env}")
    async def environment_save(request: Request, project_id: str, env: str):
        body = await request.body()
        fields = {}
        if body:
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return JSONResponse(
                    {"error": "The request body must be JSON with the environment fields."},
                    status_code=400,
                )
            if not isinstance(data, dict):
                return JSONResponse({"error": "The request body must be a JSON object."}, status_code=400)
            fields = data
        try:
            registry.save_environment(project_id, env, fields)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return registry.environment_status(project_id, env)

    @app.post("/api/projects/{project_id}/environments/{env}/key")
    async def environment_key(project_id: str, env: str):
        try:
            registry.ensure_environment_key(project_id, env)
            return registry.environment_status(project_id, env)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.post("/api/projects/{project_id}/environments/{env}/test")
    async def environment_test(project_id: str, env: str):
        try:
            ok, message = registry.test_environment_access(project_id, env)
        except ProjectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        status = registry.environment_status(project_id, env)
        if not ok:
            return JSONResponse({"error": message, "environment": status}, status_code=422)
        return {"ok": True, "message": message, "environment": status}

    return app


def run() -> None:
    config = CaliperConfig.from_env()
    configure_logging(config.log_level)
    try:
        application = create_app(config=config)
        application.state.startup()
    except (ConfigError, MigrationError) as exc:
        logger.error("Caliper did not start: %s", exc)
        raise SystemExit(2) from exc
    logger.info(
        "Caliper starting on %s:%s with data at %s", config.host, config.port, config.data_dir
    )
    uvicorn.run(
        application,
        host=config.host,
        port=config.port,
        log_config=None,
        timeout_graceful_shutdown=max(1, int(config.shutdown_grace_seconds)),
    )
