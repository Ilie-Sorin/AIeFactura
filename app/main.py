from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.status import HTTP_302_FOUND, HTTP_303_SEE_OTHER, HTTP_307_TEMPORARY_REDIRECT, HTTP_308_PERMANENT_REDIRECT

from app.config import get_settings
from app.routers import admin, auth, dashboard, documents, groups, imports, registry, relations
from app.scheduler import shutdown_scheduler, start_scheduler

_REDIRECT_STATUSES = {HTTP_302_FOUND, HTTP_303_SEE_OTHER, HTTP_307_TEMPORARY_REDIRECT, HTTP_308_PERMANENT_REDIRECT}


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    shutdown_scheduler()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AIeFactura", lifespan=lifespan)

    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(registry.router)
    app.include_router(documents.router)
    app.include_router(groups.router)
    app.include_router(relations.router)
    app.include_router(imports.router)
    app.include_router(admin.router)

    @app.exception_handler(StarletteHTTPException)
    async def redirect_aware_http_exception_handler(request: Request, exc: StarletteHTTPException):
        # require_login() foloseste HTTPException + Location ca sa redirectioneze
        # spre /login din interiorul unei dependinte -- il traducem intr-un
        # raspuns de redirect real, nu JSON cu status 303.
        if exc.status_code in _REDIRECT_STATUSES and exc.headers and "location" in {
            k.lower() for k in exc.headers
        }:
            location = exc.headers.get("Location") or exc.headers.get("location")
            return RedirectResponse(url=location, status_code=exc.status_code)
        from fastapi.exception_handlers import http_exception_handler

        return await http_exception_handler(request, exc)

    return app


app = create_app()
