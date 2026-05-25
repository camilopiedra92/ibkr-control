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


# ===== Wizard redesign schemas (v0.2.2) =====


class DetectedAccount(BaseModel):
    ibkr_account_id: str
    suggested_alias: str | None
    account_type: str | None
    account_holder: str | None


class Step2DetectResponse(BaseModel):
    detected_accounts: list[DetectedAccount]
    flex_import_id: int
    ingest_summary: dict  # {n_trades, n_cash_tx, ...} -- opaque shape


class Step2DetectFromXmlResponse(BaseModel):
    detected_accounts: list[DetectedAccount]
    parsed_only: bool = True


class Step2SaveAccountItem(BaseModel):
    ibkr_account_id: str = Field(min_length=9, max_length=12, pattern=r"^U\d{8,11}$")
    alias: str | None = Field(default=None, max_length=200)
    pct: Decimal = Field(ge=Decimal("0"), le=Decimal("1"), decimal_places=4)


class Step2SaveRequest(BaseModel):
    accounts: list[Step2SaveAccountItem] = Field(min_length=1)


class Step3UploadResponse(BaseModel):
    flex_import_temp_id: str
    detected_accounts: list[DetectedAccount]
    new_accounts: list[DetectedAccount]
    period: dict  # {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}
    anyo: int
    sha256: str


class Step3SaveNewAccountsRequest(BaseModel):
    accounts: list[Step2SaveAccountItem] = Field(min_length=1)


class Step3CommitRequest(BaseModel):
    temp_ids: list[str]  # empty list = skip historicos


class Step3CommitResponse(BaseModel):
    flex_import_ids: list[int]
    total_rows_inserted: int


class WizardStateResponse(BaseModel):
    step1_credentials: bool
    step2_accounts: bool
    step3_xmls: bool
    step3_n_xmls_uploaded: int
    setup_completed_at: datetime | None
    detected_accounts: list[DetectedAccount] | None = None
    pending_stash_temp_ids: list[str] = Field(default_factory=list)
