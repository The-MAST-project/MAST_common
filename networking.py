import logging
import ipaddress
import socket
from common.mast_logging import init_log

logger = logging.getLogger('networking')
init_log(logger, logging.DEBUG)

WEIZMANN_DOMAIN = 'weizmann.ac.il'


class NetworkDestination:

    def __init__(self, addr: str, port: int):
        """


        Parameters
        ----------
        host - Either a quad notation IPv4 address or a
         hostname (fully qualified or short)
        port - a port number
        """
        ipaddr = None
        hostname = None

        try:
            # check if it's already an IPv4 address in quad format
            ipaddr = ipaddress.IPv4Address(addr)
            try:
                hostname = socket.gethostbyaddr(addr)
            except socket.gaierror:
                logger.error(f"cannot resolve {addr=}")
                raise

        except ipaddress.AddressValueError:
            # nope, it's a hostname
            try:
                ipaddr = socket.gethostbyname(addr)
                hostname = addr
            except socket.gaierror:
                if not addr.endswith('.' + WEIZMANN_DOMAIN):
                    full_host = addr + '.' + WEIZMANN_DOMAIN
                    try:
                        ipaddr = socket.gethostbyname(full_host)
                        hostname = full_host
                    except socket.gaierror:
                        logger.error(f"cannot resolve {addr=} or {full_host=}")
                        raise

        self.ipaddr: str = ipaddr
        self.hostname: str = hostname[0]
        self.port: int = port

    def __repr__(self):
        return f"NetworkDestination(ipaddr='{self.ipaddr}', hostname='{self.hostname}', port={self.port})"


class NetworkedDevice:
    """
    A device accessed via an IP connection
    """

    def __init__(self, conf: dict):
        """

        :param conf: A dictionary with keys:
            'network': {
                - 'host'    - [Mandatory] The hostname or IP address of the device
                - 'ipaddr'  - IP address
                - 'port'    - [Optional] Port
            }
        """

        address = conf['ipaddr'] if 'ipaddr' in conf else conf['host'] if 'host' in conf else None
        if not address:
            raise Exception(f"both 'ipaddr' and 'host' missing in {conf=}")
        port = conf['port'] if 'port' in conf else 80

        self.destination = NetworkDestination(address, port)

