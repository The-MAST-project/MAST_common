from typing import NamedTuple


class CameraBinning(NamedTuple):
    x: int
    y: int

    def __repr__(self) -> str:
        return f"{self.x}x{self.y}"


class CameraRoi(NamedTuple):
    """
    An ASCOM compatible region-of-interest
    """

    start_x: int = 0
    start_y: int = 0
    num_x: int | None = None
    num_y: int | None = None

    def __repr__(self) -> str:
        return f"x={self.start_x},y={self.start_y},w={self.num_x},h={self.num_y}"
