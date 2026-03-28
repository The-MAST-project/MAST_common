from pydantic import BaseModel, Field

class ScienceModel(BaseModel):
    case: str | None = Field(
        default=None,
        json_schema_extra={"ui": {
            "label": "Case",
            "widget": "textarea",
        }},
    )
    classification: str | None = Field(
        default=None,
        json_schema_extra={"ui": {
            "label": "Classification",
            "widget": "text",
        }},
    )
    