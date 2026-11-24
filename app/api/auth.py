"""ADMIN_TOKEN bearer / basic auth for mutating routes (architecture §12)."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)

from app.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)
_basic = HTTPBasic(auto_error=False)


def _tokens_match(provided: str, expected: str) -> bool:
    if not expected:
        return False
    return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def require_admin(
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    basic: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """
    Authenticate the single demo reviewer role.

    - When ``ADMIN_TOKEN`` is set: require Bearer or Basic (password = token).
    - When empty and not production: allow (local/dev convenience); actor = ``dev``.
    - When empty in production: 401 (misconfigured).
    """
    expected = settings.admin_token
    if not expected:
        if settings.app_env == "production":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="admin token not configured",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return "dev"

    if bearer is not None and _tokens_match(bearer.credentials, expected):
        return "reviewer"
    if basic is not None and _tokens_match(basic.password, expected):
        return basic.username or "reviewer"

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="unauthorized",
        headers={"WWW-Authenticate": "Bearer"},
    )


AdminActor = Annotated[str, Depends(require_admin)]
