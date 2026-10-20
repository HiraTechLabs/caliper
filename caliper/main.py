import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from caliper.principles import PRINCIPLES
from caliper.registry import STACK_PRESETS, ProjectError, ProjectRegistry

BASE_DIR = Path(__file__).resolve().parent


def create_app(registry: ProjectRegistry | None = None) -> FastAPI:
    if registry is None:
        projects_root = os.environ.get("CALIPER_PROJECTS_ROOT", str(BASE_DIR.parent / "projects"))
        registry = ProjectRegistry(Path(projects_root))
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
        return templates.TemplateResponse(request, "project_detail.html", {"project": manifest})

    return app


app = create_app()


def run() -> None:
    host = os.environ.get("CALIPER_HOST", "127.0.0.1")
    port = int(os.environ.get("CALIPER_PORT", "8080"))
    uvicorn.run(app, host=host, port=port)
