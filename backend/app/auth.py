"""
auth.py
-------
FastAPI dependency for JWT verification via Supabase.

Usage
-----
In any endpoint that needs an authenticated user:

    from app.auth import get_current_user
    from fastapi import Depends

    async def my_endpoint(user_id: str = Depends(get_current_user)):
        ...

The dependency:
  1. Reads the `Authorization: Bearer <token>` header.
  2. Sends the token to Supabase to verify it and retrieve the user.
  3. Returns the verified user.id string.
  4. Raises HTTP 401 with a descriptive message on any failure.
"""

import os

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client, create_client

# ── Supabase client (reused across requests) ───────────────────────────────────
# We intentionally create a module-level client here so that we share the
# connection pool rather than creating a new one on every request.

def _get_supabase_client() -> Client:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY must be set before importing app.auth"
        )
    return create_client(url, key)


# Lazy singleton — created on first request, not at import time, so that
# dotenv has already been loaded by main.py before this module runs.
_supabase_client: Client | None = None


def _supabase() -> Client:
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = _get_supabase_client()
    return _supabase_client


# ── HTTPBearer scheme (auto-rejects missing / malformed headers) ───────────────

_bearer_scheme = HTTPBearer(
    scheme_name="Supabase JWT",
    description="Pass `Authorization: Bearer <supabase_access_token>`",
    auto_error=True,   # Returns 403 automatically for missing header
)


# ── Dependency ─────────────────────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """
    FastAPI dependency — verify a Supabase JWT and return the user_id.

    Raises
    ------
    HTTP 401  if the token is invalid, expired, or the header is malformed.
    """
    token = credentials.credentials

    try:
        response = _supabase().auth.get_user(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token verification failed: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if response is None or response.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return str(response.user.id)
