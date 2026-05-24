from decimal import Decimal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    marginal_rate: Decimal
    timezone: str


class UserSettingsUpdate(BaseModel):
    marginal_rate: Decimal | None = Field(
        default=None,
        gt=Decimal("0"),
        lt=Decimal("1"),
        max_digits=5,
        decimal_places=4,
    )
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone_must_be_zoneinfo(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in available_timezones():
            raise ValueError(f"unknown timezone: {v!r}")
        return v
