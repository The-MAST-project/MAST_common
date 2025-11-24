import datetime
import json
import random

from pydantic import BaseModel

from common.utils import isoformat_zulu


class Correction(BaseModel):
    time: str
    ra_delta: float
    dec_delta: float

class Corrections(BaseModel):
    phase: str
    target_ra: float
    target_dec: float
    tolerance_ra: float
    tolerance_dec: float
    last_delta: Correction | None = None
    sequence: list[Correction] = []

if __name__ == "__main__":
    corrections = Corrections(
        phase="sky",
        target_ra=1.2345,
        target_dec=2.3456,
        tolerance_ra=0.3,
        tolerance_dec=0.3,
    )
    for _ in range(5):
        corrections.sequence.append(
            Correction(
                time=isoformat_zulu(datetime.datetime.now(datetime.UTC)),
                ra_delta=random.uniform(0, 10),
                dec_delta=random.uniform(0, 10),
            )
        )
    corrections.last_delta = Correction(
        time=isoformat_zulu(datetime.datetime.now(datetime.UTC)), ra_delta=0.13, dec_delta=0.21
    )

    out_json = json.dumps(corrections.model_dump(), indent=2)
    in_json = Corrections.model_validate(json.loads(out_json))
