import socket
from logging import Logger

from pydantic import BaseModel, model_validator

from common.mast_logging import init_log

logger = Logger("mast-config-network")
init_log(logger)


host_to_ipaddr: dict[str, str | None] = {}  # remembers resolved ipaddrs
ipaddr_to_host: dict[str, str | None] = {}  # remembers resolved hosts


class NetworkConfig(BaseModel):
    """
    Network configuration for components that need network connectivity.
    - Either 'host' or 'ipaddr' fields must be provided.
    - An attempt to resolve the other field will be made.
    - 'port' must be a valid TCP port number (1-65535).
    """

    host: str | None = None
    port: int = 80
    ipaddr: str | None = None

    @model_validator(mode="after")
    def validate_network(self):
        if self.host is None and self.ipaddr is None:
            raise ValueError("Either 'host' or 'ipaddr' must be provided.")
        if self.port <= 0 or self.port > 65535:
            raise ValueError("Port must be a valid TCP port number (1-65535).")

        if self.host is None and self.ipaddr is not None:  # if only ipaddr is provided
            if self.ipaddr in ipaddr_to_host:
                self.host = ipaddr_to_host[self.ipaddr]
            else:
                try:
                    self.host, _, _ = socket.gethostbyaddr(self.ipaddr)
                except socket.herror:
                    logger.info(
                        f"validate_network: could not resolve IP address {self.ipaddr}, host will be None"
                    )
                    self.host = None
                ipaddr_to_host[self.ipaddr] = self.host

        elif self.host is not None and self.ipaddr is None:  # if only host is provided
            if self.host in host_to_ipaddr:
                self.ipaddr = host_to_ipaddr[self.host]
            else:
                try:
                    self.ipaddr = socket.gethostbyname(self.host)
                except socket.gaierror:
                    logger.info(
                        f"validate_network: could not resolve host {self.host}, ipaddr will be None"
                    )
                    self.ipaddr = None
                host_to_ipaddr[self.host] = self.ipaddr
        return self
