import datetime

from pydantic import BaseModel, computed_field


class EventModel(BaseModel):
    what: str | None = None
    details: list[str] | None = None

    @computed_field
    @property
    def when(self) -> str:
        return datetime.datetime.now(datetime.UTC).isoformat()
