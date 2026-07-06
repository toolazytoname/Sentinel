"""Risk modules."""
from .reflection import ReflectionWriter, TradeContext
from .research import CoinGeckoEventsSource, EventSource, ResearchIngester
from .stages import (
    CRITERIA,
    StageCriteria,
    StageReport,
    apply_recommendation,
    check_stage_upgrade,
    register_strategy,
)
from .veto import (
    MarketContext,
    TradeSignal,
    VetoResult,
    audit,
    check_rules,
)

__all__ = [
    # research
    "EventSource",
    "CoinGeckoEventsSource",
    "ResearchIngester",
    # reflection
    "ReflectionWriter",
    "TradeContext",
    # stages
    "CRITERIA",
    "StageCriteria",
    "StageReport",
    "check_stage_upgrade",
    "register_strategy",
    "apply_recommendation",
    # veto
    "MarketContext",
    "TradeSignal",
    "VetoResult",
    "audit",
    "check_rules",
]