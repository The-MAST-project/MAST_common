import logging
from typing import Any

from pydantic import BaseModel


class CanonicalResponse(BaseModel):
    """
    Formalizes API responses.  An API method will return a CanonicalResponse, so that the
     API caller may safely parse it.

    An API method may ONLY return one of the following keys (in decreasing severity): 'errors' or 'value'

    - 'errors' - the method detected one or more errors (no 'value')
    - 'value' - all went well, this is the return value (may be 'None')
    """

    api_version: str = "1.0"  # denotes this as a canonical response
    value: Any | None = None
    errors: list[str] | None = None

    @property
    def is_error(self):
        return self.errors is not None

    @property
    def succeeded(self):
        return self.value is not None

    @property
    def failed(self):
        return self.errors is not None

    @property
    def failure(self) -> list[str] | str | None:
        if self.errors:
            return self.errors

    def log(self, _logger: logging.Logger, label: str | None = None):
        if not label:
            label = "CanonicalResponse"
        if self.is_error:
            _logger.error(f"{label} => error(s): {self.errors}")


CanonicalResponse_Ok: CanonicalResponse = CanonicalResponse(value="ok")
