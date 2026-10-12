"""Reaching the internet from a MAST process: which proxy, and which CA bundle.

Every client that leaves the site -- the IERS table fetch, object-name resolution (Sesame,
TNS) -- must take its proxy from here, not from its library's own lookup.

**Why not the library's lookup.** On Windows, `urllib.request.getproxies()` (which astropy's
`download_file` and httpx with `trust_env=True` both rely on) reads the WinINET registry only
when the environment holds *no* proxy setting at all, and `no_proxy` counts. MAST_unit's
`app.py` deletes `http_proxy` / `https_proxy` so that PWI4 on this machine is not proxied, but
leaves `no_proxy`; so inside the unit `getproxies()` returns `{'no': ...}`, and every one of
those clients went direct and timed out (mast00, 2026-10-12: the IERS fetch with WinError
10060; `resolve_object_name("M31")` with no Sesame mirror answering in 15 s).

Not `common.proxy`, which is the nginx reverse-proxy context for *incoming* requests.
"""

from __future__ import annotations

import ssl
import sys
import urllib.request
from urllib.parse import urlsplit


def internet_proxies() -> dict[str, str]:
    """`http_proxy` / `https_proxy` if the environment sets either, else the registry's."""
    environment = urllib.request.getproxies_environment()
    chosen = {scheme: environment[scheme] for scheme in ("http", "https") if scheme in environment}
    if not chosen and sys.platform == "win32":
        registry = urllib.request.getproxies_registry()
        chosen = {scheme: registry[scheme] for scheme in ("http", "https") if scheme in registry}
    return chosen


def internet_proxy(url: str) -> str | None:
    """The proxy for `url`'s scheme, or None to go direct. For httpx: pass it as `proxy=`,
    with `trust_env=False` so httpx does not do its own (broken, as above) lookup."""
    return internet_proxies().get(urlsplit(url).scheme)


def tls_context() -> ssl.SSLContext:
    """certifi's CA bundle when installed, as astropy and httpx use: Python's default store
    does not verify the IERS data centre's chain on these machines (CERTIFICATE_VERIFY_FAILED,
    "unable to get local issuer certificate", mast00, 2026-10-12)."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())
