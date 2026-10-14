"""JWT Bearer authentication for the public MCP/HTTP boundary."""

from __future__ import annotations

import os
import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import InvalidTokenError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .attachment_urls import verify_wf02_attachment_request


_current_jwt_claims: ContextVar[dict[str, Any] | None] = ContextVar(
    "agentd_current_jwt_claims",
    default=None,
)


def current_jwt_subject() -> str | None:
    """Return the authenticated caller identity for the current ASGI request."""
    claims = _current_jwt_claims.get()
    if not isinstance(claims, dict):
        return None
    for name in ("sub", "client_id", "azp"):
        value = claims.get(name)
        if isinstance(value, str) and value.strip():
            return f"{name}:{value.strip()}"
    return None


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class JWTSettings:
    """Authentication settings loaded without ever logging the secret."""

    required: bool
    secret: str | None
    algorithm: str
    issuer: str | None
    audience: str | None
    require_expiration: bool
    public_wf01_artifacts: bool

    @classmethod
    def from_env(cls) -> "JWTSettings":
        required = _as_bool(os.environ.get("MCP_AUTH_REQUIRED"), default=True)
        secret = os.environ.get("MCP_JWT_SECRET") or os.environ.get("JWT_SECRET")
        algorithm = os.environ.get("MCP_JWT_ALGORITHM", "HS256").strip().upper()
        if algorithm not in {"HS256", "HS384", "HS512"}:
            raise RuntimeError("MCP_JWT_ALGORITHM must be HS256, HS384, or HS512")
        if required and not secret:
            raise RuntimeError(
                "MCP authentication is enabled but MCP_JWT_SECRET/JWT_SECRET is missing"
            )
        return cls(
            required=required,
            secret=secret,
            algorithm=algorithm,
            issuer=os.environ.get("MCP_JWT_ISSUER") or None,
            audience=os.environ.get("MCP_JWT_AUDIENCE") or None,
            require_expiration=_as_bool(
                os.environ.get("MCP_JWT_REQUIRE_EXP"),
                default=True,
            ),
            public_wf01_artifacts=_as_bool(
                os.environ.get("MCP_PUBLIC_WF01_ARTIFACTS"),
                default=False,
            ),
        )

    def decode(self, token: str) -> dict[str, Any]:
        options: dict[str, Any] = {
            "require": ["exp"] if self.require_expiration else [],
            "verify_aud": bool(self.audience),
            "verify_iss": bool(self.issuer),
        }
        claims = jwt.decode(
            token,
            self.secret,
            algorithms=[self.algorithm],
            audience=self.audience,
            issuer=self.issuer,
            options=options,
        )
        if not isinstance(claims, dict):
            raise InvalidTokenError("JWT payload must be an object")
        return claims


class JWTBearerMiddleware:
    """Protect every HTTP route except explicit operational probes."""

    def __init__(
        self,
        app: ASGIApp,
        settings: JWTSettings,
        *,
        public_paths: set[str] | None = None,
    ) -> None:
        self.app = app
        self.settings = settings
        self.public_paths = public_paths or {"/health", "/ready"}

    @staticmethod
    def _is_public_wf01_artifact(scope: Scope, path: str) -> bool:
        """Match only anonymous read requests for persisted deliverables."""
        if str(scope.get("method") or "").upper() not in {"GET", "HEAD"}:
            return False
        patterns = (
            r"/data/[^/]+",
            r"/data/reports/[^/]+/[^/]+",
            r"/v1/wf01/tasks/[^/]+/report",
            r"/v1/wf01/tasks/[^/]+/exports/"
            r"(?:html|process_html|rules_html|csv|pdf|scientific_data)(?:/preview)?",
            r"/v1/wf01/tasks/[^/]+/scientific-artifacts(?:/[^/]+)?",
        )
        return any(re.fullmatch(pattern, path) for pattern in patterns)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.settings.required:
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if path in self.public_paths:
            await self.app(scope, receive, send)
            return
        if (
            self.settings.public_wf01_artifacts
            and self._is_public_wf01_artifact(scope, path)
        ):
            await self.app(scope, receive, send)
            return
        if verify_wf02_attachment_request(
            path,
            scope.get("query_string") or b"",
            secret=self.settings.secret,
            method=str(scope.get("method") or ""),
        ):
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        authorization = headers.get("authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            await self._reject(scope, receive, send, "Bearer JWT is required")
            return

        try:
            claims = self.settings.decode(token.strip())
        except InvalidTokenError:
            await self._reject(scope, receive, send, "Bearer JWT is invalid or expired")
            return

        scope["jwt_claims"] = claims
        context_token = _current_jwt_claims.set(claims)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_jwt_claims.reset(context_token)

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        message: str,
    ) -> None:
        response = JSONResponse(
            {"error": {"code": "UNAUTHORIZED", "message": message}},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)
