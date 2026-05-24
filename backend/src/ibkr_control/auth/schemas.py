from fastapi_users import schemas
from pydantic import Field


class UserRead(schemas.BaseUser[int]):
    name: str


class UserCreate(schemas.BaseUserCreate):
    name: str = Field(min_length=1, max_length=200)


class UserUpdate(schemas.BaseUserUpdate):
    name: str | None = Field(default=None, min_length=1, max_length=200)
