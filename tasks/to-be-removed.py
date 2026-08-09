from common.api import UnitApi
from common.mast_logging import get_logger

logger = get_logger(__name__)


class Target:
    DEFAULT_PRIORITY = 0

    def __init__(self, name: str, ra: float, dec: float, priority: float = DEFAULT_PRIORITY):
        self.name: str = name
        self.ra: float = ra
        self.dec: float = dec
        self.priority: float = priority
        self.required_units: list[int] = []
        self.units: list[UnitApi] = []
        self.number_of_visits: int = 1  # from config
        self.observing_duration: float  # [seconds] from config

    def snr(self) -> float:
        """
        Calculates the target's Signal-to-Noise-Ratio at the current time
        :return:
        """
