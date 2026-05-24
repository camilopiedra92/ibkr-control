# Re-export models para que Alembic detecte todas las tablas via Base.metadata
from ibkr_control.auth.models import User  # noqa: F401
from ibkr_control.settings.models import UserSettings  # noqa: F401
from ibkr_control.db.base import Base  # noqa: F401
