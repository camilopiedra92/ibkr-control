"""tier1_baseline

Squash de las 5 revisiones de la cadena SP1 en UN baseline pristino que
reproduce el estado ACTUAL de ``Base.metadata`` (W1 Task 1, T1-D14 amended: el
baseline es mutable hasta el primer deploy — no hay deployment, la DB dev es
descartable, el usuario recarga XMLs por wizard). Cadena vieja squasheada:

  05943d9efcdb (saas baseline + RLS) -> a1f2c3d4e5b6 (system function)
  -> 7fdaf6528762 (apscheduler_jobs) -> 544a0b2c362c (db hardening:
  FK indexes + RESTRICT + created_at + close_datetime comment)
  -> fd27737af54e (account multihome: per-org uniqueness).

El squash original (W1 Task 1) fue PURAMENTE MECÁNICO (byte-equivalente a la
cadena de 5). **Amendment #1 (W1 Task 3, T1-D14: el baseline es MUTABLE hasta el
primer deploy):** este MISMO archivo y revision id se amendan IN PLACE para
agregar el modelo de connections — tablas nuevas ``institutions`` (control
plane, sin RLS), ``connections`` + ``connection_ibkr_flex`` (org-scoped, RLS,
patrón Plaid Item con subtipo enforced en SQL), la columna de linaje
``connection_id`` en ``flex_imports`` + ``ingest_log``, el seed de la
institución ``ibkr``, y las 2 tablas nuevas sumadas al snapshot RLS
``_ORG_SCOPED_TABLES``. La sección autogenerada se regeneró canónicamente
(container, DB virgen) y se trasplantó entre los marcadores.

**Amendment #2 (W1 Task 4):** el cuerpo de ``system_credentialed_org_ids()`` se
repunta de ``flex_credentials`` a ``connections`` (``WHERE provider_type =
'ibkr_flex' AND status <> 'disabled'``) — el cron Flex ahora itera connections
activas, no las credenciales legacy. Frozen idéntico a
``db/rls.py::system_enum_function_sql()``.

**Amendment #3 (W1 Task 7):** ``flex_credentials`` (tabla + modelo + entrada en
el snapshot RLS) se eliminó por completo — todos los consumidores ya leen las
tablas ``connections`` (Tasks 4-6). El baseline ya no la crea: el delta fue una
sustracción a mano (los bloques de create_table/index/drop eran autocontenidos)
validada por el drift test (Base.metadata == schema migrado).

**Amendment #4 (W2 — securities master, T1-D7/D8/D9):** se agregan las tablas de
control plane ``instruments`` + ``instrument_identifiers`` (globales, SIN RLS —
NO van en ``_ORG_SCOPED_TABLES``, como ``trm_days``/``institutions``) y la
columna ``instrument_id`` FK ``RESTRICT`` en los 7 hechos: NOT NULL en
trades/closed_lots/open_position_lots/accruals×2 (CR-1: conid 100% presente),
nullable en cash_transactions/transfers (la Flex Query 2024 no trae conid en
cash; los FOP STK tampoco — dato corregido en amendment #6: los FOP STK SÍ
traen conid, era un error del grep inicial). Se agrega el índice
``(account_id, instrument_id)``
en los 5 creators y ``instrument_id`` simple en cash/transfers. La sección
autogenerada se regeneró canónicamente (container, DB virgen) y se trasplantó
entre los marcadores — los columnas/índices/FK se fold-earon dentro de cada
``create_table`` (baseline limpio, no ``add_column``). Las 2 tablas globales
reciben el blanket GRANT del baseline (sección hand-written 2) — sin policy RLS.

**Amendment #5 (W3 — restatement log, T1-D10..D13):** se agrega la tabla
org-scoped ``restatement_log`` (señal auditable de mutación material: DO UPDATE
en snapshot tables + sibling rows de closed_lots; poblada SOLO por el persister,
detection-only). DDL en la sección autogenerada (regenerada canónicamente:
container, DB virgen, splice entre marcadores) + entrada en
``_ORG_SCOPED_TABLES`` (su policy RLS sale del loop de la sección hand-written
3) — los tres en lockstep con ``db/rls.py::ORG_SCOPED_TABLES`` (el guard
``test_org_scoped_snapshot_matches_live_ssot`` lo exige).

**Amendment #6 (transfer↔instrument lineage, TL-D1/D4/D5 — spec 2026-06-11):**
los transfers de securities pasan a CREATORS del securities master (el split
creator/resolver va por calidad de evidencia, no por tag). Delta: columnas
``transfers.asset_class`` (NOT NULL) + ``transfers.conid`` (nullable) +
``cash_transactions.conid`` (nullable, fidelidad de fuente TL-D4) y el CHECK
bicondicional ``ck_transfers_transfer_cash_iff_no_instrument``
(``(asset_class = 'CASH') = (instrument_id IS NULL)``, TL-D5). Amendment #6
regenerado canónicamente en container (2026-06-11: autogenerate temporal vía
servicio migrate contra DB virgen, splice entre marcadores, mismo revision id)
y validado por el drift test (Base.metadata == schema migrado). SIN cambios RLS
(``_ORG_SCOPED_TABLES`` intacto).

**Amendment #7 (precisión decimal exacta, PD-1/PD-2 — spec 2026-06-12):** las
27 columnas NUMERIC cuyo valor proviene del XML Flex (hechos x7 +
``instruments.multiplier``) pasan de ``Numeric(p,s)`` a ``NUMERIC``
unconstrained — la fuente IBKR no documenta precisión (censo real: hasta 9
decimales en ``ibCommission``/``positionValue`` vs scale 4 declarada; Migration
F refutada) y la scale declarada redondeaba en el write path (114 falsos
positivos del golden W3). Política nueva: scale declarada solo con contrato de
fuente documentado (``trm.value_cop``, ``participations.pct`` se quedan).
Regenerado canónicamente en container (autogenerate temporal contra DB virgen,
splice entre marcadores, mismo revision id). SIN cambios RLS.

**Amendment #8 (SP2 — authorization):** (a) ``ck_access_grants_valid_range``
pasa a ``valid_to >= valid_from`` (vigencia half-open [from, to): intervalo
vacío legal para revoke same-day, SP2-D8); (b) ``restatement_log.account_id``
FK RESTRICT NOT NULL + índice ``(organization_id, account_id)`` (filtro
party-scoped del grantee, SP2-D9); (c) función SECURITY DEFINER
``authz_grant_party_ids(p_user_id, p_org_id)`` (bootstrap del resolver de
autorización — frozen idéntico a ``db/rls.py::authz_grant_function_sql()``,
SP2-D3). Regenerado canónicamente en container (autogenerate temporal contra
DB virgen, splice entre markers).

**Amendment #9 (ingest-completeness hardening, IC-1/2/3 — spec 2026-07-06):**
(a) IC-1 — columnas nullable de captura fiscal en ``cash_transactions``
(``settle_date``/``report_date``/``ex_date``/``issuer_country``/``action_id`` +
``raw_attrs`` JSONB server_default ``'{}'``): source data que el parser ya ve
(``action_id`` linkea dividendo↔WHT para el descuento Art. 254 ET); pobladas por
el persister en un PR follow-up. (b) IC-2 — ``instruments.issuer_country``
(nullable): país emisor canónico. (c) IC-3 — no-solapamiento de vigencias en
``participations`` vía columna generada ``validity``
(``daterange(valid_from, valid_to, '[)')`` STORED, single source of truth) + un
``EXCLUDE USING gist`` column-based ``participations_no_overlap`` sobre
``(organization_id =, party_id =, account_id =, validity &&)``. La columna la
detecta autogenerate; el ``CREATE EXTENSION IF NOT EXISTS btree_gist`` (owner, no
app_rls) y el ``EXCLUDE`` se aplican inline (autogenerate no los expresa). Spike
de idempotencia previo: segundo autogenerate con diff vacío (solo el falso
positivo apscheduler que el drift test filtra). Regenerado canónicamente en
container. SIN cambios RLS (``_ORG_SCOPED_TABLES`` intacto).

El DDL de ``upgrade()`` hasta el marcador ``end Alembic commands`` es
autogenerado canónicamente (container, DB virgen, ``alembic revision
--autogenerate``). Las SECCIONES HAND-WRITTEN que autogenerate NO captura
(RLS, rol app_rls, apscheduler_jobs, función de sistema, policy de access_grants,
seed de institutions) se re-aplican INLINE al final del upgrade — CERO imports de
``ibkr_control.db.rls`` para DDL (regla T1-D14: las migraciones nunca importan
builders vivos; el baseline congela todo inline). ÚNICA excepción: el VALOR del
password de ``app_rls`` se lee del env ``APP_RLS_PASSWORD`` inline (mismo
mecanismo que el baseline viejo via ``app_rls_password()``: es un VALOR de
runtime, no DDL estructural — debe resolverse al aplicar, no hardcodearse).

apscheduler_jobs NO vive en ``Base.metadata`` (tabla runtime de APScheduler) —
el drift test la ignora; se crea aquí (IF NOT EXISTS, dirty-volume safe) porque
app_rls no tiene CREATE.

Revision ID: a9977ac077e5
Revises:
Create Date: 2026-06-10 16:31:36.300280

"""

