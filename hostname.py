import os
import socket


def get_hostname() -> str:
    """Return the (possibly virtual) short hostname of the current machine."""
    virt = os.environ.get("MAST_VIRTUAL_HOSTNAME")
    if virt:
        return virt.split(".")[0]
    return socket.gethostname().split(".")[0]
