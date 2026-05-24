"""Pydantic schemas para los endpoints Phase 2."""
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field


# -- Credentials ------------------------------------------------------------
class FlexCredentialsRead(BaseModel):
    configured_at: datetime
    query_id: str
    last_rotated_at: datetime


class FlexCredentialsValidate(BaseModel):
    token: str = Field(min_length=10, max_length=512)
    query_id: str = Field(min_length=1, max_length=64)


class FlexCredentialsUpdate(BaseModel):
    token: str | None = Field(default=None, min_length=10, max_length=512)
    query_id: str | None = Field(default=None, min_length=1, max_length=64)


# -- Setup wizard -----------------------------------------------------------
class SetupState(BaseModel):
    step1_credentials: bool = False
    step2_accounts: bool = False
    step3_xmls: bool = False
    step3_n_xmls_uploaded: int = 0
    step4_started_at: datetime | None = None
    step4_job_id: int | None = None
    step4_substeps: dict[str, str] = Field(default_factory=dict)
    setup_completed_at: datetime | None = None


class AccountInWizard(BaseModel):
    ibkr_account_id: str = Field(pattern=r"^U\d{8}$")
    alias: str | None = Field(default=None, max_length=128)
    pct: Decimal = Field(ge=0, le=1, max_digits=5, decimal_places=4)


class SetupStep2Save(BaseModel):
    accounts: list[AccountInWizard] = Field(min_length=1, max_length=20)


class SetupStep4Start(BaseModel):
    pass  # Sin body -- solo dispara el meta-job


class SetupJobStarted(BaseModel):
    job_id: int


# -- Ingest -----------------------------------------------------------------
class IngestTrigger(BaseModel):
    kind: str = Field(pattern=r"^(flex|trm|both)$")


class IngestJobStarted(BaseModel):
    job_id: int


class IngestLogRead(BaseModel):
    id: int
    job_kind: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    items_processed: int | None
    error_message: str | None
    trigger: str
