"""Точка входа FastAPI."""

from __future__ import annotations

import base64
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .collector import CollectorContext
from .config import Settings
from .db import Database
from .mc import MeshCentralClient
from .scheduler import build_scheduler
from .timeutil import fmt_local, human_ago, human_duration, human_mb, iso_utc, pct, utcnow
from .web.routes import router
from .web.urls import host_url

log = logging.getLogger("monitor")

BASE_DIR = Path(__file__).parent
WEB_DIR = BASE_DIR / "web"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # same-origin: свой Referer доходит до нас (запасной CSRF-проверки, если
    # браузер не прислал Origin), но наружу утекать ничего не должно.
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'none'; connect-src 'self'"
    ),
}


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def _secure(response: Response, *, html: bool = False) -> Response:
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    if html:
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _basic_ok(header: str, settings: Settings) -> bool:
    scheme, _, payload = header.partition(" ")
    if scheme.lower() != "basic" or not payload:
        return False
    try:
        decoded = base64.b64decode(payload, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    user, _, password = decoded.partition(":")
    return secrets.compare_digest(user, settings.auth_user) and secrets.compare_digest(
        password, settings.auth_pass
    )


def _same_origin(request: Request, settings: Settings) -> bool:
    """Примитивная защита от CSRF: браузерный POST должен быть со своего origin.

    Host может отличаться от Origin в dev-режиме (IP vs имя хоста, reverse-прокси),
    поэтому дополнительно разрешаем явный список MON_ALLOWED_ORIGINS. Отказы
    пишем в лог без секретов — по ним сразу видно, что именно прислал браузер.
    """
    host = request.headers.get("host", "").lower()
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    value = origin or referer
    if not value:
        # Не браузер (curl, скрипт) — Origin/Referer не присылает.
        return True
    parsed = urlparse(value)
    netloc = parsed.netloc.lower()
    allowed = netloc == host
    if not allowed and settings.allowed_origins:
        allowed = value.rstrip("/").lower() in settings.allowed_origins or netloc in {
            urlparse(item).netloc.lower() for item in settings.allowed_origins
        }
    if not allowed:
        log.warning(
            "cross-origin POST отклонён: host=%s origin=%s referer=%s",
            host or "-",
            origin or "-",
            referer or "-",
        )
    return allowed


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)

    db = Database(settings.db_path)
    db.init()
    client = MeshCentralClient(settings)
    ctx = CollectorContext(settings=settings, db=db, client=client, log=log)

    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    templates.env.filters.update(
        {
            "localtime": lambda value, fmt="%d.%m.%Y %H:%M:%S": fmt_local(
                value, settings.tz, fmt
            ),
            "ago": lambda value: human_ago(value, settings.tz),
            "duration": human_duration,
            "mb": human_mb,
            "pct": pct,
            "iso": iso_utc,
        }
    )
    templates.env.globals.update(version=__version__, tz=settings.tz, host_url=host_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("RMM Monitor %s запускается: %s", __version__, settings.safe_summary())
        scheduler = build_scheduler(ctx)
        app.state.scheduler = scheduler
        if scheduler is not None:
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)
            db.dispose()
            log.info("RMM Monitor остановлен")

    app = FastAPI(
        title="RMM Monitor",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.ctx = ctx
    app.state.templates = templates
    app.state.scheduler = None

    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    app.include_router(router)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        # /agent/snapshot.ps1 доступен без Basic: агент MeshCentral не умеет
        # авторизоваться, а сам скрипт не содержит секретов.
        if path in {"/healthz", "/health", "/agent/snapshot.ps1"} or path.startswith(
            "/static/"
        ):
            return _secure(await call_next(request))

        if settings.auth_enabled:
            if not _basic_ok(request.headers.get("authorization", ""), settings):
                return _secure(
                    Response(
                        content="Требуется авторизация",
                        status_code=401,
                        media_type="text/plain; charset=utf-8",
                        headers={
                            "WWW-Authenticate": 'Basic realm="RMM Monitor", charset="UTF-8"'
                        },
                    )
                )
        elif not settings.allow_anonymous:
            return _secure(
                JSONResponse(
                    {"detail": "доступ не настроен: задай MON_AUTH_USER/MON_AUTH_PASS"},
                    status_code=503,
                )
            )

        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and not _same_origin(request, settings)
        ):
            if path.startswith("/api/"):
                return _secure(
                    JSONResponse({"detail": "cross-origin request rejected"}, status_code=403)
                )
            # Обычная форма: отдаём нормальную страницу, а не голый JSON.
            return _secure(
                templates.TemplateResponse(
                    request=request,
                    name="error.html",
                    context={
                        "request": request,
                        "settings": settings,
                        "now": utcnow(),
                        "page": "",
                        "code": 403,
                        "detail": "Запрос пришёл не с этой страницы. Обнови вкладку и попробуй снова.",
                    },
                    status_code=403,
                ),
                html=True,
            )

        response = await call_next(request)
        return _secure(response, html=path not in {"/healthz", "/health"})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/") or request.url.path == "/healthz":
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "request": request,
                "settings": settings,
                "now": utcnow(),
                "page": "",
                "code": exc.status_code,
                "detail": exc.detail,
            },
            status_code=exc.status_code,
        )

    return app
