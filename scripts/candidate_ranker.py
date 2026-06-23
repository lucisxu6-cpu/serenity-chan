#!/usr/bin/env python3
"""Rank Serenity + Chan candidates by scored decision priority."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    from serenity_chan_scorecard import score
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.candidate_ranker
    from scripts.serenity_chan_scorecard import score


RATING_RANK = {"OBSERVE_ONLY": 0, "D": 1, "C": 2, "B": 3, "A": 4, "S": 5}


def _load_json(path: str) -> Dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _scored_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    if "candidate_priority_score" in data and "watchlist_bucket" in data:
        return data
    return score(data)


def _rating_rank(item: Dict[str, Any]) -> int:
    rating = str(item.get("research_rating") or item.get("final_rating") or "OBSERVE_ONLY")
    return RATING_RANK.get(rating, 0)


def rank_candidates(payloads: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    scored = [_scored_payload(dict(payload)) for payload in payloads]
    ranked = sorted(
        scored,
        key=lambda item: (
            float(item.get("candidate_priority_score", 0)),
            _rating_rank(item),
        ),
        reverse=True,
    )
    if not ranked:
        return {"ranking": [], "cluster_summary": {"count": 0}}

    top_score = float(ranked[0].get("candidate_priority_score", 0))
    ranking: List[Dict[str, Any]] = []
    for index, item in enumerate(ranked, start=1):
        score_value = float(item.get("candidate_priority_score", 0))
        if score_value >= top_score - 3:
            cluster = "same_priority_cluster"
        elif score_value >= top_score - 10:
            cluster = "near_priority_cluster"
        else:
            cluster = "lower_priority_cluster"
        ranking.append({
            "rank": index,
            "ticker": item.get("ticker", ""),
            "company": item.get("company", ""),
            "market": item.get("market", ""),
            "candidate_priority_score": round(score_value, 2),
            "research_rating": item.get("research_rating") or item.get("final_rating", ""),
            "evidence_confidence_rating": item.get("evidence_confidence_rating", ""),
            "action_readiness": item.get("action_readiness", ""),
            "watchlist_bucket": item.get("watchlist_bucket", ""),
            "cluster": cluster,
        })

    return {
        "ranking": ranking,
        "cluster_summary": {
            "count": len(ranking),
            "top_score": round(top_score, 2),
            "top_ticker": ranking[0]["ticker"],
            "same_priority_count": sum(1 for item in ranking if item["cluster"] == "same_priority_cluster"),
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Rank Serenity + Chan scorecards")
    parser.add_argument("scorecards", nargs="+", help="scorecard input/output JSON files")
    args = parser.parse_args(argv)
    try:
        payloads = [_load_json(path) for path in args.scorecards]
        print(json.dumps(rank_candidates(payloads), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
