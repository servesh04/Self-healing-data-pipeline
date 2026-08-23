"""Bearer-token auth for /api/* routes.

One shared token in an env var — not JWT, not user accounts. Compared with
compare_digest so a wrong token cannot be recovered by timing the response.
"""

from secrets import compare_digest

from fastapi import Depends, HTTPException, status
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
