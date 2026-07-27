from __future__ import annotations

import os
import time
from functools import wraps

import jwt
from flask import jsonify, request, g

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_JWKS_CACHE_SECONDS = 600

_jwks_client: "jwt.PyJWKClient | None" = None
_jwks_client_built_at: float = 0.0


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.message = message
        self.status = status


def _get_jwks_client() -> "jwt.PyJWKClient":
    global _jwks_client, _jwks_client_built_at
    now = time.time()
    if _jwks_client is None or (now - _jwks_client_built_at) > _JWKS_CACHE_SECONDS:
        if not SUPABASE_URL:
            raise AuthError("Server misconfigured: SUPABASE_URL not set", 500)
        jwks_url = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
        _jwks_client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=_JWKS_CACHE_SECONDS)
        _jwks_client_built_at = now
    return _jwks_client


def verify_token(auth_header: str | None) -> dict:
    if not auth_header or not auth_header.startswith("Bearer "):
        raise AuthError("Missing or malformed Authorization header", 401)

    token = auth_header[len("Bearer "):]
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, signing_key.key,
            algorithms=["ES256", "HS256", "RS256"],
            audience="authenticated",
        )
    except AuthError:
        raise
    except jwt.ExpiredSignatureError:
        raise AuthError("Session expired, please log in again", 401)
    except jwt.PyJWKClientError as exc:
        raise AuthError(f"Could not fetch signing key: {exc}", 401)
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"Invalid session token: {exc}", 401)
    except Exception as exc:
        # Catch-all so a server-side misconfiguration (missing dependency,
        # unreachable JWKS endpoint, unexpected library error, etc.) always
        # returns clean JSON instead of crashing with a raw error page that
        # breaks the frontend's response parsing.
        raise AuthError(f"Authentication failed due to a server error: {exc}", 500)
    return claims


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            claims = verify_token(request.headers.get("Authorization"))
        except AuthError as exc:
            return jsonify({"error": exc.message}), exc.status
        g.user = {"id": claims["sub"], "email": claims.get("email")}
        return fn(*args, **kwargs)
    return wrapper
