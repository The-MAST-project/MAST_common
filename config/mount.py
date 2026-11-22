from pydantic import BaseModel


class MountConfig(BaseModel):
    """Configuration for the telescope mount."""

    ascom_driver: str

    # What `Mount.wait_until_settled` asks of the following distance when its caller gives no value.
    # Following distance is wind-driven and gusts to about 6 arcsec, so the default 0.5 is only met in a lull:
    # a unit that guides in wind sets its own. Read each time, so an edit reaches a running unit.
    settle_tolerance_arcsec: float = 0.5
    settle_stable_samples: int = 2
