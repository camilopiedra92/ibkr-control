# Re-export models para que Alembic detecte todas las tablas via Base.metadata
from ibkr_control.auth.models import User  # noqa: F401
from ibkr_control.settings.models import UserSettings  # noqa: F401
from ibkr_control.db.models.accounts import Account  # noqa: F401
from ibkr_control.db.models.participations import Participation  # noqa: F401
from ibkr_control.db.models.flex_credentials import FlexCredentials  # noqa: F401
from ibkr_control.db.models.institutions import Institution  # noqa: F401
from ibkr_control.db.models.connections import (  # noqa: F401
    Connection,
    ConnectionIbkrFlex,
)
from ibkr_control.db.models.trm import TrmDay, TrmImport  # noqa: F401
from ibkr_control.db.models.counterparties import Counterparty  # noqa: F401
from ibkr_control.db.models.flex_raw import (  # noqa: F401
    FlexImport,
    FlexImportAccount,
    Trade,
    ClosedLot,
    OpenPositionLot,
    Transfer,
    CashTransaction,
    ChangeInDividendAccrual,
    OpenDividendAccrual,
)
from ibkr_control.db.models.ingest_log import IngestLog  # noqa: F401
from ibkr_control.db.models.organizations import Organization  # noqa: F401
from ibkr_control.db.models.memberships import Membership  # noqa: F401
from ibkr_control.db.models.parties import Party  # noqa: F401
from ibkr_control.db.models.access_grants import AccessGrant  # noqa: F401
from ibkr_control.db.base import Base  # noqa: F401

__all__ = [
    "User",
    "UserSettings",
    "Account",
    "Participation",
    "FlexCredentials",
    "Institution",
    "Connection",
    "ConnectionIbkrFlex",
    "TrmDay",
    "TrmImport",
    "Counterparty",
    "FlexImport",
    "FlexImportAccount",
    "Trade",
    "ClosedLot",
    "OpenPositionLot",
    "Transfer",
    "CashTransaction",
    "ChangeInDividendAccrual",
    "OpenDividendAccrual",
    "IngestLog",
    "Organization",
    "Membership",
    "Party",
    "AccessGrant",
    "Base",
]
