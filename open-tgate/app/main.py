from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api_accounts import router as telegram_router
from .api_integrations import router as integrations_router
from .api_workspace import mcp_router, router as workspace_router
from .config import get_settings
from .observability import init_sentry
from .oauth import router as oauth_router
from .security import require_admin

settings = get_settings()
init_sentry(settings, component="api")
app = FastAPI(title="Open-TGate API", version=__version__, docs_url=None if settings.app_env == "production" else "/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.dashboard_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(telegram_router)
app.include_router(integrations_router)
app.include_router(workspace_router)
app.include_router(mcp_router)
app.include_router(oauth_router)


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "open-tgate-api"}


@app.get("/readyz")
def readiness() -> dict[str, object]:
    state_exists = Path(settings.tdlib_database_directory).parent.exists()
    return {
        "ready": settings.production_ready,
        "version": __version__,
        "environment": settings.app_env,
        "tdlib_state_mount": state_exists,
        "external_send_enabled": settings.external_send_enabled,
    }


@app.get("/api/v1/system", dependencies=[Depends(require_admin)])
def system_status() -> dict[str, object]:
    return {
        "worker_id": settings.worker_id,
        "configured": settings.production_ready,
        "external_send_enabled": settings.external_send_enabled,
        "safety": "human approval required; inbound content is untrusted",
    }



@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith('/oauth/'):
        error = str(exc.detail) if exc.status_code < 500 else 'server_error'
        return JSONResponse({'error': error}, status_code=exc.status_code,
                            headers={**(exc.headers or {}), 'Cache-Control': 'no-store'})
    return JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith('/oauth/'):
        return JSONResponse({'error': 'invalid_request'}, status_code=400,
                            headers={'Cache-Control': 'no-store'})
    # Preserve FastAPI's normal validation response for the existing API.
    from fastapi.exception_handlers import request_validation_exception_handler
    return await request_validation_exception_handler(request, exc)
