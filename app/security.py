from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets as stdlib_secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from hmac import compare_digest
from typing import Deque, Dict, Iterable, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette import status

from app.config import SecretStore
from app.database import Database


SESSION_COOKIE = "whackamole_session"
CSRF_COOKIE = "whackamole_csrf"
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_REQUEST_BYTES = 1024 * 1024
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def _normalized_origin(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"Invalid allowed origin: {value}")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError(f"Allowed origins must not include a path, query, or fragment: {value}")
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = 80 if parsed.scheme == "http" else 443
    port = parsed.port or default_port
    suffix = "" if port == default_port else f":{port}"
    return f"{parsed.scheme}://{host}{suffix}"


def _parse_origins(value: str) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(_normalized_origin(part) for part in value.split(",") if part.strip()))


def _allowed_bypass_network(value: str) -> ipaddress._BaseNetwork:
    network = ipaddress.ip_network(value.strip(), strict=False)
    if network.prefixlen == 0:
        raise ValueError("Wildcard UI bypass networks are forbidden")
    address = network.network_address
    if address.is_unspecified or address.is_multicast or address.is_global:
        raise ValueError(f"UI bypass network must be explicitly non-global: {network}")
    return network


def _parse_networks(value: str) -> Tuple[ipaddress._BaseNetwork, ...]:
    return tuple(_allowed_bypass_network(part) for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class AuthSettings:
    bypass_networks: Tuple[ipaddress._BaseNetwork, ...]
    allowed_origins: Tuple[str, ...]
    cookie_secure: bool

    @classmethod
    def from_environment(cls) -> "AuthSettings":
        bypass_networks = _parse_networks(os.getenv("WHACKAMOLE_UI_BYPASS_CIDRS", ""))
        allowed_origins = _parse_origins(os.getenv("WHACKAMOLE_ALLOWED_ORIGINS", ""))
        if bypass_networks and not allowed_origins:
            raise RuntimeError("WHACKAMOLE_ALLOWED_ORIGINS is required when local UI bypass is enabled")
        secure = os.getenv("WHACKAMOLE_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
        return cls(bypass_networks=bypass_networks, allowed_origins=allowed_origins, cookie_secure=secure)

    @classmethod
    def from_values(cls, bypass_cidrs: str, allowed_origins: str, cookie_secure: bool = False) -> "AuthSettings":
        bypass_networks = _parse_networks(bypass_cidrs)
        origins = _parse_origins(allowed_origins)
        if bypass_networks and not origins:
            raise ValueError("At least one allowed origin is required when local UI bypass is enabled")
        return cls(bypass_networks=bypass_networks, allowed_origins=origins, cookie_secure=bool(cookie_secure))

    def bypasses(self, client_ip: str) -> bool:
        try:
            address = ipaddress.ip_address(client_ip)
        except ValueError:
            return False
        return any(address in network for network in self.bypass_networks)


@dataclass(frozen=True)
class SessionIdentity:
    username: str
    auth_method: str
    csrf_token: str
    expires_at: int


class AuthManager:
    def __init__(self, db: Database, secrets: SecretStore, settings: Optional[AuthSettings] = None) -> None:
        self.db = db
        self.secrets = secrets
        self._settings_override = settings
        self.settings = settings or self._load_settings()
        self._failures: Dict[str, Deque[int]] = defaultdict(deque)
        self._bootstrap_api_token()
        self.db.extend_active_auth_sessions(SESSION_TTL_SECONDS)

    def _load_settings(self) -> AuthSettings:
        if os.getenv("WHACKAMOLE_UI_BYPASS_CIDRS") or os.getenv("WHACKAMOLE_ALLOWED_ORIGINS"):
            return AuthSettings.from_environment()
        return AuthSettings.from_values(
            self.db.get_kv("security_ui_bypass_cidrs") or "",
            self.db.get_kv("security_allowed_origins") or "",
            (self.db.get_kv("security_cookie_secure") or "false").lower() == "true",
        )

    def configure_network_bypass(self, settings: AuthSettings) -> None:
        if self._settings_override is not None:
            self.settings = self._settings_override
            return
        self.db.set_kv("security_ui_bypass_cidrs", ",".join(str(network) for network in settings.bypass_networks))
        self.db.set_kv("security_allowed_origins", ",".join(settings.allowed_origins))
        self.db.set_kv("security_cookie_secure", "true" if settings.cookie_secure else "false")
        self.settings = self._load_settings()

    def _bootstrap_api_token(self) -> None:
        if self.secrets.has("whackamole_api_token"):
            return
        token = os.getenv("WHACKAMOLE_API_TOKEN", "").strip()
        if token:
            if len(token) < 32:
                raise RuntimeError("WHACKAMOLE_API_TOKEN must contain at least 32 characters")
            self.secrets.set("whackamole_api_token", token)

    def has_admin(self) -> bool:
        return self.db.get_admin_account() is not None

    def validate_username(self, username: str) -> str:
        value = str(username or "").strip()
        if not USERNAME_RE.fullmatch(value):
            raise ValueError("Username must be 3-64 characters using letters, numbers, dot, underscore, or hyphen")
        return value

    def validate_password(self, password: str) -> str:
        value = str(password or "")
        if not 15 <= len(value) <= 256:
            raise ValueError("Password must be between 15 and 256 characters")
        return value

    def create_admin(self, username: str, password: str) -> bool:
        username = self.validate_username(username)
        password = self.validate_password(password)
        created = self.db.create_admin_account(username, username.casefold(), PASSWORD_HASHER.hash(password))
        if created:
            self.db.append_security_event("admin_setup", username=username, outcome="success")
        return created

    def update_admin(self, username: str, password: str) -> None:
        username = self.validate_username(username)
        password = self.validate_password(password)
        self.db.update_admin_account(username, username.casefold(), PASSWORD_HASHER.hash(password))
        self.db.append_security_event("admin_credentials_changed", username=username, outcome="success")

    def verify_password(self, username: str, password: str) -> bool:
        row = self.db.get_admin_account()
        if row is None or str(row["username_normalized"]) != str(username or "").strip().casefold():
            return False
        try:
            valid = PASSWORD_HASHER.verify(str(row["password_hash"]), str(password or ""))
        except (VerifyMismatchError, InvalidHashError):
            return False
        return bool(valid)

    def verify_api_token(self, token: str) -> bool:
        expected = self.secrets.get("whackamole_api_token") or ""
        return bool(expected and token and compare_digest(str(token), expected))

    def bearer_token(self, request: Request) -> str:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        return token if scheme.lower() == "bearer" else ""

    def create_session(self, username: str, auth_method: str, client_ip: str) -> Tuple[str, str]:
        raw_token = stdlib_secrets.token_urlsafe(32)
        csrf_token = stdlib_secrets.token_urlsafe(32)
        admin = self.db.get_admin_account()
        auth_version = int(admin["auth_version"]) if admin is not None else 0
        self.db.create_auth_session(
            _token_hash(raw_token),
            username,
            auth_version,
            auth_method,
            client_ip if auth_method == "network" else "",
            _token_hash(csrf_token),
            int(time.time()) + SESSION_TTL_SECONDS,
        )
        return raw_token, csrf_token

    def session_identity(self, raw_token: str, csrf_token: str, client_ip: str) -> Optional[SessionIdentity]:
        if not raw_token or not csrf_token:
            return None
        row = self.db.get_auth_session(_token_hash(raw_token))
        if row is None:
            return None
        if not compare_digest(str(row["csrf_hash"]), _token_hash(csrf_token)):
            return None
        method = str(row["auth_method"])
        if method == "network":
            if str(row["client_ip"]) != client_ip or not self.settings.bypasses(client_ip):
                return None
        else:
            admin = self.db.get_admin_account()
            if admin is None or int(row["auth_version"]) != int(admin["auth_version"]):
                return None
        return SessionIdentity(str(row["username"]), method, csrf_token, int(row["expires_at"]))

    def delete_session(self, raw_token: str) -> None:
        if raw_token:
            self.db.delete_auth_session(_token_hash(raw_token))

    def login_blocked(self, client_ip: str) -> bool:
        now = int(time.time())
        failures = self._failures[client_ip]
        while failures and failures[0] < now - 900:
            failures.popleft()
        recent = [failure for failure in failures if failure >= now - 300]
        return len(recent) >= 5

    def record_login_failure(self, client_ip: str, route: str) -> None:
        self._failures[client_ip].append(int(time.time()))
        self.db.append_security_event("login", client_ip=client_ip, route=route, outcome="failure")

    def clear_login_failures(self, client_ip: str) -> None:
        self._failures.pop(client_ip, None)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        manager: AuthManager = request.app.state.auth
        path = request.url.path
        client_ip = request.client.host if request.client else ""
        request.state.client_ip = client_ip

        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > MAX_REQUEST_BYTES:
                    return self._secure(JSONResponse({"detail": "Request body too large"}, status_code=413))
            except ValueError:
                return self._secure(JSONResponse({"detail": "Invalid Content-Length"}, status_code=400))
        if request.method in UNSAFE_METHODS:
            body = await request.body()
            if len(body) > MAX_REQUEST_BYTES:
                return self._secure(JSONResponse({"detail": "Request body too large"}, status_code=413))

        if not self._valid_host(request, manager.settings, path):
            manager.db.append_security_event("request_rejected", client_ip=client_ip, route=path, outcome="host")
            return self._secure(JSONResponse({"detail": "Invalid Host"}, status_code=400))

        bearer_valid = manager.verify_api_token(manager.bearer_token(request))
        policy = route_policy(path)
        public = policy == "public"
        bearer_only = policy == "bearer_api"
        session_token = request.cookies.get(SESSION_COOKIE, "")
        csrf_token = request.cookies.get(CSRF_COOKIE, "")
        identity = manager.session_identity(
            session_token,
            csrf_token,
            client_ip,
        )
        new_session: Optional[Tuple[str, str]] = None

        if bearer_only and not bearer_valid:
            return self._secure(
                JSONResponse(
                    {"detail": "Invalid or missing API token"},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            )

        if not public and not bearer_only and not bearer_valid and identity is None:
            if not manager.has_admin():
                return self._secure(RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER))
            if manager.settings.bypasses(client_ip) and request.method in {"GET", "HEAD"}:
                new_session = manager.create_session("local-network", "network", client_ip)
                identity = SessionIdentity(
                    "local-network",
                    "network",
                    new_session[1],
                    int(time.time()) + SESSION_TTL_SECONDS,
                )
                manager.db.append_security_event("network_bypass", client_ip=client_ip, route=path, outcome="success")
            else:
                if path.startswith("/ui-api/"):
                    return self._secure(JSONResponse({"detail": "Authentication required"}, status_code=401))
                return self._secure(RedirectResponse(f"/login?next={_safe_next(path)}", status_code=status.HTTP_303_SEE_OTHER))

        request.state.authenticated = bool(bearer_valid or identity)
        request.state.auth_method = "bearer" if bearer_valid else (identity.auth_method if identity else "public")
        request.state.auth_username = identity.username if identity else ""
        request.state.csrf_token = identity.csrf_token if identity else ""
        request.state.local_bypass = bool(identity and identity.auth_method == "network")

        if request.method in UNSAFE_METHODS and not bearer_valid:
            if path in {"/login", "/setup"}:
                if not self._valid_origin(request, manager.settings):
                    return self._secure(JSONResponse({"detail": "Invalid request origin"}, status_code=403))
            elif not public:
                if identity is None or not await self._valid_csrf(request, identity.csrf_token):
                    manager.db.append_security_event("request_rejected", client_ip=client_ip, route=path, outcome="csrf")
                    return self._secure(JSONResponse({"detail": "CSRF validation failed"}, status_code=403))
                if not self._valid_origin(request, manager.settings):
                    return self._secure(JSONResponse({"detail": "Invalid request origin"}, status_code=403))

        response = await call_next(request)
        if new_session:
            _set_session_cookies(response, new_session[0], new_session[1], manager.settings.cookie_secure)
        elif identity is not None and session_token and csrf_token:
            current_identity = manager.session_identity(session_token, csrf_token, client_ip)
            if current_identity is None:
                return self._secure(response)
            remaining = max(1, current_identity.expires_at - int(time.time()))
            _set_session_cookies(
                response,
                session_token,
                csrf_token,
                manager.settings.cookie_secure,
                max_age=remaining,
            )
        return self._secure(response)

    @staticmethod
    def _valid_host(request: Request, settings: AuthSettings, path: str) -> bool:
        if not settings.allowed_origins or path == "/api/status":
            return True
        host = request.headers.get("host", "").lower()
        return any(urlsplit(origin).netloc.lower() == host for origin in settings.allowed_origins)

    @staticmethod
    def _valid_origin(request: Request, settings: AuthSettings) -> bool:
        origin = request.headers.get("origin", "").strip()
        if not origin:
            referer = request.headers.get("referer", "").strip()
            if referer:
                parsed = urlsplit(referer)
                origin = f"{parsed.scheme}://{parsed.netloc}"
        try:
            normalized = _normalized_origin(origin) if origin else ""
            if settings.allowed_origins:
                return bool(normalized and normalized in settings.allowed_origins)
            if not normalized:
                return False
            request_origin = _normalized_origin(f"{request.url.scheme}://{request.headers.get('host', '')}")
            return normalized == request_origin
        except (ValueError, TypeError):
            return False

    @staticmethod
    async def _valid_csrf(request: Request, expected: str) -> bool:
        supplied = request.headers.get("x-csrf-token", "")
        content_type = request.headers.get("content-type", "")
        if not supplied and content_type.startswith("application/x-www-form-urlencoded"):
            body = await request.body()
            supplied = parse_qs(body.decode("utf-8", errors="replace")).get("_csrf_token", [""])[0]
        return bool(supplied and expected and compare_digest(supplied, expected))

    @staticmethod
    def _secure(response: Response) -> Response:
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _safe_next(path: str) -> str:
    return path if path.startswith("/") and not path.startswith("//") else "/"


def _is_public_path(path: str) -> bool:
    return path in {"/login", "/setup", "/api/status"} or path.startswith("/static/")


def _is_bearer_api(path: str) -> bool:
    return path.startswith("/api/") and path != "/api/status"


def route_policy(path: str) -> str:
    if _is_public_path(path):
        return "public"
    if _is_bearer_api(path):
        return "bearer_api"
    if path.startswith("/ui-api/"):
        return "browser_api"
    return "ui"


def _set_session_cookies(
    response: Response,
    session_token: str,
    csrf_token: str,
    secure: bool,
    *,
    max_age: int = SESSION_TTL_SECONDS,
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def set_session_cookies(response: Response, session_token: str, csrf_token: str, secure: bool) -> None:
    _set_session_cookies(response, session_token, csrf_token, secure)


def validate_service_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Service URLs must be HTTP(S), include a hostname, and must not contain credentials or fragments")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address and (address.is_link_local or address.is_multicast or address.is_unspecified):
        raise ValueError("Link-local, multicast, and unspecified service destinations are forbidden")
    return raw


def service_origin(value: str) -> str:
    raw = validate_service_url(value)
    return _normalized_origin(raw) if raw else ""


def bound_secret_name(name: str) -> str:
    return f"{name}__origin"


def set_bound_secret(store: SecretStore, name: str, value: str, url: str) -> None:
    store.set(name, value)
    store.set(bound_secret_name(name), service_origin(url))


def get_bound_secret(store: SecretStore, name: str, url: str) -> Optional[str]:
    value = store.get(name)
    if not value:
        return None
    bound = store.get(bound_secret_name(name))
    origin = service_origin(url)
    if bound:
        return value if compare_digest(bound, origin) else None
    if origin:
        # Trust the configured origin once for credentials created by older releases.
        store.set(bound_secret_name(name), origin)
        return value
    return None


def clear_bound_secret(store: SecretStore, name: str) -> None:
    store.clear(name)
    store.clear(bound_secret_name(name))
