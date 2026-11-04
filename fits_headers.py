"""The project-standard FITS header cards: what every MAST frame says about where, when and how.

Cards are built here and written by the caller, because frames are written two ways: the
unit's own imagers build a header before saving (`apply_cards`), while PHD2 writes its file
itself and the unit stamps it afterwards (`stamp_fits_file`, which never raises).

    cards = standard_fits_cards(plan_id=..., sky_quality=unit_status.latest.sky_quality)

Keywords keep to the FITS standard's 8 characters -- no HIERARCH, which some readers drop.
The groups:

* **Identity** -- `ORIGIN`, `OBSERVAT` (the site), `TELESCOP` (the machine), `MACHROLE`, from
  the bootstrap config file, so it works on every machine and needs no database.
* **Plan** -- `PLANID`, when the frame was taken for one.
* **Sky quality** -- from `latest.sky_quality`, and only while it is fresh: a stale value
  in a header reads as current, which is worse than none.

  - `SQM*`: the frame a solved focus_on_star took at best focus, if observed within
    `MEASURED_MAX_AGE_SECONDS`.
  - `SQG*`: the guiding session's trailing 1/3/5-minute windows, only while it runs and
    its last guide frame is within `GUIDING_MAX_AGE_SECONDS`.

Each sky-quality group carries its own date (`SQM-DATE`, `SQG-DATE`), so a reader can judge
it without trusting the gate.
"""

from __future__ import annotations

import datetime
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any

from common.mast_logging import get_logger

if TYPE_CHECKING:
    from astropy.io import fits

    from common.models.latest import LatestSkyQuality

logger = get_logger(__name__)

#: ``(value, comment)``, as astropy takes it.
Card = tuple[Any, str]

#: The focus_on_star sky-quality frame goes into headers for this long after it was taken.
MEASURED_MAX_AGE_SECONDS = 3600.0
#: The guiding windows go into headers only while the last guide frame is this recent.
GUIDING_MAX_AGE_SECONDS = 30.0


def fits_date(when: datetime.datetime | str) -> str:
    """FITS's date format: UTC, ``YYYY-MM-DDThh:mm:ss.sss``, no offset."""
    if isinstance(when, str):
        when = datetime.datetime.fromisoformat(when)
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.UTC)
    return when.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def identity_cards() -> dict[str, Card]:
    """Who took the frame. Without a readable config file, only `ORIGIN` and `TELESCOP`."""
    cards: dict[str, Card] = {
        "ORIGIN": ("MAST", "MAsters of Spectra"),
        "TELESCOP": (socket.gethostname(), "the machine that took the frame"),
    }
    try:
        from common.config.local import load_local_config

        local = load_local_config()
    except Exception as ex:  # noqa: BLE001 -- a header must never fail the frame
        logger.warning(f"fits headers: no local config ({ex}); identity cards limited")
        return cards
    cards["OBSERVAT"] = (local.site, "MAST site")
    cards["MACHROLE"] = (local.machine_role, "machine role: unit, spec or control")
    return cards


def _age_seconds(iso: str | None, now: datetime.datetime) -> float | None:
    if not iso:
        return None
    try:
        return (now - datetime.datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return None


def _put(cards: dict[str, Card], key: str, value: Any, comment: str, digits: int | None = None) -> None:
    if value is None:
        return
    cards[key] = (round(value, digits) if digits is not None else value, comment)


def sky_quality_cards(sky_quality: LatestSkyQuality | None, now: datetime.datetime | None = None) -> dict[str, Card]:
    """The fresh parts of ``sky_quality`` as cards; ``{}`` when nothing is fresh."""
    cards: dict[str, Card] = {}
    if sky_quality is None:
        return cards
    now = now or datetime.datetime.now(datetime.UTC)

    m = sky_quality.measured
    age = _age_seconds(m.observed_at, now) if m is not None else None
    if m is not None and m.succeeded and age is not None and -60.0 <= age <= MEASURED_MAX_AGE_SECONDS:
        cards["SQM-DATE"] = (fits_date(m.observed_at), "[UTC] focus_on_star sky-quality frame, mid-exp")
        _put(cards, "SQM-AGE", max(0.0, age), "[s] its age when this header was written", 0)
        _put(cards, "SQMFWHM", m.fwhm_arcsec, "[arcsec] focus star FWHM at best focus", 3)
        _put(cards, "SQMHFD", m.hfd_arcsec, "[arcsec] focus star HFD at best focus", 3)
        _put(cards, "SQMZP", m.zero_point_mag, "[mag] zero point, Gaia G, counts/s", 3)
        _put(cards, "SQMZPSD", m.zero_point_spread_mag, "[mag] zero point spread (MAD sigma)", 3)
        _put(cards, "SQMZPN", m.zero_point_stars or None, "stars in the zero point", None)
        _put(cards, "SQMAIRM", m.airmass, "airmass of the sky-quality frame", 3)
        _put(cards, "SQMBKG", m.background_adu, "[ADU] background, bias included", 1)

    g = sky_quality.while_guiding
    age = _age_seconds(g.last_sample, now) if g is not None else None
    if g is not None and g.running and age is not None and age <= GUIDING_MAX_AGE_SECONDS and g.windows:
        ends = g.windows[0].ends_at or g.last_sample
        cards["SQG-DATE"] = (fits_date(ends), "[UTC] guiding sky-quality windows end")
        for w in g.windows:
            minutes = w.seconds // 60
            _put(cards, f"SQG{minutes}HFD", w.hfd_arcsec, f"[arcsec] guide star HFD, last {minutes} min", 3)
            _put(cards, f"SQG{minutes}TRN", w.transparency, f"transparency vs session start, last {minutes} min", 3)
            cards[f"SQG{minutes}N"] = (w.samples, f"guide frames, last {minutes} min")
            cards[f"SQG{minutes}LOS"] = (w.star_lost, f"guide star lost, last {minutes} min")
    return cards


def standard_fits_cards(
    *,
    plan_id: str | None = None,
    sky_quality: LatestSkyQuality | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Card]:
    """Every project-standard card: `DATE`, identity, plan, and the fresh sky quality."""
    now = now or datetime.datetime.now(datetime.UTC)
    cards: dict[str, Card] = {"DATE": (fits_date(now), "[UTC] when this header was written")}
    cards.update(identity_cards())
    _put(cards, "PLANID", plan_id, "the plan the frame was taken for")
    cards.update(sky_quality_cards(sky_quality, now))
    return cards


def apply_cards(header: fits.Header, cards: dict[str, Card]) -> None:
    """Set ``cards`` on ``header``, overwriting keys already there."""
    for key, (value, comment) in cards.items():
        header[key] = (value, comment)


def stamp_fits_file(path: str | Path | None, cards: dict[str, Card]) -> bool:
    """Add ``cards`` to the primary header of the file at ``path``, in place.

    For frames another program wrote (PHD2). Never raises: the frame is already on disk and
    fine, and losing the stamp must not lose it. Returns whether it was stamped.
    """
    if not path or not cards:
        return False
    from astropy.io import fits

    try:
        with fits.open(path, mode="update", memmap=False) as hdul:
            apply_cards(hdul[0].header, cards)
        return True
    except Exception as ex:  # noqa: BLE001 -- see above
        logger.warning(f"could not stamp {len(cards)} header cards into {path}: {ex}")
        return False
