import types
from typing import Annotated, Literal, Union, get_args, get_origin


def flatten(it):
    for x in it:
        if isinstance(x, (list, tuple)):
            yield from flatten(x)
        else:
            yield x


def literal_values(tp) -> list:
    origin = get_origin(tp)
    if origin is Literal:
        return list(get_args(tp))
    if origin is Annotated:
        # first arg of Annotated is the underlying type
        return literal_values(get_args(tp)[0])
    if origin is Union or origin is getattr(types, "UnionType", None):
        out = []
        for t in get_args(tp):
            out.extend(literal_values(t))
        return out
    return []
