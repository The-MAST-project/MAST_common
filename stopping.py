from typing import Callable
from threading import Lock
from collections import deque
from common.utils import RepeatTimer


class StoppingMonitor:

    def __init__(self, max_len: int, sampler: Callable[[], float], interval: float, epsilon: float = 0):
        """
        Monitors an object (e.g. mount, stage, focuser) to decide if it is still moving

        :param max_len: number of most recent samples remembered
        :param sampler: returns the current position
        :param interval: frequency of sampling
        :param epsilon: if any of the sample deltas is higher than epsilon, the monitored object is still moving
        """
        self.queue = deque(maxlen=max_len)
        self.lock = Lock()
        self.timer = RepeatTimer(interval=interval, function=self.sample)
        self.timer.start()
        self.sampler = sampler
        self.previous: float | None = None
        self.epsilon: float = epsilon

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

    def stopped_moving(self) -> bool:
        """
        If all deltas in the queue are under epsilon, we have stopped
        """
        with self.lock:
            if len(self.queue) != self.queue.maxlen:
                return False
            return any([x > self.epsilon for x in self.queue])