import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a9977ac077e5"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------------------------------------------------------------------------
# Hand-written constants frozen inline (T1-D14: no live builder imports).
# Snapshot of db/rls.py::ORG_SCOPED_TABLES + APP_ROLE at the time of this
# baseline. flex_credentials fue eliminada (amendment #3, W1 Task 7) — el snapshot
# ya solo cubre las tablas connections. Si la lista viva diverge, el drift test NO
# lo atrapa (las policies no están en Base.metadata) — test_rls.py es la red de
# comportamiento; test_tier1_baseline el lockstep guard.
# ---------------------------------------------------------------------------
_APP_ROLE = "app_rls"
_ORG_SCOPED_TABLES = [
    "accounts",
    "parties",
    "participations",
    "connections",
    "connection_ibkr_flex",
    "counterparties",
    "flex_imports",
    "flex_import_accounts",
    "trades",
    "closed_lots",
    "open_position_lots",
    "transfers",
    "cash_transactions",
    "change_in_dividend_accruals",
    "open_dividend_accruals",
    "ingest_log",
    "restatement_log",
]
# Reader convention (db/rls.py): NULLIF(...,'') so an unset GUC -> NULL ->
# default-deny (clean), not a 22P02 error. Frozen verbatim.
_CURRENT_ORG = "NULLIF(current_setting('app.current_org', true), '')::bigint"
_CURRENT_USER = "NULLIF(current_setting('app.current_user', true), '')::bigint"


