"""Resolve an object name to J2000 coordinates.

Standalone by design. The consumers are a plan sweeper (fill in a target that has a name
but no coordinates, and record them), `mount/goto_object`, and eventually
`unit/start_acquisition_and_guiding`, which takes J2000 only today. None of them are
wired here.

Order of enquiry, cheapest and most certain first:

1. **Moving targets are refused without a network call.** Comets and minor planets have
   no fixed J2000 -- only an ephemeris at an instant -- so a plausible answer would be
   worse than none. Refusing by pattern also stops such a name reaching a catalogue that
   might match it for unrelated reasons.
2. **Cache.**
3. **TNS**, for TNS-shaped names (`AT`/`SN` + year), because it is the authority for
   transients and has them within minutes of the discovery report. A miss or an outage
   falls through rather than failing: a name that merely looks like a transient may well
   be an older object that Sesame knows.
4. **Sesame** for everything else, through astropy, which queries SIMBAD then NED then
   VizieR and stops at the first positive answer.

A note on why TNS is first, since the reasoning was wrong at first and the correction
matters: it is NOT that Sesame returns a transient's host galaxy instead of the transient.
Measured on 2026-08-12, SIMBAD gives SN2023ixf its own position, 4.39 arcmin from M101's
nucleus. The real gap is recency -- a transient reported last night is in TNS and not yet
in SIMBAD -- so Sesame's failure mode for a fresh transient is "not found", which is loud
and safe. That is a weaker argument for TNS-first than the one originally recorded in
MAST_common#60, and it is why falling through to Sesame is correct rather than dangerous.

Every result carries where it came from. A misresolution is indistinguishable from a
correct one at every layer below this -- the mount slews normally, guiding locks, and a
spectrum is taken of the wrong object -- so the provenance is the only way to work out
afterwards why a night pointed where it did.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict, dataclass

from astropy.coordinates import name_resolve
from astropy.utils.data import conf as astropy_data_conf

from common.mast_logging import get_logger

logger = get_logger(__name__)

#: Whole-enquiry budget. The callers are operator-facing or sweep-driven; past this a
#: human assumes it is broken, and a sweeper has other targets to get to.
TOTAL_TIMEOUT_SECONDS = 15.0

#: One POST to one host.
TNS_TIMEOUT_SECONDS = 5.0

#: Per REQUEST, not per enquiry: astropy tries its Sesame mirrors in turn (CDS Strasbourg,
#: then Harvard CfA), so the worst case is twice this. astropy's own default is 10s, which
#: would make Sesame alone a 20s worst case and blow the budget above -- hence stating it.
SESAME_TIMEOUT_SECONDS = 4.0

#: A fixed object's position does not change; the cost of caching it is a stale name, not
#: a stale sky. Long, but not forever, so a corrected catalogue entry is eventually seen.
CATALOGUE_TTL_SECONDS = 30 * 24 * 3600.0

#: TNS positions are refined after discovery as astrometry improves, so an early answer
#: must not be pinned.
TNS_TTL_SECONDS = 3600.0

#: Short, deliberately. A transient reported ten minutes from now must not stay
#: unresolvable because a miss was cached hard -- for follow-up that is the wrong thing
#: to remember. Long enough only to stop a sweeper re-asking on every pass.
#:
#: Applied ONLY to a definitive miss -- the service answered and said no such object. A
#: timeout, a throttle or an outage is never remembered: see `_is_definitive_miss`.
NEGATIVE_TTL_SECONDS = 300.0

TNS_OBJECT_URL = "https://www.wis-tns.org/api/get/object"


class ObjectNameError(Exception):
    """A name could not be turned into coordinates."""


class MovingTargetError(ObjectNameError):
    """The name designates something with no fixed J2000 position."""


@dataclass(frozen=True)
class ResolvedObject:
    """Where a name resolved to, and who said so."""

    name: str
    ra_j2000_hours: float
    dec_j2000_degs: float
    resolver: str
    """Which service answered -- "tns", or "sesame" (SIMBAD/NED/VizieR internally)."""
    canonical_name: str | None = None
    """The identifier the service matched, when it reports one. Worth recording: a name
    that resolved via an alias is the case most likely to have resolved to the wrong
    thing."""

    def as_dict(self) -> dict:
        return asdict(self)


# Comets: `C/2023 A3`, `1P/Halley`, `73P-C`. Minor planets: `(433)`, `433 Eros`,
# provisional `2024 AB`, `2024 AB1`. Deliberately narrow -- a false positive refuses a
# real object, which is worse than a false negative falling through to a catalogue that
# will simply not find it.
_COMET = re.compile(r"^\s*\d*[PDCXAI][/-]\s*", re.IGNORECASE)
_MINOR_PLANET_PROVISIONAL = re.compile(r"^\s*\(?\s*\d{4}\s*\)?\s+[A-Z]{2}\d*\s*$")
_MINOR_PLANET_NUMBERED = re.compile(r"^\s*\(\s*\d+\s*\)")
_SOLAR_SYSTEM_BODIES = frozenset(
    """mercury venus mars jupiter saturn uranus neptune pluto sun moon luna sol ceres
    vesta pallas juno io europa ganymede callisto titan enceladus phobos deimos""".split()
)

# `AT2024xyz`, `SN2023ixf`, `SN 1987A`.
_TNS_NAME = re.compile(r"^\s*(AT|SN)\s*(\d{4})\s*([A-Za-z]{1,4})\s*$", re.IGNORECASE)


def _is_moving_target(name: str) -> bool:
    stripped = name.strip()
    return bool(
        stripped.lower() in _SOLAR_SYSTEM_BODIES
        or _COMET.match(stripped)
        or _MINOR_PLANET_PROVISIONAL.match(stripped)
        or _MINOR_PLANET_NUMBERED.match(stripped)
    )


def is_tns_name(name: str) -> bool:
    """True for `AT`/`SN` + year + letters -- the shape TNS assigns."""
    return bool(_TNS_NAME.match(name))


class _Cache:
    """Name -> (result or None) with a per-entry expiry.

    In memory and process-local on purpose. The durable record of a resolution belongs in
    the plan that needed it -- explicit, auditable, and never re-resolved -- rather than in
    a shared cache where a wrong answer would be served to the whole fleet with no trail.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, ResolvedObject | None]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(name: str) -> str:
        return " ".join(name.split()).casefold()

    def get(self, name: str) -> tuple[bool, ResolvedObject | None]:
        """(hit, value). A hit whose value is None is a remembered failure."""
        with self._lock:
            entry = self._entries.get(self._key(name))
            if entry is None:
                return False, None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._entries[self._key(name)]
                return False, None
            return True, value

    def put(self, name: str, value: ResolvedObject | None, ttl: float) -> None:
        with self._lock:
            self._entries[self._key(name)] = (time.monotonic() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_cache = _Cache()


def _tns_credentials() -> tuple[str, str, str] | None:
    """(api_key, tns_id, bot_name), or None if the vault does not carry all three.

    Requests need every one of them: the key authenticates, and the id and name go into
    the `tns_marker` User-Agent, without which TNS rejects the request.
    """
    try:
        from common.config import Config

        tns = Config().vault.tns
    except Exception:
        logger.exception("could not read TNS credentials from the vault")
        return None

    api_key = getattr(tns, "api_key", None)
    tns_id = getattr(tns, "tns_id", None)
    bot_name = getattr(tns, "bot_name", None)
    if not (api_key and api_key.get_secret_value() and tns_id and bot_name):
        return None
    return api_key.get_secret_value(), str(tns_id), str(bot_name)


def _is_definitive_miss(error: Exception) -> bool:
    """True when Sesame answered and said there is no such object.

    False when it did not answer at all, which astropy reports through the same exception
    type but a different message ("All Sesame queries failed ..." versus "Unable to find
    coordinates for name ..."). The distinction matters because only the first may be
    remembered: caching the second turns a passing service problem into a name that does
    not exist for the next five minutes.

    That is not hypothetical. On 2026-08-12, after several queries in quick succession,
    CDS refused one and `NGC 224` -- which is M31 -- came back unresolved and was then
    remembered as such. It resolved in 0.83s on the next attempt.

    Biased toward NOT caching: if astropy ever rewords the miss message this returns
    False and nothing is remembered, which costs extra queries rather than correctness.
    """
    return "unable to find coordinates for name" in str(error).lower()


def _resolve_via_sesame(name: str, timeout: float) -> ResolvedObject:
    """Ask Sesame, which asks SIMBAD then NED then VizieR and stops at the first answer."""
    with astropy_data_conf.set_temp("remote_timeout", timeout):
        coord = name_resolve.get_icrs_coordinates(name)
    return ResolvedObject(
        name=name,
        ra_j2000_hours=float(coord.ra.hour),
        dec_j2000_degs=float(coord.dec.deg),
        resolver="sesame",
    )


def resolve_object_name(name: str, total_timeout: float = TOTAL_TIMEOUT_SECONDS) -> ResolvedObject:
    """Coordinates for `name`, in J2000, with a record of which service supplied them.

    Raises `MovingTargetError` for a designation with no fixed position, and
    `ObjectNameError` when nothing could resolve it within the budget. Both are the
    caller's cue to report and move on -- a plan sweeper leaves the target for its next
    pass, an endpoint answers with an error.
    """
    if not name or not name.strip():
        raise ObjectNameError("no object name given")

    asked = name.strip()
    deadline = time.monotonic() + total_timeout

    if _is_moving_target(asked):
        raise MovingTargetError(
            f"'{asked}' is a moving target; it has no fixed J2000 position, only an "
            "ephemeris at an instant, so it cannot be resolved to coordinates here"
        )

    hit, cached = _cache.get(asked)
    if hit:
        if cached is None:
            raise ObjectNameError(f"'{asked}' could not be resolved (remembered from a recent attempt)")
        logger.debug(f"'{asked}' resolved from cache ({cached.resolver})")
        return cached

    attempts: list[str] = []

    if is_tns_name(asked):
        credentials = _tns_credentials()
        if credentials is None:
            # Not fatal, and not a reason to refuse: Sesame carries transients once they
            # are catalogued. A fresh one will simply not be found, which is safe.
            attempts.append("tns: no credentials in the vault")
            logger.info(f"'{asked}' looks like a TNS name but the vault has no TNS credentials; trying Sesame")
        else:
            try:
                resolved = _resolve_via_tns(asked, credentials, min(TNS_TIMEOUT_SECONDS, deadline - time.monotonic()))
            except Exception as e:  # noqa: BLE001 -- any TNS failure falls through to Sesame
                attempts.append(f"tns: {e}")
                logger.info(f"TNS could not resolve '{asked}' ({e}); trying Sesame")
            else:
                if resolved is not None:
                    _cache.put(asked, resolved, TNS_TTL_SECONDS)
                    return resolved
                attempts.append("tns: no match")

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        # Not cached: running out of budget says nothing about whether the object exists.
        raise ObjectNameError(f"'{asked}' not resolved within {total_timeout:g}s ({'; '.join(attempts)})")

    definitive = True
    try:
        resolved = _resolve_via_sesame(asked, min(SESAME_TIMEOUT_SECONDS, remaining))
    except name_resolve.NameResolveError as e:
        definitive = _is_definitive_miss(e)
        attempts.append(f"sesame: {e}")
    except Exception as e:  # noqa: BLE001 -- a resolver must not raise anything unexpected at its caller
        definitive = False
        attempts.append(f"sesame: {type(e).__name__}: {e}")
        logger.exception(f"unexpected failure resolving '{asked}' through Sesame")
    else:
        _cache.put(asked, resolved, CATALOGUE_TTL_SECONDS)
        return resolved

    if definitive:
        _cache.put(asked, None, NEGATIVE_TTL_SECONDS)
    raise ObjectNameError(f"'{asked}' could not be resolved ({'; '.join(attempts)})")


def _resolve_via_tns(name: str, credentials: tuple[str, str, str], timeout: float) -> ResolvedObject | None:
    """Ask TNS for a transient's own position. None if TNS has no such object.

    NOT exercised against the live service -- no bot credentials exist yet -- so the
    request shape is written from the TNS 2.0 API manual and tested against a fake
    transport. Treat the first live call as the real test.
    """
    import httpx

    api_key, tns_id, bot_name = credentials
    marker = f'tns_marker{{"tns_id": {tns_id}, "type": "bot", "name": "{bot_name}"}}'
    payload = {"api_key": api_key, "data": f'{{"objname": "{_tns_objname(name)}"}}'}

    response = httpx.post(
        TNS_OBJECT_URL, data=payload, headers={"User-Agent": marker}, timeout=timeout, follow_redirects=True
    )
    response.raise_for_status()
    body = response.json()

    data = (body or {}).get("data") or {}
    if not data or data.get("radeg") is None or data.get("decdeg") is None:
        return None

    return ResolvedObject(
        name=name,
        ra_j2000_hours=float(data["radeg"]) / 15.0,
        dec_j2000_degs=float(data["decdeg"]),
        resolver="tns",
        canonical_name=(f"{data.get('name_prefix', '')}{data.get('objname', '')}".strip() or None),
    )


def _tns_objname(name: str) -> str:
    """TNS wants the designation without its prefix or spaces: `SN 2023ixf` -> `2023ixf`."""
    match = _TNS_NAME.match(name)
    return f"{match.group(2)}{match.group(3)}" if match else name.strip()
