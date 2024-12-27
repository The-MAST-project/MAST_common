import logging
import ipaddress
import socket
from common.mast_logging import init_log
from common.utils import function_name

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
            except socket.herror:
                logger.error(f"cannot resolve {addr} to hostname")
                pass

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

        self.ipaddr: str = str(ipaddr)
        self.hostname: str | None = hostname[0] if isinstance(hostname, list) else None
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
        op = function_name()

        if 'network' not in conf:
            raise ValueError(f"{op}: no 'network' in {conf=}")
        network_conf = conf['network']
        address = network_conf['ipaddr'] if 'ipaddr' in network_conf else network_conf['host'] if 'host' in network_conf else None
        if not address:
            raise Exception(f"both 'ipaddr' and 'host' missing in {network_conf=}")
        port = network_conf['port'] if 'port' in network_conf else 80

        self.network = NetworkDestination(address, port)