def _app_rls_password() -> str:
    """Password for the app_rls login role — read from env at apply time.

    Replica EXACTA del mecanismo del baseline viejo (db/rls.py::app_rls_password):
    es un VALOR de runtime (no DDL estructural), interpolado en el string literal
    de ``CREATE ROLE ... PASSWORD '<value>'``. Dev/test default ``app_rls_pw``;
    prod inyecta un secreto real via ``APP_RLS_PASSWORD`` (SP4/deploy). Se lee del
    env INLINE (no hay import de builders vivos). Fail-loud si trae una comilla
    simple (rompería el literal SQL) — rechazar, no escapar.
    """
    pw = os.environ.get("APP_RLS_PASSWORD", "app_rls_pw")
    if "'" in pw:
        raise ValueError(
            "APP_RLS_PASSWORD must not contain a single quote "
            "(it is embedded in a SQL string literal in the CREATE ROLE DDL)"
        )
    return pw


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "institutions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_institutions")),
        sa.UniqueConstraint("code", name=op.f("uq_institutions_code")),
        comment="Control plane (global, sin RLS, como trm_days): catálogo de instituciones. Seeded por migración, no input de usuario.",
    )
    op.create_table(
        "instruments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("asset_class", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=True),
        sa.Column("multiplier", sa.Numeric(), nullable=True),
        sa.Column("issuer_country", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
        comment="Control plane (global, sin RLS): securities master. Identidad externa en instrument_identifiers; symbol/atributos last-seen del XML IBKR. Escrito solo por el persister (T1-D9).",
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("setup_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "setup_progress",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_ingest_trigger_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint("type IN ('personal', 'firm')", name=op.f("ck_organizations_type")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        comment="Tenant boundary. type personal=hogar, firm=estudio contable.",
    )
    op.create_table(
        "trm_days",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("value_cop", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("vigencia_desde", sa.Date(), nullable=False),
        sa.Column("vigencia_hasta", sa.Date(), nullable=False),
        sa.Column(
            "source",
            sa.String(),
            server_default=sa.text("'dian_socrata_ceyp_9c7c'"),
            nullable=False,
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("date", name=op.f("pk_trm_days")),
        comment="1 row por día calendario, expandido desde vigencia_desde..vigencia_hasta",
    )
    op.create_index(op.f("ix_trm_days_date"), "trm_days", ["date"], unique=False)
    op.create_table(
        "trm_imports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("date_range_from", sa.Date(), nullable=False),
        sa.Column("date_range_to", sa.Date(), nullable=False),
        sa.Column("n_rows_api", sa.Integer(), nullable=False),
        sa.Column("n_days_expanded", sa.Integer(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trm_imports")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=1024), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_table(
        "accounts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("ibkr_account_id", sa.String(), nullable=False),
        sa.Column("alias", sa.String(), nullable=True),
        sa.Column("currency", sa.String(), server_default=sa.text("'USD'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_accounts_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
        sa.UniqueConstraint(
            "organization_id", "ibkr_account_id", name="uq_accounts_org_ibkr_account_id"
        ),
        comment="Multi-home (patrón Plaid, spec 2026-06-10): la misma cuenta broker puede existir en N orgs, una fila por org — universos aislados, el SaaS no verifica exclusividad de propiedad. Dentro de un org sigue siendo identidad compartida: sin user_id, propiedad vía participations (la conjunta es 50/50).",
    )
    op.create_table(
        "connections",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("institution_id", sa.BigInteger(), nullable=False),
        sa.Column("provider_type", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "last_sync_status IS NULL OR last_sync_status IN ('ok', 'failed')",
            name=op.f("ck_connections_last_sync_status"),
        ),
        sa.CheckConstraint(
            "provider_type IN ('ibkr_flex')", name=op.f("ck_connections_provider_type")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'degraded', 'reauth_required', 'disabled')",
            name=op.f("ck_connections_status"),
        ),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
            name=op.f("fk_connections_institution_id_institutions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_connections_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connections")),
        sa.UniqueConstraint("id", "provider_type", name="uq_connections_id_provider_type"),
        comment="Org-scoped (RLS). Vínculo org<->institución (patrón Plaid Item). Config provider-specific en la detail 1:1 (connection_ibkr_flex). status SOLO vía ingest/connection_state.py (W4).",
    )
    op.create_index(
        op.f("ix_connections_institution_id"), "connections", ["institution_id"], unique=False
    )
    op.create_index(
        op.f("ix_connections_organization_id"), "connections", ["organization_id"], unique=False
    )
    op.create_table(
        "counterparties",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("source_label", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_counterparties_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_counterparties")),
        sa.UniqueConstraint(
            "organization_id", "external_id", name="uq_counterparties_org_external"
        ),
        comment="Identidad externa org-scoped (espeja accounts). RLS.",
    )
    op.create_index(
        op.f("ix_counterparties_organization_id"),
        "counterparties",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "instrument_identifiers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), nullable=False),
        sa.Column("id_type", sa.String(), nullable=False),
        sa.Column("id_value", sa.String(), nullable=False),
        sa.CheckConstraint(
            "id_type IN ('conid', 'isin', 'cusip', 'figi')",
            name=op.f("ck_instrument_identifiers_id_type"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_instrument_identifiers_instrument_id_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instrument_identifiers")),
        sa.UniqueConstraint(
            "id_type", "id_value", name="uq_instrument_identifiers_id_type_id_value"
        ),
        comment="Identidad externa del instrumento, una fila por (tipo, valor). Multi-provider day-1 (T1-D7): conid IBKR hoy; isin cuando el XML lo trae; cusip/figi reservados.",
    )
    op.create_index(
        op.f("ix_instrument_identifiers_instrument_id"),
        "instrument_identifiers",
        ["instrument_id"],
        unique=False,
    )
    op.create_table(
        "memberships",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member')", name=op.f("ck_memberships_role")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_memberships_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_memberships_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "organization_id", name=op.f("pk_memberships")),
        comment="User<->org con rol. Identidad; sin org-RLS (se lee para resolver contexto).",
    )
    op.create_table(
        "parties",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("tax_id", sa.String(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_parties_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_parties_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parties")),
        comment="Persona fiscal (contribuyente). Duena de cuentas via participations. Separada de User: puede no tener login (conyuge, cliente del estudio). Org-scoped (organization_id, RLS).",
    )
    op.create_index(
        op.f("ix_parties_organization_id"), "parties", ["organization_id"], unique=False
    )
    op.create_index(op.f("ix_parties_user_id"), "parties", ["user_id"], unique=False)
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "marginal_rate",
            sa.Numeric(precision=5, scale=4),
            server_default="0.3900",
            nullable=False,
        ),
        sa.Column(
            "timezone", sa.String(length=64), server_default="America/Bogota", nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_settings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_settings")),
    )
    op.create_table(
        "access_grants",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("grantor_party_id", sa.BigInteger(), nullable=False),
        sa.Column("grantee_organization_id", sa.BigInteger(), nullable=True),
        sa.Column("grantee_user_id", sa.BigInteger(), nullable=True),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(), server_default=sa.text("'read_only'"), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('read_only')", name=op.f("ck_access_grants_role")),
        sa.CheckConstraint(
            "(grantee_organization_id IS NOT NULL) <> (grantee_user_id IS NOT NULL)",
            name=op.f("ck_access_grants_grantee_arc"),
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name=op.f("ck_access_grants_valid_range")
        ),
        sa.ForeignKeyConstraint(
            ["grantee_organization_id"],
            ["organizations.id"],
            name=op.f("fk_access_grants_grantee_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["grantee_user_id"],
            ["users.id"],
            name=op.f("fk_access_grants_grantee_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["grantor_party_id"],
            ["parties.id"],
            name=op.f("fk_access_grants_grantor_party_id_parties"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_access_grants_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_grants")),
        comment="Grant cross-org party-scoped. RLS especial (grantor-org OR grantee).",
    )
    op.create_index(
        op.f("ix_access_grants_grantee_organization_id"),
        "access_grants",
        ["grantee_organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_access_grants_grantee_user_id"), "access_grants", ["grantee_user_id"], unique=False
    )
    op.create_index(
        op.f("ix_access_grants_grantor_party_id"),
        "access_grants",
        ["grantor_party_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_access_grants_organization_id"), "access_grants", ["organization_id"], unique=False
    )
    op.create_table(
        "connection_ibkr_flex",
        sa.Column("connection_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "provider_type", sa.String(), server_default=sa.text("'ibkr_flex'"), nullable=False
        ),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("token_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("query_id", sa.String(), nullable=False),
        sa.Column(
            "last_rotated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider_type = 'ibkr_flex'", name=op.f("ck_connection_ibkr_flex_provider_type")
        ),
        sa.ForeignKeyConstraint(
            ["connection_id", "provider_type"],
            ["connections.id", "connections.provider_type"],
            name=op.f("fk_connection_ibkr_flex_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_connection_ibkr_flex_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("connection_id", name=op.f("pk_connection_ibkr_flex")),
        comment="Detail 1:1 tipada del provider ibkr_flex (T1-D1, cero JSONB). Org-scoped (RLS). Subtipo enforced por FK compuesto + CHECK (T1-D2).",
    )
    op.create_index(
        op.f("ix_connection_ibkr_flex_organization_id"),
        "connection_ibkr_flex",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "flex_imports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("connection_id", sa.BigInteger(), nullable=True),
        sa.Column("anyo", sa.Integer(), nullable=False),
        sa.Column("xml_hash", sa.String(), nullable=False),
        sa.Column("xml_size_bytes", sa.Integer(), nullable=False),
        sa.Column("xml_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("period_covered_from", sa.Date(), nullable=False),
        sa.Column("period_covered_to", sa.Date(), nullable=False),
        sa.Column("year_status", sa.String(), server_default=sa.text("'rolling'"), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("n_observed_trades", sa.Integer(), nullable=True),
        sa.Column("n_observed_lots_closed", sa.Integer(), nullable=True),
        sa.Column("n_observed_open_lots", sa.Integer(), nullable=True),
        sa.Column("n_observed_cash_tx", sa.Integer(), nullable=True),
        sa.Column("n_observed_dividends", sa.Integer(), nullable=True),
        sa.Column("n_observed_transfers", sa.Integer(), nullable=True),
        sa.Column("n_new_trades", sa.Integer(), nullable=True),
        sa.Column("n_new_lots_closed", sa.Integer(), nullable=True),
        sa.Column("n_new_open_lots", sa.Integer(), nullable=True),
        sa.Column("n_new_cash_tx", sa.Integer(), nullable=True),
        sa.Column("n_new_dividends", sa.Integer(), nullable=True),
        sa.Column("n_new_transfers", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'ok'"), nullable=False),
        sa.Column("poison_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "source IN ('web_service', 'manual_upload')", name=op.f("ck_flex_imports_source")
        ),
        sa.CheckConstraint("status IN ('ok', 'poison')", name=op.f("ck_flex_imports_status")),
        sa.CheckConstraint(
            "year_status IN ('rolling', 'sealed')", name=op.f("ck_flex_imports_year_status")
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_flex_imports_connection_id_connections"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_flex_imports_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_flex_imports")),
        sa.UniqueConstraint("organization_id", "xml_hash", name="uq_flex_imports_org_xml_hash"),
    )
    op.create_index(
        op.f("ix_flex_imports_connection_id"), "flex_imports", ["connection_id"], unique=False
    )
    op.create_index(
        op.f("ix_flex_imports_organization_id"), "flex_imports", ["organization_id"], unique=False
    )
    op.create_table(
        "ingest_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("connection_id", sa.BigInteger(), nullable=True),
        sa.Column("job_kind", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("items_processed", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "job_kind IN ('flex', 'trm', 'manual_refresh', 'manual_upload', 'setup_initial')",
            name=op.f("ck_ingest_log_job_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'ok', 'failed')", name=op.f("ck_ingest_log_status")
        ),
        sa.CheckConstraint(
            "trigger IN ('cron', 'manual', 'wizard')", name=op.f("ck_ingest_log_trigger")
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_ingest_log_connection_id_connections"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_ingest_log_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingest_log")),
    )
    op.create_index(
        op.f("ix_ingest_log_connection_id"), "ingest_log", ["connection_id"], unique=False
    )
    op.create_index(
        "ix_ingest_log_org_started_at",
        "ingest_log",
        ["organization_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_index(
        op.f("ix_ingest_log_organization_id"), "ingest_log", ["organization_id"], unique=False
    )
    op.create_table(
        "participations",
        sa.Column("party_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("pct", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column(
            "validity",
            postgresql.DATERANGE(),
            sa.Computed("daterange(valid_from, valid_to, '[)')", persisted=True),
            nullable=False,
        ),
        sa.CheckConstraint("pct >= 0 AND pct <= 1", name=op.f("ck_participations_pct_range")),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name=op.f("ck_participations_valid_range")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_participations_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_participations_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["party_id"],
            ["parties.id"],
            name=op.f("fk_participations_party_id_parties"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "party_id", "account_id", "valid_from", name=op.f("pk_participations")
        ),
        comment="Propiedad fiscal: Party posee Account con pct (SCD-2). Org-scoped.",
    )
    op.create_index(
        op.f("ix_participations_organization_id"),
        "participations",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "cash_transactions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("flex_import_id", sa.BigInteger(), nullable=True),
        sa.Column("transaction_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), nullable=True),
        sa.Column("conid", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("amount_usd", sa.Numeric(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=True),
        sa.Column("settle_date", sa.Date(), nullable=True),
        sa.Column("report_date", sa.Date(), nullable=True),
        sa.Column("ex_date", sa.Date(), nullable=True),
        sa.Column("issuer_country", sa.String(), nullable=True),
        sa.Column("action_id", sa.String(), nullable=True),
        sa.Column(
            "raw_attrs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_cash_transactions_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["flex_import_id"],
            ["flex_imports.id"],
            name=op.f("fk_cash_transactions_flex_import_id_flex_imports"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_cash_transactions_instrument_id_instruments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_cash_transactions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cash_transactions")),
        sa.UniqueConstraint(
            "organization_id", "transaction_id", name="uq_cash_transactions_org_transaction_id"
        ),
        comment="Account-scoped. Visibilidad vía participations; sin user_id. transaction_id único POR TENANT (multi-home, spec 2026-06-10): la misma cuenta broker puede existir en N orgs, cada org tiene su copia de los hechos.",
    )
    op.create_index(op.f("ix_cash_transactions_date"), "cash_transactions", ["date"], unique=False)
    op.create_index(
        op.f("ix_cash_transactions_flex_import_id"),
        "cash_transactions",
        ["flex_import_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cash_transactions_instrument_id"),
        "cash_transactions",
        ["instrument_id"],
        unique=False,
    )
    op.create_table(
        "change_in_dividend_accruals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("flex_import_id", sa.BigInteger(), nullable=True),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("conid", sa.String(), nullable=True),
        sa.Column("isin", sa.String(), nullable=True),
        sa.Column("issuer_country", sa.String(), nullable=True),
        sa.Column("currency", sa.String(), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=True),
        sa.Column("pay_date", sa.Date(), nullable=True),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("accrual_date", sa.Date(), nullable=True),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("gross_rate_per_share", sa.Numeric(), nullable=True),
        sa.Column("gross_amount_usd", sa.Numeric(), nullable=False),
        sa.Column("tax_usd", sa.Numeric(), nullable=False),
        sa.Column("fee_usd", sa.Numeric(), nullable=True),
        sa.Column("net_amount_usd", sa.Numeric(), nullable=False),
        sa.Column("action_id", sa.String(), nullable=True),
        sa.Column("asset_category", sa.String(), nullable=True),
        sa.Column("sub_category", sa.String(), nullable=True),
        sa.Column("level_of_detail", sa.String(), nullable=True),
        sa.Column("code", sa.String(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "raw_attrs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_change_in_dividend_accruals_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["flex_import_id"],
            ["flex_imports.id"],
            name=op.f("fk_change_in_dividend_accruals_flex_import_id_flex_imports"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_change_in_dividend_accruals_instrument_id_instruments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_change_in_dividend_accruals_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_in_dividend_accruals")),
        sa.UniqueConstraint(
            "account_id",
            "conid",
            "ex_date",
            "pay_date",
            "accrual_date",
            "report_date",
            "action_id",
            "code",
            name="uq_change_in_dividend_accruals_natural_key",
        ),
        comment="Account-scoped. Visibilidad vía participations; sin user_id. Identidad por natural key compuesto (uq_change_in_dividend_accruals_natural_key); sin transaction_id.",
    )
    op.create_index(
        op.f("ix_change_in_dividend_accruals_account_id_instrument_id"),
        "change_in_dividend_accruals",
        ["account_id", "instrument_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_change_in_dividend_accruals_account_id_symbol"),
        "change_in_dividend_accruals",
        ["account_id", "symbol"],
        unique=False,
    )
    op.create_index(
        op.f("ix_change_in_dividend_accruals_flex_import_id"),
        "change_in_dividend_accruals",
        ["flex_import_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_change_in_dividend_accruals_organization_id"),
        "change_in_dividend_accruals",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_change_in_dividend_accruals_report_date"),
        "change_in_dividend_accruals",
        ["report_date"],
        unique=False,
    )
    op.create_table(
        "flex_import_accounts",
        sa.Column("flex_import_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_flex_import_accounts_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["flex_import_id"],
            ["flex_imports.id"],
            name=op.f("fk_flex_import_accounts_flex_import_id_flex_imports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_flex_import_accounts_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "flex_import_id", "account_id", name=op.f("pk_flex_import_accounts")
        ),
        comment="Procedencia cuenta<->import: cada cuenta observada en un FlexImport (incluidas las AccountInformation-only sin hechos). Org-scoped (RLS). Hecho de primera clase: que cuentas trajo el import de un org. El aislamiento cross-tenant lo da RLS por organization_id; esta tabla sirve al wizard para scopear que cuentas reclama un org via sus imports.",
    )
    op.create_index(
        op.f("ix_flex_import_accounts_organization_id"),
        "flex_import_accounts",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "open_dividend_accruals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("flex_import_id", sa.BigInteger(), nullable=True),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("conid", sa.String(), nullable=True),
        sa.Column("isin", sa.String(), nullable=True),
        sa.Column("issuer_country", sa.String(), nullable=True),
        sa.Column("currency", sa.String(), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=True),
        sa.Column("pay_date", sa.Date(), nullable=True),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("gross_rate_per_share", sa.Numeric(), nullable=True),
        sa.Column("gross_amount_usd", sa.Numeric(), nullable=False),
        sa.Column("tax_usd", sa.Numeric(), nullable=False),
        sa.Column("fee_usd", sa.Numeric(), nullable=True),
        sa.Column("net_amount_usd", sa.Numeric(), nullable=False),
        sa.Column("action_id", sa.String(), nullable=True),
        sa.Column("asset_category", sa.String(), nullable=True),
        sa.Column("sub_category", sa.String(), nullable=True),
        sa.Column("code", sa.String(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "raw_attrs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_open_dividend_accruals_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["flex_import_id"],
            ["flex_imports.id"],
            name=op.f("fk_open_dividend_accruals_flex_import_id_flex_imports"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_open_dividend_accruals_instrument_id_instruments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_open_dividend_accruals_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_open_dividend_accruals")),
        sa.UniqueConstraint(
            "account_id",
            "conid",
            "ex_date",
            "pay_date",
            "report_date",
            "action_id",
            "code",
            name="uq_open_dividend_accruals_natural_key",
        ),
        comment="Account-scoped. Visibilidad vía participations; sin user_id. Identidad por natural key compuesto (uq_open_dividend_accruals_natural_key); sin transaction_id.",
    )
    op.create_index(
        op.f("ix_open_dividend_accruals_account_id_instrument_id"),
        "open_dividend_accruals",
        ["account_id", "instrument_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_open_dividend_accruals_account_id_symbol"),
        "open_dividend_accruals",
        ["account_id", "symbol"],
        unique=False,
    )
    op.create_index(
        op.f("ix_open_dividend_accruals_flex_import_id"),
        "open_dividend_accruals",
        ["flex_import_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_open_dividend_accruals_organization_id"),
        "open_dividend_accruals",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_open_dividend_accruals_report_date"),
        "open_dividend_accruals",
        ["report_date"],
        unique=False,
    )
    op.create_table(
        "open_position_lots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("flex_import_id", sa.BigInteger(), nullable=True),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("asset_class", sa.String(), nullable=False),
        sa.Column("open_date", sa.Date(), nullable=False),
        sa.Column("qty", sa.Numeric(), nullable=False),
        sa.Column("cost_basis_usd", sa.Numeric(), nullable=False),
        sa.Column("mark_price_usd", sa.Numeric(), nullable=True),
        sa.Column("mark_value_usd", sa.Numeric(), nullable=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("originating_transaction_id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_open_position_lots_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["flex_import_id"],
            ["flex_imports.id"],
            name=op.f("fk_open_position_lots_flex_import_id_flex_imports"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_open_position_lots_instrument_id_instruments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_open_position_lots_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_open_position_lots")),
        sa.UniqueConstraint(
            "account_id",
            "symbol",
            "open_date",
            "snapshot_date",
            "originating_transaction_id",
            name="uq_open_position_lots_natural_key",
        ),
        comment="Account-scoped. Visibilidad vía participations; sin user_id. Identidad por natural key compuesto (uq_open_position_lots_natural_key); no hay transaction_id global, sí originating_transaction_id como discriminador.",
    )
    op.create_index(
        op.f("ix_open_position_lots_account_id_instrument_id"),
        "open_position_lots",
        ["account_id", "instrument_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_open_position_lots_account_id_symbol"),
        "open_position_lots",
        ["account_id", "symbol"],
        unique=False,
    )
    op.create_index(
        op.f("ix_open_position_lots_flex_import_id"),
        "open_position_lots",
        ["flex_import_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_open_position_lots_organization_id"),
        "open_position_lots",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "restatement_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("flex_import_id", sa.BigInteger(), nullable=True),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("table_name", sa.String(), nullable=False),
        sa.Column("natural_key", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("column_name", sa.String(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("sealed_year", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('value_update', 'sibling_row')", name=op.f("ck_restatement_log_kind")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_restatement_log_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["flex_import_id"],
            ["flex_imports.id"],
            name=op.f("fk_restatement_log_flex_import_id_flex_imports"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_restatement_log_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restatement_log")),
        comment="Org-scoped (RLS). Señal de restatement: IBKR cambió un valor material de un hecho ya persistido (value_update, snapshot tables) o emitió un sibling con distinto fifo_pnl (sibling_row, closed_lots). Detection-only; nunca borra hechos.",
    )
    op.create_index(
        op.f("ix_restatement_log_flex_import_id"),
        "restatement_log",
        ["flex_import_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restatement_log_organization_id"),
        "restatement_log",
        ["organization_id", sa.literal_column("detected_at DESC")],
        unique=False,
    )
    op.create_index(
        op.f("ix_restatement_log_organization_id_account_id"),
        "restatement_log",
        ["organization_id", "account_id"],
        unique=False,
    )
    op.create_table(
        "trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("flex_import_id", sa.BigInteger(), nullable=True),
        sa.Column("transaction_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("asset_class", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("settle_date", sa.Date(), nullable=True),
        sa.Column("qty", sa.Numeric(), nullable=False),
        sa.Column("price_usd", sa.Numeric(), nullable=False),
        sa.Column("proceeds_usd", sa.Numeric(), nullable=False),
        sa.Column("commission_usd", sa.Numeric(), nullable=False),
        sa.Column("open_close", sa.String(), nullable=True),
        sa.Column("buy_sell", sa.String(), nullable=False),
        sa.Column(
            "raw_attrs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("buy_sell IN ('BUY', 'SELL')", name=op.f("ck_trades_buy_sell")),
        sa.CheckConstraint(
            "open_close IS NULL OR open_close IN ('O', 'C')", name=op.f("ck_trades_open_close")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_trades_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["flex_import_id"],
            ["flex_imports.id"],
            name=op.f("fk_trades_flex_import_id_flex_imports"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_trades_instrument_id_instruments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_trades_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trades")),
        sa.UniqueConstraint(
            "organization_id", "transaction_id", name="uq_trades_org_transaction_id"
        ),
        comment="Account-scoped. Visibilidad vía participations; sin user_id. transaction_id único POR TENANT (multi-home, spec 2026-06-10): la misma cuenta broker puede existir en N orgs, cada org tiene su copia de los hechos.",
    )
    op.create_index(
        op.f("ix_trades_account_id_instrument_id"),
        "trades",
        ["account_id", "instrument_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_trades_account_id_symbol"), "trades", ["account_id", "symbol"], unique=False
    )
    op.create_index(op.f("ix_trades_flex_import_id"), "trades", ["flex_import_id"], unique=False)
    op.create_index(op.f("ix_trades_trade_date"), "trades", ["trade_date"], unique=False)
    op.create_table(
        "transfers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("flex_import_id", sa.BigInteger(), nullable=True),
        sa.Column("transaction_id", sa.String(), nullable=False),
        sa.Column("transfer_date", sa.Date(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("src_account_id", sa.BigInteger(), nullable=True),
        sa.Column("src_counterparty_id", sa.BigInteger(), nullable=True),
        sa.Column("dst_account_id", sa.BigInteger(), nullable=True),
        sa.Column("dst_counterparty_id", sa.BigInteger(), nullable=True),
        sa.Column("instrument_id", sa.BigInteger(), nullable=True),
        sa.Column("asset_class", sa.String(), nullable=False),
        sa.Column("conid", sa.String(), nullable=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("qty", sa.Numeric(), nullable=False),
        sa.Column("transfer_type", sa.String(), nullable=False),
        sa.CheckConstraint(
            "(asset_class = 'CASH') = (instrument_id IS NULL)",
            name=op.f("ck_transfers_transfer_cash_iff_no_instrument"),
        ),
        sa.CheckConstraint("direction IN ('IN', 'OUT')", name=op.f("ck_transfers_direction")),
        sa.CheckConstraint(
            "(dst_account_id IS NOT NULL) <> (dst_counterparty_id IS NOT NULL)",
            name=op.f("ck_transfers_dst_arc"),
        ),
        sa.CheckConstraint(
            "(src_account_id IS NOT NULL) <> (src_counterparty_id IS NOT NULL)",
            name=op.f("ck_transfers_src_arc"),
        ),
        sa.ForeignKeyConstraint(
            ["dst_account_id"],
            ["accounts.id"],
            name=op.f("fk_transfers_dst_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dst_counterparty_id"],
            ["counterparties.id"],
            name=op.f("fk_transfers_dst_counterparty_id_counterparties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["flex_import_id"],
            ["flex_imports.id"],
            name=op.f("fk_transfers_flex_import_id_flex_imports"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_transfers_instrument_id_instruments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_transfers_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["src_account_id"],
            ["accounts.id"],
            name=op.f("fk_transfers_src_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["src_counterparty_id"],
            ["counterparties.id"],
            name=op.f("fk_transfers_src_counterparty_id_counterparties"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfers")),
        sa.UniqueConstraint(
            "organization_id", "transaction_id", name="uq_transfers_org_transaction_id"
        ),
        comment="Account-scoped. Visibilidad vía participations; sin user_id. transaction_id único POR TENANT (multi-home, spec 2026-06-10): la misma cuenta broker puede existir en N orgs, cada org tiene su copia de los hechos.",
    )
    op.create_index(
        op.f("ix_transfers_dst_account_id"), "transfers", ["dst_account_id"], unique=False
    )
    op.create_index(
        op.f("ix_transfers_dst_counterparty_id"), "transfers", ["dst_counterparty_id"], unique=False
    )
    op.create_index(
        op.f("ix_transfers_flex_import_id"), "transfers", ["flex_import_id"], unique=False
    )
    op.create_index(
        op.f("ix_transfers_instrument_id"), "transfers", ["instrument_id"], unique=False
    )
    op.create_index(
        op.f("ix_transfers_src_account_id"), "transfers", ["src_account_id"], unique=False
    )
    op.create_index(
        op.f("ix_transfers_src_counterparty_id"), "transfers", ["src_counterparty_id"], unique=False
    )
    op.create_table(
        "closed_lots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("flex_import_id", sa.BigInteger(), nullable=True),
        sa.Column("transaction_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("asset_class", sa.String(), nullable=False),
        sa.Column("open_date", sa.Date(), nullable=False),
        sa.Column("close_date", sa.Date(), nullable=False),
        sa.Column(
            "close_datetime",
            sa.DateTime(),
            nullable=False,
            comment="Naive POR DISEÑO (D3 sp1-db-hardening): IBKR emite 'YYYYMMDD;HHMMSS' sin timezone (exchange-local); timestamptz inventaría una zona. La regla 730d (Art. 300 ET) opera a granularidad de día sobre close_date.",
        ),
        sa.Column("qty", sa.Numeric(), nullable=False),
        sa.Column("cost_basis_usd", sa.Numeric(), nullable=False),
        sa.Column("proceeds_usd", sa.Numeric(), nullable=False),
        sa.Column("fifo_pnl_usd", sa.Numeric(), nullable=False),
        sa.Column("source_trade_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_closed_lots_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["flex_import_id"],
            ["flex_imports.id"],
            name=op.f("fk_closed_lots_flex_import_id_flex_imports"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_closed_lots_instrument_id_instruments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_closed_lots_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_trade_id"], ["trades.id"], name=op.f("fk_closed_lots_source_trade_id_trades")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_closed_lots")),
        sa.UniqueConstraint(
            "organization_id",
            "transaction_id",
            "close_datetime",
            "qty",
            "fifo_pnl_usd",
            name="uq_closed_lots_natural_key",
        ),
        comment="Account-scoped. Visibilidad vía participations; sin user_id. Identidad por natural key compuesto (uq_closed_lots_natural_key); transaction_id NO es único ni global ni per-org — múltiples ejecuciones de cierre lo comparten (amendment A3); la key es per-tenant (multi-home, spec 2026-06-10).",
    )
    op.create_index(
        op.f("ix_closed_lots_account_id_instrument_id"),
        "closed_lots",
        ["account_id", "instrument_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_closed_lots_account_id_symbol"),
        "closed_lots",
        ["account_id", "symbol"],
        unique=False,
    )
    op.create_index(
        op.f("ix_closed_lots_flex_import_id"), "closed_lots", ["flex_import_id"], unique=False
    )
    op.create_index(
        op.f("ix_closed_lots_source_trade_id"), "closed_lots", ["source_trade_id"], unique=False
    )
    # ### end Alembic commands ###

    # --- IC-3: participations non-overlap EXCLUDE (amendment #9) ------------
    # btree_gist provides the '=' operator class the gist EXCLUDE needs for the
    # scalar equality columns; not used anywhere else today. Runs as the migrate
    # owner (app_rls has no CREATE). The `validity` generated column is emitted by
    # autogenerate (above); the EXCLUDE itself is hand-written because
    # autogenerate does not express ExcludeConstraint. Column-based (over the flat
    # `validity` daterange, not an inline expression) so it round-trips clean —
    # the pre-build idempotency spike confirmed an empty second autogenerate diff.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        "ALTER TABLE participations ADD CONSTRAINT participations_no_overlap "
        "EXCLUDE USING gist ("
        "organization_id WITH =, party_id WITH =, account_id WITH =, validity WITH &&)"
    )

    # --- (0) seed the global institutions catalog (control plane) -----------
    # institutions is control-plane data (no RLS, like trm_days): the catalog is
    # not user input — it is seeded here so connections.institution_id has a row
    # to reference. ON CONFLICT (code) DO NOTHING keeps it idempotent.
    op.execute(
        "INSERT INTO institutions (code, name) VALUES ('ibkr', 'Interactive Brokers') "
        "ON CONFLICT (code) DO NOTHING"
    )

    # =======================================================================
    # HAND-WRITTEN SECTIONS (frozen inline — autogenerate cannot emit these).
    # Migrations run as the DB owner (the migrate one-shot container), which is
    # exempt from RLS even under FORCE — so creating the role + policies + the
    # SECURITY DEFINER function all run with the necessary privilege here.
    # =======================================================================

    # --- (1) apscheduler_jobs (runtime table; app_rls has no CREATE) --------
    # Frozen VERBATIM from the old 7fdaf6528762. IF NOT EXISTS is load-bearing
    # (dirty-volume safe): APScheduler's SQLAlchemyJobStore lazily create_all()s
    # this exact table at runtime; a volume that ran a pre-split app already has
    # it. The schema is APScheduler-owned + frozen by the APScheduler==3.11.* pin
    # (no third writer), so IF NOT EXISTS cannot mask meaningful drift. Not in
    # Base.metadata -> the drift test ignores it; test_tier1_baseline asserts it.
    op.execute(
        "CREATE TABLE IF NOT EXISTS apscheduler_jobs ("
        "id VARCHAR(191) NOT NULL PRIMARY KEY, "
        "next_run_time DOUBLE PRECISION, "
        "job_state BYTEA NOT NULL)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_apscheduler_jobs_next_run_time "
        "ON apscheduler_jobs (next_run_time)"
    )

    # --- (2) app_rls role + grants -----------------------------------------
    # The non-superuser, non-owner login role the long-running app connects as,
    # so RLS (with FORCE) actually applies in runtime. Frozen from the output of
    # db/rls.py::app_role_grants_sql(); the password is the only runtime value,
    # read from env via _app_rls_password() (no hardcoded credential in source).
    op.execute(
        f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_APP_ROLE}') "
        f"THEN CREATE ROLE {_APP_ROLE} LOGIN PASSWORD '{_app_rls_password()}'; END IF; END $$"
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {_APP_ROLE}"
    )
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_APP_ROLE}"
    )
    # apscheduler_jobs was created above without going through ALTER DEFAULT
    # PRIVILEGES timing guarantees; grant DML explicitly (idempotent, frozen from
    # 7fdaf6528762) so the running app can read/write job rows.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON apscheduler_jobs TO {_APP_ROLE}")

    # --- (3) standard org-isolation RLS policy on every org-scoped table ----
    # Frozen from db/rls.py::standard_policy_sql(). ENABLE + FORCE (so even the
    # table owner is subject) + a single policy keyed on app.current_org.
    for table in _ORG_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON {table}\n"
            f"            USING (organization_id = {_CURRENT_ORG})\n"
            f"            WITH CHECK (organization_id = {_CURRENT_ORG})"
        )

    # --- (4) special grant_visibility policy on access_grants ---------------
    # Frozen from db/rls.py::access_grants_policy_sql(). A grant is visible to the
    # grantor's org OR the grantee (org or user) — cross-org by design; writes are
    # still confined to the current org.
    op.execute("ALTER TABLE access_grants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE access_grants FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY grant_visibility ON access_grants\n"
        "            USING (\n"
        f"              organization_id = {_CURRENT_ORG}\n"
        f"              OR grantee_organization_id = {_CURRENT_ORG}\n"
        f"              OR grantee_user_id = {_CURRENT_USER}\n"
        "            )\n"
        f"            WITH CHECK (organization_id = {_CURRENT_ORG})"
    )

    # --- (5) system_credentialed_org_ids() SECURITY DEFINER function --------
    # Frozen from db/rls.py::system_enum_function_sql() (amendment #2, W1 Task 4):
    # the body enumerates orgs with a non-disabled ibkr_flex connection (repointed
    # from the legacy flex_credentials — the cron now iterates connections).
    # SECURITY DEFINER (owner is the migration superuser, RLS-exempt) so the
    # Flex cron can enumerate credentialed orgs (a cross-tenant control-plane
    # read that would default-deny to 0 rows as app_rls). SET search_path pins
    # name resolution (anti-hijack); REVOKE FROM PUBLIC + GRANT TO app_rls is
    # least privilege. Not in Base.metadata -> drift test ignores it.
    op.execute(
        "CREATE OR REPLACE FUNCTION system_credentialed_org_ids() "
        "RETURNS SETOF bigint LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path = pg_catalog, public AS $$ "
        "SELECT DISTINCT organization_id FROM connections "
        "WHERE provider_type = 'ibkr_flex' AND status <> 'disabled' $$"
    )
    op.execute("REVOKE EXECUTE ON FUNCTION system_credentialed_org_ids() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION system_credentialed_org_ids() TO {_APP_ROLE}")

    # --- (6) authz_grant_party_ids() SECURITY DEFINER function (SP2-D3) ------
    # Frozen from db/rls.py::authz_grant_function_sql() (amendment #8, SP2): the
    # authorization resolver runs BEFORE org RLS context is set, so "may user U
    # enter org X?" is inherently cross-org — same security envelope as
    # system_credentialed_org_ids() (SECURITY DEFINER, owner RLS-exempt, pinned
    # search_path anti-hijack, REVOKE PUBLIC + GRANT app_rls least privilege).
    # Resolves the grantor party_ids of grants vigentes (half-open [from, to),
    # CURRENT_DATE) toward the user (direct grantee_user_id OR via a firm-org
    # membership). Not in Base.metadata -> drift test ignores it.
    op.execute(
        "CREATE OR REPLACE FUNCTION authz_grant_party_ids("
        "p_user_id bigint, p_org_id bigint) "
        "RETURNS SETOF bigint LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path = pg_catalog, public AS $$ "
        "SELECT g.grantor_party_id FROM access_grants g "
        "WHERE g.organization_id = p_org_id "
        "AND (g.grantee_user_id = p_user_id "
        "OR g.grantee_organization_id IN ("
        "SELECT m.organization_id FROM memberships m WHERE m.user_id = p_user_id)) "
        "AND g.valid_from <= CURRENT_DATE "
        "AND (g.valid_to IS NULL OR g.valid_to > CURRENT_DATE) $$"
    )
    op.execute("REVOKE EXECUTE ON FUNCTION authz_grant_party_ids(bigint, bigint) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION authz_grant_party_ids(bigint, bigint) TO {_APP_ROLE}")


def downgrade() -> None:
    """Downgrade schema."""
    # Drop the hand-written objects first (mirrors the old chain's downgrades:
    # the function from a1f2c3d4e5b6, apscheduler_jobs from 7fdaf6528762). The
    # RLS policies are dropped implicitly with their tables below; the role is
    # dropped last (after its grants are gone with the tables).
    op.execute("DROP FUNCTION IF EXISTS authz_grant_party_ids(bigint, bigint)")
    op.execute("DROP FUNCTION IF EXISTS system_credentialed_org_ids()")
    op.execute("DROP INDEX IF EXISTS ix_apscheduler_jobs_next_run_time")
    op.execute("DROP TABLE IF EXISTS apscheduler_jobs")
    # IC-3 (amendment #9): drop the EXCLUDE before its table, then the extension.
    op.execute("ALTER TABLE participations DROP CONSTRAINT IF EXISTS participations_no_overlap")
    op.execute("DROP EXTENSION IF EXISTS btree_gist")

    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f("ix_closed_lots_source_trade_id"), table_name="closed_lots")
    op.drop_index(op.f("ix_closed_lots_flex_import_id"), table_name="closed_lots")
    op.drop_index(op.f("ix_closed_lots_account_id_symbol"), table_name="closed_lots")
    op.drop_index(op.f("ix_closed_lots_account_id_instrument_id"), table_name="closed_lots")
    op.drop_table("closed_lots")
    op.drop_index(op.f("ix_transfers_src_counterparty_id"), table_name="transfers")
    op.drop_index(op.f("ix_transfers_src_account_id"), table_name="transfers")
    op.drop_index(op.f("ix_transfers_instrument_id"), table_name="transfers")
    op.drop_index(op.f("ix_transfers_flex_import_id"), table_name="transfers")
    op.drop_index(op.f("ix_transfers_dst_counterparty_id"), table_name="transfers")
    op.drop_index(op.f("ix_transfers_dst_account_id"), table_name="transfers")
    op.drop_table("transfers")
    op.drop_index(op.f("ix_trades_trade_date"), table_name="trades")
    op.drop_index(op.f("ix_trades_flex_import_id"), table_name="trades")
    op.drop_index(op.f("ix_trades_account_id_symbol"), table_name="trades")
    op.drop_index(op.f("ix_trades_account_id_instrument_id"), table_name="trades")
    op.drop_table("trades")
    op.drop_index(
        op.f("ix_restatement_log_organization_id_account_id"), table_name="restatement_log"
    )
    op.drop_index(op.f("ix_restatement_log_organization_id"), table_name="restatement_log")
    op.drop_index(op.f("ix_restatement_log_flex_import_id"), table_name="restatement_log")
    op.drop_table("restatement_log")
    op.drop_index(op.f("ix_open_position_lots_organization_id"), table_name="open_position_lots")
    op.drop_index(op.f("ix_open_position_lots_flex_import_id"), table_name="open_position_lots")
    op.drop_index(op.f("ix_open_position_lots_account_id_symbol"), table_name="open_position_lots")
    op.drop_index(
        op.f("ix_open_position_lots_account_id_instrument_id"), table_name="open_position_lots"
    )
    op.drop_table("open_position_lots")
    op.drop_index(
        op.f("ix_open_dividend_accruals_report_date"), table_name="open_dividend_accruals"
    )
    op.drop_index(
        op.f("ix_open_dividend_accruals_organization_id"), table_name="open_dividend_accruals"
    )
    op.drop_index(
        op.f("ix_open_dividend_accruals_flex_import_id"), table_name="open_dividend_accruals"
    )
    op.drop_index(
        op.f("ix_open_dividend_accruals_account_id_symbol"), table_name="open_dividend_accruals"
    )
    op.drop_index(
        op.f("ix_open_dividend_accruals_account_id_instrument_id"),
        table_name="open_dividend_accruals",
    )
    op.drop_table("open_dividend_accruals")
    op.drop_index(
        op.f("ix_flex_import_accounts_organization_id"), table_name="flex_import_accounts"
    )
    op.drop_table("flex_import_accounts")
    op.drop_index(
        op.f("ix_change_in_dividend_accruals_report_date"), table_name="change_in_dividend_accruals"
    )
    op.drop_index(
        op.f("ix_change_in_dividend_accruals_organization_id"),
        table_name="change_in_dividend_accruals",
    )
    op.drop_index(
        op.f("ix_change_in_dividend_accruals_flex_import_id"),
        table_name="change_in_dividend_accruals",
    )
    op.drop_index(
        op.f("ix_change_in_dividend_accruals_account_id_symbol"),
        table_name="change_in_dividend_accruals",
    )
    op.drop_index(
        op.f("ix_change_in_dividend_accruals_account_id_instrument_id"),
        table_name="change_in_dividend_accruals",
    )
    op.drop_table("change_in_dividend_accruals")
    op.drop_index(op.f("ix_cash_transactions_instrument_id"), table_name="cash_transactions")
    op.drop_index(op.f("ix_cash_transactions_flex_import_id"), table_name="cash_transactions")
    op.drop_index(op.f("ix_cash_transactions_date"), table_name="cash_transactions")
    op.drop_table("cash_transactions")
    op.drop_index(op.f("ix_participations_organization_id"), table_name="participations")
    op.drop_table("participations")
    op.drop_index(op.f("ix_ingest_log_organization_id"), table_name="ingest_log")
    op.drop_index("ix_ingest_log_org_started_at", table_name="ingest_log")
    op.drop_index(op.f("ix_ingest_log_connection_id"), table_name="ingest_log")
    op.drop_table("ingest_log")
    op.drop_index(op.f("ix_flex_imports_organization_id"), table_name="flex_imports")
    op.drop_index(op.f("ix_flex_imports_connection_id"), table_name="flex_imports")
    op.drop_table("flex_imports")
    op.drop_index(
        op.f("ix_connection_ibkr_flex_organization_id"), table_name="connection_ibkr_flex"
    )
    op.drop_table("connection_ibkr_flex")
    op.drop_index(op.f("ix_access_grants_organization_id"), table_name="access_grants")
    op.drop_index(op.f("ix_access_grants_grantor_party_id"), table_name="access_grants")
    op.drop_index(op.f("ix_access_grants_grantee_user_id"), table_name="access_grants")
    op.drop_index(op.f("ix_access_grants_grantee_organization_id"), table_name="access_grants")
    op.drop_table("access_grants")
    op.drop_table("user_settings")
    op.drop_index(op.f("ix_parties_user_id"), table_name="parties")
    op.drop_index(op.f("ix_parties_organization_id"), table_name="parties")
    op.drop_table("parties")
    op.drop_table("memberships")
    op.drop_index(
        op.f("ix_instrument_identifiers_instrument_id"), table_name="instrument_identifiers"
    )
    op.drop_table("instrument_identifiers")
    op.drop_index(op.f("ix_counterparties_organization_id"), table_name="counterparties")
    op.drop_table("counterparties")
    op.drop_index(op.f("ix_connections_organization_id"), table_name="connections")
    op.drop_index(op.f("ix_connections_institution_id"), table_name="connections")
    op.drop_table("connections")
    op.drop_table("accounts")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    op.drop_table("trm_imports")
    op.drop_index(op.f("ix_trm_days_date"), table_name="trm_days")
    op.drop_table("trm_days")
    op.drop_table("organizations")
    op.drop_table("instruments")
    op.drop_table("institutions")
    # ### end Alembic commands ###

    # Role last: its grants are gone once the tables are dropped. This is a wipe
    # baseline, so downgrade fidelity is low-priority (frozen from 05943d9efcdb).
    op.execute(f"DROP ROLE IF EXISTS {_APP_ROLE}")
