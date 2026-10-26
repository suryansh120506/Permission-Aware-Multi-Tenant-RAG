"""
auth.py - Mock authentication layer.

Resolves an incoming bearer token into an immutable `User` principal
(user_id, tenant_id, role). This is the only module you would replace in a
real deployment: swap the static token registry for OIDC/JWT validation
(signature, issuer, audience, expiry) and map the verified claims into `User`.

Security properties kept even in the mock:
  * Identity (tenant + role) comes ONLY from the verified credential, never
    from request bodies, query params, or client-controlled headers.
  * Tokens are stored as SHA-256 digests and compared in constant time.
  * Every failure returns the same generic 401, so callers cannot probe
    which tokens exist.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger("rag.auth")


class Role(str, Enum):
    HR = "HR"
    ENGINEERING = "Engineering"


@dataclass(frozen=True, slots=True)
class User:
    user_id: str
    tenant_id: str
    role: Role


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


# Demo credentials only. Real systems never ship secrets in source code.
_TOKEN_REGISTRY: dict[bytes, User] = {
    _digest("alice-hr-token"): User(user_id="alice", tenant_id="AcmeCorp", role=Role.HR),
    _digest("bob-eng-token"): User(user_id="bob", tenant_id="AcmeCorp", role=Role.ENGINEERING),
}


def resolve_token(token: str) -> User | None:
    """Map a raw bearer token to a User, or None if it is unknown.

    Checks every entry without returning early, so the time taken does not
    depend on which entry matched.
    """
    presented = _digest(token)
    match: User | None = None
    for stored, user in _TOKEN_REGISTRY.items():
        if hmac.compare_digest(presented, stored):
            match = user
    return match


_bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Mock bearer token: `alice-hr-token` (HR) or `bob-eng-token` (Engineering).",
)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> User:
    """FastAPI dependency: resolve `Authorization: Bearer <token>` into a User."""
    if credentials is None:
        logger.info("auth.rejected reason=missing_credentials")
        raise _unauthorized()

    user = resolve_token(credentials.credentials)
    if user is None:
        logger.warning("auth.rejected reason=unknown_token")
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
