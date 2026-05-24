# Re-export models para que Alembic detecte todas las tablas via Base.metadata
from ibkr_control.auth.models import User  # noqa: F401
from ibkr_control.settings.models import UserSettings  # noqa: F401
from ibkr_control.db.models.accounts import Account  # noqa: F401
from ibkr_control.db.models.participations import Participation  # noqa: F401
from ibkr_control.db.models.flex_credentials import FlexCredentials  # noqa: F401
from ibkr_control.db.models.trm import TrmDay, TrmImport  # noqa: F401
from ibkr_control.db.models.flex_raw import (  # noqa: F401
    FlexImport, Trade, ClosedLot, OpenPositionLot, Transfer, TransferLot, CashTransaction,
)
from ibkr_control.db.base import Base  # noqa: F401

__all__ = [
    "User", "UserSettings", "Account", "Participation", "FlexCredentials",
    "TrmDay", "TrmImport",
    "FlexImport", "Trade", "ClosedLot", "OpenPositionLot", "Transfer", "TransferLot",
    "CashTransaction",
    "Base",
]
