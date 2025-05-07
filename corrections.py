import datetime
import json
import random
from typing import List

correction_phases = ["sky", "spec", "guiding", "testing"]


class Correction:
    def __init__(self, time: datetime.datetime, ra_arcsec: float, dec_arcsec: float):
        self.time: datetime.datetime = time
        self.ra_delta: float = ra_arcsec
        self.dec_delta: float = dec_arcsec

    def to_dict(self):
        return {
            "time": datetime.datetime.isoformat(self.time),
            "ra_delta": self.ra_delta,
            "dec_delta": self.dec_delta,
        }

    @classmethod
    def from_dict(cls, data: dict):
        # Convert ISO formatted string back to datetime
        time = datetime.datetime.fromisoformat(data["time"])
        ra_arcsec = data["ra_delta"]
        dec_arcsec = data["dec_delta"]
        return cls(time, ra_arcsec, dec_arcsec)


class Corrections:
    def __init__(
        self,
        phase: str,
        target_ra: float,
        target_dec: float,
        tolerance_ra: float,
        tolerance_dec: float,
    ):
        self.phase = phase
        self.target_ra = target_ra
        self.target_dec = target_dec
        self.tolerance_ra = tolerance_ra
        self.tolerance_dec = tolerance_dec
        self.sequence: List[Correction] = []
        self.last_delta: Correction | None = None

    def to_dict(self):
        return {
            "phase": self.phase,
            "target_ra": self.target_ra,
            "target_dec": self.target_dec,
            "tolerance_ra": self.tolerance_ra,
            "tolerance_dec": self.tolerance_dec,
            "sequence": [correction.to_dict() for correction in self.sequence],
            "last_delta": self.last_delta.to_dict() if self.last_delta else None,
        }

    @classmethod
    def from_dict(cls, data: dict):
        corrs = cls(
            phase=data["phase"],
            target_ra=data["target_ra"],
            target_dec=data["target_dec"],
            tolerance_ra=data["tolerance_ra"],
            tolerance_dec=data["tolerance_dec"],
        )
        # Convert the sequence list of dictionaries into Correction objects
        corrs.sequence = [Correction.from_dict(corr) for corr in data["sequence"]]

        corrs.last_delta = (
            Correction.from_dict(data["last_delta"])
            if "last_delta" in data and data["last_delta"] is not None
            else None
        )
        return corrs


if __name__ == "__main__":
    corrections = Corrections(
        "testing",
        target_ra=1.2345,
        target_dec=2.3456,
        tolerance_ra=0.3,
        tolerance_dec=0.3,
    )
    for i in range(5):
        corrections.sequence.append(
            Correction(
                time=datetime.datetime.now(datetime.UTC),
                ra_arcsec=random.uniform(0, 10),
                dec_arcsec=random.uniform(0, 10),
            )
        )
    corrections.last_delta = Correction(
        datetime.datetime.now(datetime.UTC), ra_arcsec=0.13, dec_arcsec=0.21
    )

    out_json = json.dumps(corrections.to_dict(), indent=2)
    in_json = Corrections.from_dict(json.loads(out_json))
