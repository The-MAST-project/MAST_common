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

    startX: int = 0
    startY: int = 0
    numX: int | None = None
    numY: int | None = None

    def __repr__(self) -> str:
        return f"x={self.startX},y={self.startY},w={self.numX},h={self.numY}"
