import base64
import datetime
import json
import sys
from pathlib import Path
from typing import Any

from common.config import Config

# ---- dump/load helpers -------------------------------------------------

_TAG = "__type__"
_BYTES = "bytes"
_B64 = "b64"

def _default_bytesafe(o: Any):
    if isinstance(o, (bytes, bytearray, memoryview)):
        return {_TAG: _BYTES, _B64: base64.b64encode(bytes(o)).decode("ascii")}
    raise TypeError(f"Not JSON serializable: {type(o)}")

def _object_hook_bytesafe(d: dict):
    if d.get(_TAG) == _BYTES and _B64 in d:
        return base64.b64decode(d[_B64].encode("ascii"))
    return d

def dumps_bytesafe(obj: Any, **kwargs) -> str:
    """json.dumps with automatic bytes→base64 encoding (nested supported)."""
    return json.dumps(obj, default=_default_bytesafe, **kwargs)

def loads_bytesafe(s: str, **kwargs) -> Any:
    """json.loads that restores base64→bytes (nested supported)."""
    return json.loads(s, object_hook=_object_hook_bytesafe, **kwargs)

# ---- file convenience ---------------------------------------------------

def dump_bytesafe(obj: Any, fp, **kwargs) -> None:
    json.dump(obj, fp, default=_default_bytesafe, **kwargs)

def load_bytesafe(fp, **kwargs) -> Any:
    return json.load(fp, object_hook=_object_hook_bytesafe, **kwargs)


def dump(file: Path):

    from common.utils import isoformat_zulu

    cfg = Config(load_from="mongodb")
    if not cfg:
        print("Could not load from MongoDB")
        sys.exit(1)

    assert cfg.origin.local_config_file

    snapshot = cfg.db
    snapshot["tstamp"] = isoformat_zulu(datetime.datetime.now(datetime.UTC))

    file.parent.mkdir(parents=True, exist_ok=True)
    with open(file, "w") as fp:
        dump_bytesafe(snapshot, fp, indent=2)
    print(f"\ndumped to {file.resolve().as_posix()}")

def load(file: Path):
    pass # TODO

def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="mast-config",
        description="MAST configuration handling"
    )
    parser.add_argument('-d', '--dump', metavar="FILE", type=Path, help="Dump the MAST config DB to JSON file")
    parser.add_argument('-l', '--load', metavar="FILE", type=Path, help="Load the MAST config DB from JSON file")
    args = parser.parse_args()

    if args.dump:
        dump(args.dump)
        sys.exit(0)

    if args.load:
        print("load() not implemented yet")
        sys.exit(1)

if __name__ == "__main__":
    main()
