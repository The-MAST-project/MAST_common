"""
common/proxy.py — Nginx reverse-proxy context.

Nginx injects three headers for every proxied service:

    X-Proxy-External-IP   the externally-visible IP  (e.g. 10.23.3.73)
    X-Proxy-Port          the externally-visible port (e.g. 8000)
    X-Proxy-Base          the URL prefix for *this* service (e.g. /mast-dash/)

ProxyContext captures those values from an incoming request and uses them to
build correct external URLs — both self-referential (same service) and
cross-service (e.g. the backend generating mast-share storage URLs).

Works with Django (request.META) and FastAPI/Starlette (request.headers).
Falls back to ProxyContext.from_settings() for background tasks that have no
incoming request; reads PROXY_EXTERNAL_IP / PROXY_PORT / PROXY_BASE from env.
"""

from urllib.parse import urlparse, urlunparse


class ProxyContext:
    # Nginx header names (lowercase, as FastAPI/Starlette exposes them)
    _HDR_IP   = "x-proxy-external-ip"
    _HDR_PORT = "x-proxy-port"
    _HDR_BASE = "x-proxy-base"
    _HDR_HOST = "x-forwarded-host"

    # Django META equivalents (uppercase, HTTP_ prefix)
    _META_IP   = "HTTP_X_PROXY_EXTERNAL_IP"
    _META_PORT = "HTTP_X_PROXY_PORT"
    _META_BASE = "HTTP_X_PROXY_BASE"
    _META_HOST = "HTTP_X_FORWARDED_HOST"

    def __init__(self, scheme: str, external_ip: str, port: str, base: str, forwarded_host: str = ""):
        self.scheme      = scheme or "http"
        self.external_ip = (external_ip or "").strip()
        self.forwarded_host = (forwarded_host or "").strip()
        self.port        = str(port).strip() if port else ""
        # Normalise base: always starts with '/', never ends with '/'
        b = (base or "").strip().strip("/")
        self.base = f"/{b}" if b else ""

    # ── factories ────────────────────────────────────────────────────────────

    @classmethod
    def from_request(cls, request) -> "ProxyContext":
        """
        Build from an incoming request.  Auto-detects Django vs FastAPI.

        Results are cached on the request object so multiple calls within the
        same request lifecycle are free.
        """
        if hasattr(request, "_proxy_context"):
            return request._proxy_context

        if hasattr(request, "META"):
            ctx = cls._from_django(request)
        else:
            ctx = cls._from_fastapi(request)

        try:
            request._proxy_context = ctx
        except AttributeError:
            pass  # immutable request objects — just return without caching
        return ctx

    @classmethod
    def _from_django(cls, request) -> "ProxyContext":
        m = request.META
        return cls(
            scheme=request.scheme,
            external_ip=m.get(cls._META_IP, ""),
            port=m.get(cls._META_PORT, ""),
            base=m.get(cls._META_BASE, ""),
            forwarded_host=m.get(cls._META_HOST, ""),
        )

    @classmethod
    def _from_fastapi(cls, request) -> "ProxyContext":
        h = request.headers
        return cls(
            scheme=request.url.scheme,
            external_ip=h.get(cls._HDR_IP, ""),
            port=h.get(cls._HDR_PORT, ""),
            base=h.get(cls._HDR_BASE, ""),
            forwarded_host=h.get(cls._HDR_HOST, ""),
        )

    @classmethod
    def from_settings(cls) -> "ProxyContext":
        """
        Build from environment / Django settings.
        Use this in background tasks that have no incoming request.

        Reads (via python-decouple if available, otherwise os.environ):
            PROXY_EXTERNAL_IP
            PROXY_PORT
            PROXY_BASE
        """
        try:
            from decouple import config
        except ImportError:
            import os
            def config(key, default=""):  # type: ignore[misc]
                return os.environ.get(key, default)

        try:
            from django.conf import settings as dj
            scheme = "https" if getattr(dj, "SECURE_SSL_REDIRECT", False) else "http"
        except Exception:
            scheme = "http"

        return cls(
            scheme=scheme,
            external_ip=config("PROXY_EXTERNAL_IP", default=""),
            port=config("PROXY_PORT", default=""),
            base=config("PROXY_BASE", default=""),
        )

    # ── properties ───────────────────────────────────────────────────────────

    @property
    def proxied(self) -> bool:
        """True when proxy headers were present in the request."""
        return bool(self.external_ip)

    @property
    def origin(self) -> str:
        """scheme://host[:port]  — only valid when proxied."""
        host = self.forwarded_host or self.external_ip
        port_suffix = f":{self.port}" if self.port else ""
        return f"{self.scheme}://{host}{port_suffix}"

    # ── core URL builder ─────────────────────────────────────────────────────

    def absolute_url(self, path: str, base: str | None = None) -> str:
        """
        Build a fully-qualified external URL for *path*.

        path  — root-relative internal path, e.g. '/login/' or
                '/unit01/2025-01-01/Autofocus/0001/'
        base  — proxy base for the *target* service; defaults to self.base
                (the base for the service that received this request).
                Pass an explicit base when generating URLs for a different
                service, e.g. base='/mast-share/'.

        When not proxied, returns path unchanged (relative URL).

        Safe to call whether or not Django's SCRIPT_NAME / FORCE_SCRIPT_NAME
        is set: if *path* already starts with the effective base (because
        Django's reverse() already included it), the base is not prepended
        a second time.
        """
        if not path.startswith("/"):
            path = f"/{path}"
        effective_base = base if base is not None else self.base
        if effective_base:
            effective_base = "/" + effective_base.strip("/")
            if not path.startswith(effective_base):
                path = effective_base + path
        if not self.proxied:
            return path
        return f"{self.origin}{path}"

    def rewrite(self, internal_url: str, base: str | None = None) -> str:
        """
        Rewrite an internal service URL to its external proxy form.

        When proxied: strips the host/port from *internal_url*, prepends the
        effective base, and returns a fully-qualified external URL.

        When not proxied: returns *internal_url* unchanged so that clients on
        the local network can still reach the service directly.

        Example (proxied):
            proxy.rewrite('http://mast-wis-control:8008/unit01/2025-01-01/…',
                          base='/mast-share/')
            → 'http://10.23.3.73:8000/mast-share/unit01/2025-01-01/…'
        """
        if not self.proxied:
            return internal_url
        parsed = urlparse(internal_url)
        path = urlunparse(("", "", parsed.path, parsed.params, parsed.query, parsed.fragment))
        return self.absolute_url(path, base=base)

    # ── Django helpers (lazy imports — safe to call from non-Django code) ────

    def url_for(self, viewname: str, *args, **kwargs) -> str:
        """
        Resolve a Django URL name to a fully-qualified external URL.
        Falls back to the internal path when not proxied.
        """
        from django.urls import reverse
        path = reverse(viewname, args=args, kwargs=kwargs)
        return self.absolute_url(path)

    def static_url(self, static_path: str) -> str:
        """
        Build a fully-qualified external URL for a Django static file.
        static_path — relative path, e.g. 'css/style.css'
        """
        from django.conf import settings
        static_root = getattr(settings, "STATIC_URL", "/static/").rstrip("/")
        path = f"{static_root}/{static_path.lstrip('/')}"
        return self.absolute_url(path)

    # ── request helper ───────────────────────────────────────────────────────

    def build_absolute_uri(self, request, path: str | None = None) -> str:
        """
        Drop-in replacement for Django's request.build_absolute_uri(path).
        Uses proxy context when proxied, falls back to Django's own method.
        """
        if path is None:
            path = request.get_full_path()
        if self.proxied:
            return self.absolute_url(path)
        return request.build_absolute_uri(path)
