import logging
from collections import deque
from threading import Lock
from typing import Callable

from common.mast_logging import init_log
from common.utils import RepeatTimer

logger = logging.Logger("stopping-monitor")
init_log(logger)


class MonitoredPosition:

    def __init__(self, ra: float, dec: float):
        self.ra = ra
        self.dec = dec
        self.epsilon = 0.001

    def __repr__(self):
        return f"MonitoredPosition({self.ra}, {self.dec})"

    def __eq__(self, other):
        return (
            abs(self.ra - other.ra) < self.epsilon
            and abs(self.dec - other.dec) < self.epsilon
        )


class StoppingMonitor:

    def __init__(
        self,
        monitored_entity: str,
        max_len: int,
        sampler: Callable[[], float] | Callable[[], MonitoredPosition],
        interval: float,
        epsilon: float = 0,
    ):
        """
        Monitors an object (e.g. mount, stage, focuser) to decide if it is still moving

        :param monitored_entity: the name of the entity being monitored (i.e.: 'mount', 'stage', etc.)
        :param max_len: number of most recent samples remembered
        :param sampler: returns the current position [callable]
        :param interval: frequency of sampling [sec]
        :param epsilon: if any of the sampled deltas is larger than epsilon, the monitored entity is still moving
        """
        self.queue = deque(maxlen=max_len)
        self.lock = Lock()
        self.timer = RepeatTimer(interval=interval, function=self.sample)
        self.monitored_entity: str = monitored_entity
        self.timer.name = f"{self.monitored_entity}-stopping-monitor"
        self.timer.start()
        self.sampler = sampler
        self.previous: float | None = None
        self.epsilon: float = epsilon
        self.was_moving: bool | None = None

    def sample(self):
        """
        The sampler() returns a (possible composite) value of the current position:
        Expected values from sampler callables:
          - Mount: sum of the axes distance to target
          - Stage: position
          - Focuser: position
        """
        with self.lock:
            self.queue.append(self.sampler())

        is_moving = not self.fully_stopped()
        if self.was_moving is None:
            self.was_moving = is_moving
            return

        if is_moving != self.was_moving:
            logger.info(
                f"{self.monitored_entity}: {'started' if is_moving else 'stopped'} moving"
            )
            self.was_moving = is_moving

    def fully_stopped(self) -> bool:
        """
        If all deltas in the queue are under epsilon, we have stopped
        """
        with self.lock:
            if self.monitored_entity == "mount":
                max_diff_ra = max(x.ra for x in self.queue) - min(
                    x.ra for x in self.queue
                )
                max_diff_dec = max(x.dec for x in self.queue) - min(
                    x.dec for x in self.queue
                )
                logger.info(f"fully_stopped {max_diff_ra=}, {max_diff_dec=}")
            if len(self.queue) != self.queue.maxlen or any(
                x is None for x in self.queue
            ):
                return False
            v = self.queue[0]
            return all([(x == v) for x in self.queue])
