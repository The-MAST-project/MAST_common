from pydantic import BaseModel, ConfigDict


class ExtendedBaseModel(BaseModel):
    """A BaseModel that survives a JSON round trip through NaN and infinity.

    Plain JSON has no way to spell those, and pydantic's default is to serialise them
    as ``null`` -- which silently turns "the solver could not measure this" into "the
    solver said nothing", two very different things to whoever reads the result later.
    ``ser_json_inf_nan="strings"`` emits ``"NaN"``, ``"Infinity"`` and ``"-Infinity"``
    instead.

    This used to be a pydantic v1 inner ``class Config`` with a ``json_encoders`` float
    hook, plus a ``parse_obj`` override that walked the decoded object rehydrating those
    strings. Both were deprecated and slated for removal in pydantic v3. The config line
    below produces byte-identical output to the old encoder, and the decoder turned out
    to be unnecessary: pydantic v2 already coerces "NaN"/"Infinity"/"-Infinity" to the
    corresponding floats, from python objects and from JSON text alike.
    """

    model_config = ConfigDict(ser_json_inf_nan="strings")
