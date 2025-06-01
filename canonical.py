import logging
import traceback
from typing import Any

from pydantic import BaseModel


class ExceptionModel(BaseModel):
    type: str
    message: str
    args: list
    traceback: str | None

    @classmethod
    def from_exception(cls, exception: Exception):
        return cls(
            type=type(exception).__name__,
            message=str(exception),
            args=list(exception.args),
            traceback="".join(
                traceback.format_exception(
                    type(exception), exception, exception.__traceback__
                )
            ),
        )

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
    exception: ExceptionModel | None = None

    @classmethod
    def from_exception(cls, exception: Exception):
        """
        Create a CanonicalResponse with an exception serialized into ExceptionModel.
        """
        return cls(
            exception=ExceptionModel.from_exception(exception),
            errors=None,
            value=None,
        )

    @property
    def is_error(self):
        return self.exception is not None or self.errors is not None

    @property
    def is_exception(self):
        return self.exception is not None

    @property
    def succeeded(self):
        return self.value is not None

    @property
    def failed(self):
        return self.exception is not None or self.errors is not None

    @property
    def failure(self) -> list[str] | str | None:
        if self.exception is not None:
            return str(self.exception)
        elif self.errors:
            return self.errors

    def log(self, _logger: logging.Logger, label: str | None = None):
        if not label:
            label = "CanonicalResponse"
        if self.is_exception:
            _logger.error(f"{label} => exception: {self.exception}")
        elif self.is_error:
            _logger.error(f"{label} => error(s): {self.errors}")


CanonicalResponse_Ok: CanonicalResponse = CanonicalResponse(value="ok")
