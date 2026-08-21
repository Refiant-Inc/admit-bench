"""FastAPI conference server. Web dependencies remain an optional extra."""

from __future__ import annotations

import hmac
import os
from pathlib import Path

from admitbench.ifac_demo.controller import DemoController
from admitbench.ifac_demo.research import ResearchStore
from admitbench.ifac_demo.session import CSTRDemoSession, load_scenario


STATIC_ROOT = Path(__file__).resolve().parent / "static"


def validate_deployment(host: str) -> None:
    """Remote research exports must never be exposed without an admin token."""
    if host not in {"127.0.0.1", "localhost", "::1"} and not os.getenv("ADMIT_IFAC_ADMIN_TOKEN"):
        raise RuntimeError(
            "a non-local IFAC demo bind requires ADMIT_IFAC_ADMIN_TOKEN to protect research exports"
        )


def create_app(controller: DemoController | None = None):
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:  # pragma: no cover - exercised in base-only installs
        raise RuntimeError('IFAC demo dependencies are missing; install with pip install -e ".[demo]"') from exc

    app = FastAPI(title="ADMIT IFAC CSTR demonstrator", docs_url=None, redoc_url=None)
    app.state.controller = controller or DemoController()
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    def call(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (ValueError, RuntimeError, PermissionError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def require_admin(x_admit_admin_token: str | None = Header(default=None)) -> None:
        expected = os.getenv("ADMIT_IFAC_ADMIN_TOKEN")
        if expected and not hmac.compare_digest(x_admit_admin_token or "", expected):
            raise HTTPException(status_code=403, detail="admin token required")

    @app.get("/")
    def participant_page():
        return FileResponse(STATIC_ROOT / "index.html")

    @app.get("/research")
    def research_page():
        return FileResponse(STATIC_ROOT / "research.html")

    @app.get("/api/session")
    def get_session():
        return app.state.controller.snapshot()

    @app.post("/api/session/begin")
    def begin():
        return call(app.state.controller.begin)

    @app.post("/api/session/consent")
    def consent(body: dict):
        return call(app.state.controller.set_consent, bool(body.get("consent", False)))

    @app.post("/api/session/advance")
    def advance(body: dict):
        return call(app.state.controller.advance, float(body.get("seconds", 0.0)))

    @app.post("/api/session/diagnosis")
    def diagnosis(body: dict):
        return call(app.state.controller.diagnose, str(body.get("diagnosis", "")))

    @app.post("/api/session/check")
    def check(body: dict):
        return call(app.state.controller.perform_check, str(body.get("check", "")))

    @app.post("/api/session/action")
    def action(body: dict):
        return call(app.state.controller.submit_action, body)

    @app.post("/api/session/summary")
    def summary():
        return call(app.state.controller.show_summary)

    @app.post("/api/session/new")
    def new_session():
        return call(app.state.controller.reset)

    @app.get("/api/research/summary")
    def research_summary(_: None = Depends(require_admin)):
        return app.state.controller.research_store.summary()

    @app.post("/api/research/export/{format}")
    def research_export(format: str, _: None = Depends(require_admin)):
        path = call(app.state.controller.research_store.export, format)
        media = "application/x-ndjson" if path.suffix == ".jsonl" else "text/csv"
        return FileResponse(path, filename=path.name, media_type=media)

    return app


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    data_dir: str | Path = "data/ifac_demo",
    scenario_path: str | Path | None = None,
) -> None:
    validate_deployment(host)
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('IFAC demo dependencies are missing; install with pip install -e ".[demo]"') from exc
    session = CSTRDemoSession(scenario=load_scenario(scenario_path))
    controller = DemoController(session=session, research_store=ResearchStore(data_dir))
    uvicorn.run(create_app(controller), host=host, port=int(port), access_log=False)
