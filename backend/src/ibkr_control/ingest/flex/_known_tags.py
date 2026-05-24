"""Catálogo de tags conocidos del Flex XML.

Si el parser encuentra un tag TOP-level fuera de esta lista, ABORTA el ingest
(patrón replicado de renta/documentos/ibkr_flex/_audit.py).

Esto es defensivo: si IBKR agrega un nuevo tag (e.g. CryptoTransactions),
queremos verlo explícitamente y actualizar la lista manualmente, NO
silenciosamente ignorarlo.
"""

# Tags TOP-level del XML que SÍ procesamos o reconocemos explícitamente.
# El audit aborta si encuentra otros.
KNOWN_TOP_LEVEL_TAGS: frozenset[str] = frozenset({
    # Tags que procesamos y persisten datos en Phase 2:
    "AccountInformation",
    "Trades",                       # Trade rows
    "ClosedLots",                   # ClosedLot rows (not in real Activity XMLs; in separate query)
    "OpenPositions",                # OpenPosition rows
    "CashTransactions",             # CashTransaction rows
    "Transfers",                    # Transfer rows
    "TransferLots",                 # TransferLot rows (may appear standalone)
    # Tags que vienen en Activity XML pero ignoramos explícitamente en Phase 2:
    "AccountSummary",               # Summary wrapper
    "AccruedDividends",             # Dividend accruals (Phase 5+)
    "CFDCharges",                   # CFD-specific charges; no CFD activity in our accounts
    "CashReport",                   # Cash report wrapper (StatementOfFundsLine rows inside)
    "ChangeInDividendAccruals",     # Wrapper for ChangeInDividendAccrual rows (actively parsed)
    "ChangeInNAV",                  # NAV changes over period
    "ChangeInPositionValues",       # Position value changes
    "CommissionCredits",            # Commission credits/rebates
    "ComplexPositions",             # Complex positions (spreads etc.)
    "ConversionRates",              # FX conversion rates used in statement
    "CorporateActions",             # Stock splits, mergers, etc. (Phase 3+)
    "DebitCardActivities",          # Debit card usage
    "DepositsOnHold",               # Deposits pending clearance
    "EquitySummaryInBase",          # Equity summary in base currency
    "FIFOPerformanceSummaryInBase", # FIFO P&L summary (we compute our own)
    "FdicInsuredDepositsByBank",    # FDIC insurance details
    "FinancialInstrumentInformation",
    "FxLots",                       # FX lots (plan alias for FxPositions)
    "FxPositions",                  # FX position lots
    "FxTrades",                     # FX trades (plan alias for FxTransactions)
    "FxTransactions",               # FX conversion transactions
    "HKIPOOpenSubscriptions",       # HK IPO subscriptions
    "HKIPOSubscriptionActivity",    # HK IPO activity
    "HardToBorrowDetails",          # Hard-to-borrow stock details
    "IBGNoteTransactions",          # IB Global Note transactions
    "IncentiveCouponAccrualDetails",# Incentive coupon details
    "InterestAccruals",             # Interest accruals
    "MTDYTDPerformanceSummary",     # MTD/YTD performance summary
    "MTMPerformanceSummaryInBase",  # Mark-to-market performance
    "MutualFundDividendDetails",    # Mutual fund dividend details
    "NetAssetValue",                # NAV (plan alias)
    "NetStockPositionSummary",      # Net stock position summary
    "OpenDividendAccruals",         # Wrapper for OpenDividendAccrual rows (actively parsed)
    "OptionEAE",                    # Options Exercise/Assignment/Expiration
    "PendingExcercises",            # Pending option exercises (typo from IBKR)
    "PriorPeriodPositions",         # Prior period positions
    "RealizedAndUnrealizedPerformanceSummaryInBase",
    "RoutingCommissions",           # Routing commission details
    "Routes",                       # Routing details (plan alias)
    "SLBActivities",                # Stock Loan Borrow activities
    "SLBActivity",                  # Plan alias for SLBActivities
    "SLBCollaterals",               # SLB collateral details
    "SLBFees",                      # SLB fee details
    "SLBOpenContracts",             # SLB open contracts
    "SalesTaxes",                   # Sales tax details
    "SecuritiesInfo",               # Securities info wrapper
    "SoftDollars",                  # Soft dollar arrangements
    "StmtFunds",                    # Statement of funds (StatementOfFundsLine rows inside)
    "StockGrantActivities",         # Stock grant (RSU/ESPP) activities
    "TierInterestDetails",          # Tiered interest rate details
    "TradeConfirms",                # Trade confirmation details
    "TradeTransfers",               # Trade-level transfer details
    "TransactionTaxes",             # Transaction taxes
    "UnbookedTrades",               # Unbooked/cancelled trades
    "UnbundledCommissionDetails",   # Unbundled commission breakdown
    "UnsettledTransfers",           # Transfers pending settlement
})

# Tags TOP-level que explícitamente IGNORAMOS al parsear (no error, no insert).
# Si un tag está acá, el parser lo saltea silenciosamente.
# INVARIANT: EXPLICITLY_IGNORED must be a subset of KNOWN_TOP_LEVEL_TAGS.
EXPLICITLY_IGNORED: frozenset[str] = frozenset({
    "AccountSummary",
    "AccruedDividends",
    "CFDCharges",
    "CashReport",
    "ChangeInNAV",
    "ChangeInPositionValues",
    "CommissionCredits",
    "ComplexPositions",
    "ConversionRates",
    "CorporateActions",
    "DebitCardActivities",
    "DepositsOnHold",
    "EquitySummaryInBase",
    "FIFOPerformanceSummaryInBase",
    "FdicInsuredDepositsByBank",
    "FinancialInstrumentInformation",
    "FxLots",
    "FxPositions",
    "FxTrades",
    "FxTransactions",
    "HKIPOOpenSubscriptions",
    "HKIPOSubscriptionActivity",
    "HardToBorrowDetails",
    "IBGNoteTransactions",
    "IncentiveCouponAccrualDetails",
    "InterestAccruals",
    "MTDYTDPerformanceSummary",
    "MTMPerformanceSummaryInBase",
    "MutualFundDividendDetails",
    "NetAssetValue",
    "NetStockPositionSummary",
    "OptionEAE",
    "PendingExcercises",
    "PriorPeriodPositions",
    "RealizedAndUnrealizedPerformanceSummaryInBase",
    "RoutingCommissions",
    "Routes",
    "SLBActivities",
    "SLBActivity",
    "SLBCollaterals",
    "SLBFees",
    "SLBOpenContracts",
    "SalesTaxes",
    "SecuritiesInfo",
    "SoftDollars",
    "StmtFunds",
    "StockGrantActivities",
    "TierInterestDetails",
    "TradeConfirms",
    "TradeTransfers",
    "TransactionTaxes",
    "UnbookedTrades",
    "UnbundledCommissionDetails",
    "UnsettledTransfers",
})

assert EXPLICITLY_IGNORED <= KNOWN_TOP_LEVEL_TAGS, (
    "EXPLICITLY_IGNORED must be a subset of KNOWN_TOP_LEVEL_TAGS"
)
