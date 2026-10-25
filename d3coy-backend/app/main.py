"""D3COY FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.api import agent_runs, agents, alerts, analytics, events, health, scenarios, websocket
from app.config import Settings, get_settings
from app.registry.agent_registry import RegistryError
from app.registry.credentials import CredentialError
from app.services.container import Container, build_container
from app.services.event_service import ServiceError

logger = logging.getLogger("app")


class _SecretRedactingFilter(logging.Filter):
    """Removes configured secrets from any log line, as a last line of defence."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            msg = record.getMessage()
            if any(s in msg for s in self.secrets):
                for s in self.secrets:
                    msg = msg.replace(s, "***")
                record.msg, record.args = msg, ()
        return True


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(level=settings.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    secrets = [k.get_secret_value() for k in (settings.admin_api_key, settings.permission_signing_key) if k]
    flt = _SecretRedactingFilter(secrets)
    for handler in logging.getLogger().handlers:
        handler.addFilter(flt)


def _error(status: int, code: str, message: str, details=None) -> JSONResponse:  # noqa: ANN001
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message,
                                                               "details": details}})


def create_app(settings: Settings | None = None, container: Container | None = None,
               load_models: bool = True) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    container = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container.ws.bind_loop(asyncio.get_running_loop())
        container.startup(load_models=load_models)
        logger.info("%s started: behavior=%s river=%s semantic=%s admin_key=%s", settings.app_name,
                    container.behavior.status()["status"], container.online.status()["status"],
                    container.intent.status()["status"],
                    "configured" if settings.admin_key_configured else "NOT configured (admin endpoints disabled)")
        yield
        container.db.dispose()

    app = FastAPI(title=f"{settings.app_name} API", version="0.1.0", lifespan=lifespan,
                  description="AI-agent identity drift detection and prevention — prototype backend.")
    app.state.container = container
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type", "X-API-Key",
                                                                              "X-Agent-Key"])

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError):
        return _error(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RegistryError)
    async def _registry_error(_: Request, exc: RegistryError):
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(CredentialError)
    async def _credential_error(_: Request, exc: CredentialError):
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return _error(exc.status_code, exc.detail["code"], exc.detail.get("message", ""))
        return _error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        # Echo field locations and messages only — never the submitted values.
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
                   for e in exc.errors()]
        return _error(422, "validation_error", "The request is invalid", details)

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError):
        logger.exception("Database error")
        return _error(503, "database_error", "A database error occurred; no action was executed")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("Unhandled error")
        return _error(500, "internal_error", "An internal error occurred")

    for r in (health.router, agents.router, events.router, alerts.router, analytics.router, scenarios.router,
              agent_runs.router, websocket.router):
        app.include_router(r)
    return app


def _lazy_app() -> FastAPI:
    return create_app()


app = _lazy_app()
