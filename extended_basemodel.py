import math
from typing import Any

from pydantic import BaseModel, ConfigDict, SerializerFunctionWrapHandler, field_serializer


def _spell(value: float) -> str:
    """The JSON spelling of a non-finite float."""
    if math.isnan(value):
        return "NaN"
    return "Infinity" if value > 0 else "-Infinity"


def _stringify_nonfinite(value: Any) -> Any:
    """Replace non-finite floats with their JSON spelling, anywhere in `value`.

    Recursive because a field can be a `list[float]` or a `dict[str, float]`, and a NaN
    inside one is exactly as unspellable in JSON as a NaN on its own.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return _spell(value)
    if isinstance(value, list):
        return [_stringify_nonfinite(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_stringify_nonfinite(v) for v in value)
    if isinstance(value, dict):
        return {k: _stringify_nonfinite(v) for k, v in value.items()}
    return value


class ExtendedBaseModel(BaseModel):
    """A BaseModel that survives a JSON round trip through NaN and infinity.

    Plain JSON has no way to spell those, and pydantic's default is to serialise them
    as ``null`` -- which silently turns "the solver could not measure this" into "the
    solver said nothing", two very different things to whoever reads the result later.
    ``ser_json_inf_nan="strings"`` emits ``"NaN"``, ``"Infinity"`` and ``"-Infinity"``
    instead.

    This used to be a pydantic v1 inner ``class Config`` with a ``json_encoders`` float
    hook, plus a ``parse_obj`` override that walked the decoded object rehydrating those
    strings. Both were deprecated and slated for removal in pydantic v3. The decoder
    turned out to be unnecessary: pydantic v2 already coerces "NaN"/"Infinity"/
    "-Infinity" to the corresponding floats, from python objects and from JSON text
    alike, and ``float("NaN")`` does the same for a caller holding one of these strings.

    ``ser_json_inf_nan`` alone does NOT reproduce the old encoder, though, and the
    serialiser below is what closes the gap. The setting applies only to
    ``model_dump_json()``; ``model_dump(mode="json")`` ignores it and hands back a raw
    float ``nan``. The old ``json_encoders`` hook ran at field level and so covered both.
    That difference is easy to miss because the two are otherwise interchangeable, and it
    is not academic: a "json-mode" dict is one that is supposed to be safe to hand to
    ``json.dumps``, and a bare ``NaN`` token is invalid JSON that non-Python consumers --
    the GUI, for one -- reject outright. Caught by MAST_unit's calibration branch, whose
    status projection dumps with ``mode="json"`` and whose test asserted the round trip.
    """

    model_config = ConfigDict(ser_json_inf_nan="strings")

    @field_serializer("*", mode="wrap", when_used="json")
    def _nonfinite_as_string(self, value: Any, handler: SerializerFunctionWrapHandler) -> Any:
        # A wrap serialiser so the default machinery still runs: `handler` produces the
        # normally-serialised value (nested models to dicts, and so on) and this only
        # rewrites the non-finite floats left in it.
        return _stringify_nonfinite(handler(value))
