"""Role-specific bearer credentials for demo and production API surfaces."""

from secrets import compare_digest

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import Settings, get_settings

# auto_error=False so a *missing* header returns our own 401 rather than
# HTTPBearer's 403 — the Phase 0 DoD checks for 401 on an unauthed request.
_scheme = HTTPBearer(auto_error=False, description="Shared token")

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Missing or invalid bearer token",
    headers={"WWW-Authenticate": "Bearer"},
)


def require_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_scheme),
    settings: Settings = Depends(get_settings),
) -> None:
    if creds is None or creds.scheme.lower() != "bearer":
        raise _UNAUTHORIZED
    if not compare_digest(creds.credentials, settings.shared_token):
        raise _UNAUTHORIZED


def _matches(creds: HTTPAuthorizationCredentials | None, token: str | None) -> bool:
    return bool(creds and creds.scheme.lower() == "bearer" and token and
                compare_digest(creds.credentials, token))


def require_loader(
    creds: HTTPAuthorizationCredentials | None = Depends(_scheme),
    settings: Settings = Depends(get_settings),
) -> None:
    token = settings.shared_token if settings.auth_mode == "demo" else settings.loader_service_token
    if not _matches(creds, token):
        raise _UNAUTHORIZED


def require_approver(
    creds: HTTPAuthorizationCredentials | None = Depends(_scheme),
    settings: Settings = Depends(get_settings),
) -> None:
    token = settings.shared_token if settings.auth_mode == "demo" else settings.approver_token
    if not _matches(creds, token):
        raise _UNAUTHORIZED


def decision_role(
    creds: HTTPAuthorizationCredentials | None = Depends(_scheme),
    settings: Settings = Depends(get_settings),
) -> str:
    if _matches(creds, settings.approver_token if settings.auth_mode == "production" else settings.shared_token):
        return "approver"
    if settings.auth_mode == "production" and _matches(creds, settings.shared_token):
        return "browser"
    raise _UNAUTHORIZED


def require_browser_scope(request: Request, settings: Settings = Depends(get_settings)) -> None:
    if settings.auth_mode == "production" and request.query_params.get("pipeline_env") not in {
        None, settings.pipeline_env,
    }:
        raise HTTPException(status_code=403, detail="browser analytics are limited to the dashboard scope")
