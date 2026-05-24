from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


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
