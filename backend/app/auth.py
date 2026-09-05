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
    """
    Read Supabase connection details from environment and return a new client.

    This is the raw factory — it is only called once by _supabase() below.
    Credentials must already be in os.environ at this point, which is why
    main.py runs load_dotenv() before importing any service modules.
    """
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY must be set before importing app.auth"
        )
    return create_client(url, key)


# Lazy singleton — created on first request, not at import time, so that
# dotenv has already been loaded by main.py before this module runs.
# Pattern: if _supabase_client is None → build it once → cache it here forever.
_supabase_client: Client | None = None


def _supabase() -> Client:
    """
    Return the cached Supabase client, building it on the very first call.

    This guard exists because Python runs module-level code at import time,
    but os.environ isn't fully populated until main.py's load_dotenv() runs.
    By deferring construction to the first real call we avoid "Invalid API key"
    errors from Supabase receiving empty strings.
    """
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = _get_supabase_client()
    return _supabase_client


# ── HTTPBearer scheme (auto-rejects missing / malformed headers) ───────────────
# FastAPI reads the Authorization header and hands credentials to get_current_user.
# auto_error=True means FastAPI returns 403 automatically for requests
# that have NO Authorization header at all, before our code even runs.
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

    Flow:
      1. FastAPI extracts the Bearer token string from the Authorization header
         via the _bearer_scheme dependency injected above.
      2. We call supabase.auth.get_user(token) — Supabase validates the JWT
         signature and expiry server-side and returns the user object.
      3. We extract and return response.user.id (a UUID string).

    This function is injected via `Depends(get_current_user)` into every
    protected endpoint — FastAPI calls it automatically before the handler runs.

    Raises
    ------
    HTTP 401  if the token is invalid, expired, or the header is malformed.
    """
    # The raw JWT string from the Authorization: Bearer <token> header.
    token = credentials.credentials

    try:
        # Ask Supabase to verify the JWT and return the associated user.
        # This makes an outbound HTTP call to Supabase on every authenticated request.
        response = _supabase().auth.get_user(token)
    except Exception as exc:
        # Any exception (network error, bad token format, etc.) maps to 401.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token verification failed: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Supabase returns None or a response with a null user for invalid/expired tokens.
    if response is None or response.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Return the Supabase user UUID as a plain string.
    # Every endpoint that depends on this function receives this user_id directly.
    return str(response.user.id)
