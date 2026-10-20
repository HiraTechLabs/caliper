import json
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from caliper.registry import STACK_PRESETS, ProjectError, ProjectRegistry
from caliper.state import VALID_KINDS, RequestStore


BASE_DIR = Path(__file__).resolve().parent


def create_app(registry: ProjectRegistry | None = None, requests: RequestStore | None = None, workflow=None) -> FastAPI:
    if registry is None:
        projects_root = os.environ.get("CALIPER_PROJECTS_ROOT", str(BASE_DIR.parent / "projects"))
        registry = ProjectRegistry(Path(projects_root))
    if requests is None:
        db_path = os.environ.get("CALIPER_DB_PATH", str(BASE_DIR.parent / ".caliper-state" / "state.sqlite3"))
        requests = RequestStore(db_path)
    from caliper.workflow import Workflow
    artifacts_root = Path(os.environ.get("CALIPER_PROJECTS_ROOT", str(BASE_DIR.parent / "projects")))
    if workflow is None:
        workflow = Workflow(requests, registry, artifacts_root)
    app = FastAPI(title="Caliper", version="0.1.0")
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")

    @app.get("/health")
    async def health():
        return {"status": "ok", "app": "caliper", "version": "0.1.0"}

    @app.get("/", response_class=HTMLResponse)
    async def project_list(request: Request):
        projects = registry.list_projects()
        for project in projects:
            project["stack_label"] = STACK_PRESETS.get(project["stack_preset"], project["stack_preset"])
        return templates.TemplateResponse(request, "project_list.html", {"projects": projects})

    @app.get("/projects/new", response_class=HTMLResponse)
    async def project_new(request: Request):
        return templates.TemplateResponse(request, "project_new.html", {
            "presets": STACK_PRESETS,
            "error": None,
            "values": {},
        })

    @app.post("/projects/new")
    async def project_create(request: Request, name: str = Form(""), repo_mode: str = Form("create_new"), repo_url: str = Form(""), stack_preset: str = Form("python-fastapi")):
        try:
            manifest = registry.create_project(
                name=name, repo_mode=repo_mode, repo_url=repo_url or None, stack_preset=stack_preset,
            )
        except ProjectError as exc:
            context = {"presets": STACK_PRESETS, "error": str(exc), "values": {"name": name, "repo_url": repo_url, "repo_mode": repo_mode, "stack_preset": stack_preset}}
            return templates.TemplateResponse(request, "project_new.html", context, status_code=422)
        return RedirectResponse(f"/projects/{manifest['id']}", status_code=303)

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    async def project_detail(request: Request, project_id: str):
        try:
            manifest = registry.get_project(project_id)
        except ProjectError:
            return templates.TemplateResponse(request, "project_missing.html", {"project_id": project_id}, status_code=404)
        manifest["stack_label"] = STACK_PRESETS.get(manifest["stack_preset"], manifest["stack_preset"])
        return templates.TemplateResponse(request, "project_detail.html", {
            "project": manifest,
            "requests": requests.list_for_project(project_id),
        })

    @app.get("/projects/{project_id}/requests/new", response_class=HTMLResponse)
    async def request_new(request: Request, project_id: str):
        try:
            manifest = registry.get_project(project_id)
        except ProjectError:
            return templates.TemplateResponse(request, "project_missing.html", {"project_id": project_id}, status_code=404)
        return templates.TemplateResponse(request, "request_new.html", {"project": manifest, "error": None, "values": {}})

    @app.post("/projects/{project_id}/requests/new")
    async def request_create(request: Request, project_id: str, kind: str = Form("feature"), text: str = Form(""), extra: str = Form("")):
        try:
            manifest = registry.get_project(project_id)
            req = requests.create(project_id=project_id, kind=kind if kind in VALID_KINDS else "feature", text=text, extra=extra or None)
        except ProjectError:
            return templates.TemplateResponse(request, "project_missing.html", {"project_id": project_id}, status_code=404)
        except ValueError as exc:
            context = {"project": manifest, "error": str(exc), "values": {"kind": kind, "text": text, "extra": extra}}
            return templates.TemplateResponse(request, "request_new.html", context, status_code=422)
        return RedirectResponse(f"/requests/{req['id']}", status_code=303)

    @app.get("/requests/{req_id}", response_class=HTMLResponse)
    async def request_detail(request: Request, req_id: str):
        req = requests.get(req_id)
        if req is None:
            return templates.TemplateResponse(request, "request_missing.html", {"req_id": req_id}, status_code=404)
        artifacts = {}
        artifacts_dir = workflow.artifact_dir(req_id)
        for name, key in (("plan.md", "plan"), ("diff.patch", "diff"), ("review.json", "review")):
            path = artifacts_dir / name
            if path.is_file():
                artifacts[key] = path.read_text(encoding="utf-8")
        failed, passed, verdicts = [], [], {}
        if "review" in artifacts:
            parsed = json.loads(artifacts["review"])
            verdicts = parsed["verdicts"]
            failed = [rid for rid, v in verdicts.items() if v["status"] == "fail"]
            passed = [rid for rid, v in verdicts.items() if v["status"] == "pass"]
            artifacts.pop("review")
        return templates.TemplateResponse(request, "request_detail.html", {
            "req": req,
            "artifacts": artifacts,
            "failed": failed,
            "passed": passed,
            "verdicts": verdicts,
        })

    @app.post("/requests/{req_id}/run")
    async def request_run(request: Request, req_id: str):
        if requests.get(req_id) is None:
            return templates.TemplateResponse(request, "request_missing.html", {"req_id": req_id}, status_code=404)
        try:
            updated = workflow.run(req_id)
        except Exception as exc:  #WFError only
            req = requests.get(req_id)
            return templates.TemplateResponse(request, "request_run_error.html", {"req_id": req_id, "reason": str(exc), "req": req}, status_code=500)
        return RedirectResponse(f"/requests/{req_id}", status_code=303)

    return app


app = create_app()


def run() -> None:
    host = os.environ.get("CALIPER_HOST", "127.0.0.1")
    port = int(os.environ.get("CALIPER_PORT", "8080"))
    uvicorn.run(app, host=host, port=port)
