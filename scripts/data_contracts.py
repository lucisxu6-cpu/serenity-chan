#!/usr/bin/env python3
"""Shared data and decision contracts for the Serenity + Chan skill."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Market(str, Enum):
    CN_A = "CN_A"
    HK = "HK"
    US = "US"
    GLOBAL = "GLOBAL"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class SourceLevel(str, Enum):
    L0 = "L0_OFFICIAL_DISCLOSURE"
    L1 = "L1_LICENSED_OR_PRO_DATABASE"
    L2 = "L2_FREE_API_OR_OPEN_SOURCE"
    L3 = "L3_MEDIA_F10_RESEARCH"
    L4 = "L4_RUMOR_OR_UNVERIFIED"


class Dataset(str, Enum):
    CURRENT_QUOTE = "current_quote"
    PRICE_HISTORY_RAW = "price_history_raw"
    PRICE_HISTORY_ADJUSTED = "price_history_adjusted"
    SHARE_CAPITAL = "share_capital"
    FINANCIALS = "financials"
    FILINGS = "filings_announcements"
    CUSTOMER_EVIDENCE = "customer_order_capacity_evidence"
    PEER_VALUATION = "peer_valuation"
    ESTIMATES = "consensus_estimates"
    TRADING_CALENDAR = "trading_calendar"


class DataStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    STALE = "STALE"
    FAILED = "FAILED"
    PENDING = "PENDING"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_REQUESTED = "NOT_REQUESTED"


class RatingCap(str, Enum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    OBSERVE_ONLY = "OBSERVE_ONLY"


class DataGapType(str, Enum):
    ACCESS_FAILURE = "ACCESS_FAILURE"
    SCOPE_NOT_REQUESTED = "SCOPE_NOT_REQUESTED"
    SOURCE_NOT_IMPLEMENTED = "SOURCE_NOT_IMPLEMENTED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    ISSUER_NON_DISCLOSURE = "ISSUER_NON_DISCLOSURE"
    NOT_MACHINE_READABLE = "NOT_MACHINE_READABLE"
    CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
    STALE_DATA = "STALE_DATA"
    EVIDENCE_DEPTH_LIMIT = "EVIDENCE_DEPTH_LIMIT"
    ADJUSTMENT_BASIS_UNVERIFIED = "ADJUSTMENT_BASIS_UNVERIFIED"
    NOT_MATERIAL = "NOT_MATERIAL"
    POLICY_BLOCKED = "POLICY_BLOCKED"


class DecisionImpact(str, Enum):
    THESIS_IMPACT = "THESIS_IMPACT"
    EVIDENCE_IMPACT = "EVIDENCE_IMPACT"
    ACTION_IMPACT = "ACTION_IMPACT"
    ENGINEERING_GAP = "ENGINEERING_GAP"
    NO_IMPACT = "NO_IMPACT"


class AcquisitionStage(str, Enum):
    LOCAL_CACHE = "LOCAL_CACHE"
    PRIMARY_DISCLOSURE = "PRIMARY_DISCLOSURE"
    LICENSED_STRUCTURED = "LICENSED_STRUCTURED"
    OPEN_STRUCTURED = "OPEN_STRUCTURED"
    STRUCTURED_PREFLIGHT = "STRUCTURED_PREFLIGHT"
    MANUAL_RETRIEVAL = "MANUAL_RETRIEVAL"


STRICT_TO_PERMISSIVE_RATING_CAPS = [
    RatingCap.OBSERVE_ONLY,
    RatingCap.D,
    RatingCap.C,
    RatingCap.B,
    RatingCap.A,
    RatingCap.S,
]


def enum_values(enum_cls: type[Enum]) -> list[str]:
    return [str(item.value) for item in enum_cls]


def stricter_cap(current: RatingCap, target: RatingCap) -> RatingCap:
    """Return the stricter of two rating ceilings."""
    order = STRICT_TO_PERMISSIVE_RATING_CAPS
    return current if order.index(current) < order.index(target) else target


@dataclass(frozen=True)
class FetchAttempt:
    dataset: str
    source_name: str
    source_level: str
    stage: str
    status: str
    attempted_at: str
    gap_type: Optional[str] = None
    decision_impact: Optional[str] = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "source_name": self.source_name,
            "source_level": self.source_level,
            "stage": self.stage,
            "status": self.status,
            "attempted_at": self.attempted_at,
            "gap_type": self.gap_type,
            "decision_impact": self.decision_impact,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DataGap:
    dataset: str
    status: str
    gap_type: str
    decision_impact: str
    rating_impact: str
    next_action: str
    source_name: str = ""
    source_level: str = ""
    evidence_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "dataset": self.dataset,
            "status": self.status,
            "gap_type": self.gap_type,
            "decision_impact": self.decision_impact,
            "rating_impact": self.rating_impact,
            "next_action": self.next_action,
            "source_name": self.source_name,
            "source_level": self.source_level,
        }
        if self.evidence_path:
            payload["evidence_path"] = self.evidence_path
        return payload


@dataclass(frozen=True)
class ManualRetrievalTask:
    dataset: str
    priority: str
    target_source: str
    objective: str
    acceptance_criteria: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "priority": self.priority,
            "target_source": self.target_source,
            "objective": self.objective,
            "acceptance_criteria": self.acceptance_criteria,
        }
