from pydantic import BaseModel, Field
from ..utils import time_stamp

#
# These models are used to store the results of scraping MAST plans.
#
class OwnersModel(BaseModel):
    name: str | None = None
    uuid: str | None = None

class ScrapingResults(BaseModel):
    owners: list[OwnersModel] | None = None
    classifications: list[str] | None = None
    known_classifications: list[str] | None = None
    allocated_units: list[str] | None = None
    scraped_at: str = Field(default_factory=time_stamp)
