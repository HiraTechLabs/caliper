import json
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from caliper.principles import PrinciplesError, PrinciplesService
from caliper.registry import ENVIRONMENT_ORDER, STACK_PRESETS, ProjectError, ProjectRegistry
from caliper.state import VALID_KINDS, RequestStore

BASE_DIR = Path(__file__).resolve().parent


def create_app(registry: ProjectRegistry | None = None, requests: RequestStore | None = None,
               workflow=None, principles: PrinciplesService | None = None) -> FastAPI:
    if registry is None:
        projects_root = os.environ.get("CALIPER_PROJECTS_ROOT", str(BASE_DIR.parent / "projects"))
        registry = ProjectRegistry(Path(projects_root))
    if requests is None:
        db_path = os.environ.get("CALIPER_DB_PATH", str(BASE_DIR.parent / ".caliper-state" / "state.sqlite3"))
        requests = RequestStore(db_path)
    if principles is None:
        principles = PrinciplesService(registry)

    from caliper.models_config import load_models_config, make_adapters
    from caliper.workflow import Workflow
    models_config_path = os.environ.get("CALIPER_MODELS_CONFIG", str(BASE_DIR / "models.yaml"))
    config = load_models_config(models_config_path)
    adapters = make_adapters(config)
    artifacts_root_override = os.environ.get("CALIPER_ARTIFACTS_ROOT")
    artifacts_root = Path(artifacts_root_override) if artifacts_root_override else Path(registry.projects_root)
    if workflow is None:
        workflow = Workflow(requests, registry, artifacts_root, planning=adapters["planning"], execution=adapters["execution"], review=adapters["review"])

    app = FastAPI(title="Caliper", version="0.1.0")
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")

    def _ui_ctx(**extra):
        projects = registry.list_projects()
        for project in projects:
            project["stack_label"] = STACK_PRESETS.get(project["stack_preset"], project["stack_preset"])
        return {"projects": projects, **extra}

    @app.get("/health")
    async def health():
        return {"status": "ok", "app": "caliper", "version": "0.1.0"}

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
            elif req["status"] in ("live", "failed"):
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
        if not registry.list_projects():
            return templates.TemplateResponse(request, "request_first_project.html", _ui_ctx())
        return templates.TemplateResponse(request, "request_new.html", _ui_ctx(error=None, values={}))

    @app.post("/requests/new")
    async def request_new_global_submit(request: Request, project_id: str = Form(""), kind: str = Form("feature"), text: str = Form(""), extra: str = Form("")):
        try:
            req = requests.create(project_id=project_id, kind=kind if kind in VALID_KINDS else "feature", text=text, extra=extra or None)
        except ValueError as exc:
            context = _ui_ctx(error=str(exc), values={"kind": kind, "text": text, "extra": extra, "project_id": project_id})
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
        for name, key in (("plan.md", "plan"), ("diff.patch", "diff"), ("review.json", "review")):
            path = artifacts_dir / name
            if path.is_file():
                artifacts[key] = path.read_text(encoding="utf-8")
        failed, passed, verdicts = [], [], {}
        if "review" in artifacts:
            verdicts = json.loads(artifacts["review"])["verdicts"]
            failed = [rid for rid, v in verdicts.items() if v["status"] == "fail"]
            passed = [rid for rid, v in verdicts.items() if v["status"] == "pass"]
            artifacts.pop("review")
        return templates.TemplateResponse(request, "request_detail.html", {
            "req": req, "artifacts": artifacts, "failed": failed, "passed": passed, "verdicts": verdicts,
        })

    @app.post("/requests/{req_id}/run")
    async def request_run(request: Request, req_id: str):
        try:
            workflow.run(req_id)
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

    @app.get("/projects/new", response_class=HTMLResponse)
    async def project_new(request: Request):
        return templates.TemplateResponse(request, "project_new.html", _ui_ctx(presets=STACK_PRESETS, error=None, values={}))

    @app.post("/projects/new")
    async def project_create(request: Request, name: str = Form(""), repo_mode: str = Form("create_new"), repo_url: str = Form(""), stack_preset: str = Form("python-fastapi")):
        try:
            manifest = registry.create_project(
                name=name, repo_mode=repo_mode, repo_url=repo_url or None, stack_preset=stack_preset,
            )
        except ProjectError as exc:
            context = _ui_ctx(presets=STACK_PRESETS, error=str(exc), values={"name": name, "repo_url": repo_url, "repo_mode": repo_mode, "stack_preset": stack_preset})
            return templates.TemplateResponse(request, "project_new.html", context, status_code=422)
        return RedirectResponse(f"/projects/{manifest['id']}", status_code=303)

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    async def project_detail(request: Request, project_id: str):
        try:
            manifest = registry.get_project(project_id)
        except ProjectError:
            return templates.TemplateResponse(request, "project_missing.html", _ui_ctx(project_id=project_id), status_code=404)
        manifest["stack_label"] = STACK_PRESETS.get(manifest["stack_preset"], manifest["stack_preset"])
        return templates.TemplateResponse(request, "project_detail.html", _ui_ctx(
            project=manifest,
            requests=requests.list_for_project(project_id),
        ))

    @app.get("/projects/{project_id}/requests/new", response_class=HTMLResponse)
    async def request_new(request: Request, project_id: str):
        try:
            manifest = registry.get_project(project_id)
        except ProjectError:
            return templates.TemplateResponse(request, "project_missing.html", _ui_ctx(project_id=project_id), status_code=404)
        return templates.TemplateResponse(request, "request_new.html", _ui_ctx(project=manifest, error=None, values={}))

    @app.post("/projects/{project_id}/requests/new")
    async def request_create(request: Request, project_id: str, kind: str = Form("feature"), text: str = Form(""), extra: str = Form("")):
        try:
            manifest = registry.get_project(project_id)
            req = requests.create(project_id=project_id, kind=kind if kind in VALID_KINDS else "feature", text=text, extra=extra or None)
        except ProjectError:
            return templates.TemplateResponse(request, "project_missing.html", _ui_ctx(project_id=project_id), status_code=404)
        except ValueError as exc:
            context = _ui_ctx(project=manifest, error=str(exc), values={"kind": kind, "text": text, "extra": extra})
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


app = create_app()


def run() -> None:
    host = os.environ.get("CALIPER_HOST", "127.0.0.1")
    port = int(os.environ.get("CALIPER_PORT", "8080"))
    uvicorn.run(app, host=host, port=port)
