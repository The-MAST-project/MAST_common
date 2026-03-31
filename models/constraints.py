from datetime import date, datetime

from pydantic import BaseModel, Field
from enum import StrEnum


class MoonConstraintModel(BaseModel):
    max_phase: float | None = Field(
        default=None,
        ge=0,
        le=100,
        json_schema_extra={
            "ui": {
                "label": "Max Phase",
                "widget": "number",
                "unit": "percent",
                "tooltip": "Maximum moon illumination percentage (0=new moon, 100=full moon)",
            },
            "searchable": "range",
        },
    )
    min_distance: float | None = Field(
        default=None,
        ge=0,
        le=180,
        json_schema_extra={
            "ui": {
                "label": "Min Distance",
                "widget": "number",
                "unit": "degrees",
                "tooltip": "Minimum angular distance from the moon",
            },
            "searchable": "range",
        },
    )


class AirmassConstraintModel(BaseModel):
    max: float | None = Field(
        default=None,
        ge=1,
        le=3.0,
        json_schema_extra={
            "ui": {
                "label": "Max Airmass",
                "widget": "number",
                "tooltip": "Maximum airmass (1.0 = zenith)",
            },
            "searchable": "range",
        },
    )


class SeeingConstraintModel(BaseModel):
    max: float | None = Field(
        default=None,
        ge=0,
        le=5.0,
        json_schema_extra={
            "ui": {
                "label": "Max Seeing",
                "widget": "number",
                "unit": "arcsec",
                "tooltip": "Maximum seeing",
            },
        },
    )


class TimeWindow(BaseModel):
    start_mode: str = Field(
        default="Anytime",
        json_schema_extra={
            "ui": {
                "label": "Start",
                "widget": "radio-date-picker",
                "options": ["Anytime", "Date", "DateTime"],
            },
        },
    )
    end_mode: str = Field(
        default="Anytime",
        json_schema_extra={
            "ui": {
                "label": "End",
                "widget": "radio-date-picker",
                "options": ["Anytime", "Date", "DateTime", "After"],
            },
        },
    )
    start: date | datetime | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Start",
                "widget": "datetime",
                "hidden": True,
            },
            "searchable": "range",
        },
    )
    end: date | datetime | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "End",
                "widget": "datetime",
                "hidden": True,
            },
            "searchable": "range",
        },
    )
    end_after_nights: int | None = Field(
        default=None,
        ge=1,
        le=100,
        json_schema_extra={
            "ui": {
                "label": "After N nights",
                "widget": "number",
                "hidden": True,
            },
        },
    )


class WhenToRepeat(StrEnum):
    only_once = "Only once"
    once_per_night = "Once per night"
    twice_per_night = "Twice per night"
    as_much_as_posible = "As much as possible"


class RepeatsModel(BaseModel):
    every: str = Field(
        default=WhenToRepeat.only_once.value,
        json_schema_extra={
            "ui": {
                "label": "How often per night",
                "widget": "select",
                "summary": True,
                "options": [m.value for m in WhenToRepeat],
                "default_options": WhenToRepeat.only_once.value,
                "section": {"label": "Reschedule", "tooltip": "When and for how many nights should the exposure series be rescheduled?"},
            },
        },
    )
    nights: int = Field(
        default=1,
        ge=1,
        le=100,
        json_schema_extra={
            "ui": {
                "label": "For how many nights",
                "widget": "number",
                "section": "Reschedule",
                "default": 1,
            },
        },
    )


class ConstraintsModel(BaseModel):
    moon: MoonConstraintModel | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Moon",
            },
        },
    )
    airmass: AirmassConstraintModel | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Airmass",
            },
        },
    )
    seeing: SeeingConstraintModel | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Seeing",
            },
        },
    )
    time_window: TimeWindow | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Time Window",
            },
        },
    )
