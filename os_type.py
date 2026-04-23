import platform
from enum import Enum, auto


class OSType(Enum):
    WINDOWS = auto()
    LINUX = auto()
    MACOS = auto()


def get_os() -> OSType:
    system = platform.system()
    if system == "Windows":
        return OSType.WINDOWS
    elif system == "Linux":
        return OSType.LINUX
    elif system == "Darwin":
        return OSType.MACOS
    else:
        raise RuntimeError(f"Unsupported operating system: {system!r}")


CURRENT_OS: OSType = get_os()
