import logging
import ipaddress
import socket
from common.utils import init_log

logger = logging.getLogger('networking')
init_log(logger, logging.DEBUG)

WEIZMANN_DOMAIN = 'weizmann.ac.il'


class NetworkDestination:

    def __init__(self, host: str, port: int):
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
            ipaddr = ipaddress.IPv4Address(host)
            try:
                hostname = socket.gethostbyaddr(ipaddr)
            except socket.gaierror:
                logger.error(f"cannot resolve {ipaddr=}")
                raise

        except ipaddress.AddressValueError:
            # nope, it's a hostname
            try:
                ipaddr = socket.gethostbyname(host)
                hostname = host
            except socket.gaierror:
                if not host.endswith('.' + WEIZMANN_DOMAIN):
                    full_host = host + '.' + WEIZMANN_DOMAIN
                    try:
                        ipaddr = socket.gethostbyname(full_host)
                        hostname = full_host
                    except socket.gaierror:
                        logger.error(f"cannot resolve {host=} or {full_host=}")
                        raise

        self.ipaddr: str = ipaddr
        self.hostname: str = hostname
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
                - 'port'    - [Optional] Port
            }
        """

        if 'network' not in conf:
            raise Exception(f"Missing 'network' key in {conf}")
        if 'host' not in conf['network']:
            raise Exception(f"Missing 'network.host' key in {conf}")
        if 'port' not in conf['network']:
            conf['network']['port'] = 80

        self.destination = NetworkDestination(conf['network']['host'], conf['network']['port'])
