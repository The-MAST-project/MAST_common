from typing import Callable
from threading import Lock
from collections import deque
from common.utils import RepeatTimer
from common.mast_logging import init_log
import logging

logger = logging.Logger('stopping-monitor')
init_log(logger)


class StoppingMonitor:

    def __init__(self, monitored_entity: str, max_len: int, sampler: Callable[[], float], interval: float, epsilon: float = 0):
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
        # self.timer.start()
        self.sampler = sampler
        self.previous: float | None = None
        self.epsilon: float = epsilon
        self.was_moving: bool | None = None
        self.monitored_entity: str = monitored_entity

    def sample(self):
        """
        The sampler() returns a (possible composite) value of the current position:
        Expected values from sampler callables:
          - Mount: sum of the axes distance to target
          - Stage: position
          - Focuser: position
        """
        current = self.sampler()
        if self.previous:
            delta = abs(self.previous - current)
            self.previous = current
            with self.lock:
                self.queue.append(delta)
        else:
            self.previous = current

        is_moving = not self.fully_stopped()
        if self.was_moving is None:
            self.was_moving = is_moving
            return

        if is_moving != self.was_moving:
            logger.info(f"{self.monitored_entity}: {'started' if is_moving else 'stopped'} moving")
            self.was_moving = is_moving

    def fully_stopped(self) -> bool:
        """
        If all deltas in the queue are under epsilon, we have stopped
        """
        with self.lock:
            # if self.monitored_entity == 'stage':
            #     logger.info(f"fully_stopped ({self.monitored_entity}): {self.queue}")
            if len(self.queue) != self.queue.maxlen:
                return False
            return not any([(x > self.epsilon) for x in self.queue])
