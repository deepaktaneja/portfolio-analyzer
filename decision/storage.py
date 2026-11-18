"""Persistence for decision signals, recommendations, history & backtests (Stage 8).

Backed by the isolated decision database (``models/decision.py``). All operations
are failure-tolerant and self-contained.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from models.decision import (
    DecisionBacktest,
    DecisionHistory,
    DecisionRecommendation,
    DecisionSignal,
    get_decision_session,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _signal_fields(sig) -> dict:
    d = sig.to_dict() if hasattr(sig, "to_dict") else dict(sig)
    return {
        "signal_type": d.get("signal_type"),
        "severity": d.get("severity"),
        "confidence": float(d.get("confidence") or 0.0),
        "message": d.get("message", ""),
        "evidence_json": json.dumps(d.get("evidence") or {}, default=str),
        "thresholds_json": json.dumps(d.get("thresholds") or {}, default=str),
        "current_value": d.get("current_value"),
        "threshold_value": d.get("threshold_value"),
    }


def save_signals(signals, portfolio_id=None, security_id=None, security_type="equity", session=None) -> int:
    """Persist a list of Signal objects/dicts. Returns count saved."""
    if not signals:
        return 0

    def _do(s):
        n = 0
        for sig in signals:
            f = _signal_fields(sig)
            s.add(DecisionSignal(
                portfolio_id=portfolio_id,
                security_id=security_id or f.get("security_id") or "",
                security_type=security_type,
                signal_type=f["signal_type"],
                severity=f["severity"],
                confidence=f["confidence"],
                message=f["message"],
                evidence_json=f["evidence_json"],
                thresholds_json=f["thresholds_json"],
                current_value=f["current_value"],
                threshold_value=f["threshold_value"],
                triggered_at=_now(),
            ))
            n += 1
        s.commit()
        return n

    if session is not None:
        return _do(session)
    s = get_decision_session()
    try:
        return _do(s)
    finally:
        s.close()


def save_recommendation(rec: dict, portfolio_id=None, session=None) -> int:
    """Persist a recommendation dict. Returns the new id."""
    def _do(s):
        row = DecisionRecommendation(
            portfolio_id=portfolio_id,
            security_id=rec.get("security_id") or "",
            security_type=rec.get("security_type", "equity"),
            action=rec.get("action", "HOLD"),
            confidence=float(rec.get("confidence") or 0.0),
            signals_json=json.dumps(rec.get("signals") or [], default=str),
            upside_pct=rec.get("upside_cagr"),
            downside_pct=rec.get("downside_cagr"),
            base_case_cagr=rec.get("base_case_cagr"),
            tax_json=json.dumps(rec.get("tax"), default=str) if rec.get("tax") else None,
            reasoning=rec.get("reasoning", ""),
            evidence_ids_json=json.dumps(rec.get("evidence_ids") or [], default=str),
            llm_opinion_json=json.dumps(rec.get("llm_opinion"), default=str) if rec.get("llm_opinion") else None,
            is_shadow=True,
            created_at=rec.get("generated_at") or _now(),
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return int(row.id)

    if session is not None:
        return _do(session)
    s = get_decision_session()
    try:
        return _do(s)
    finally:
        s.close()

def record_decision(recommendation_id, security_id, user_decision, note=None,
                    price_at_decision=None, session=None) -> int:
    """Record a user's decision (HELD/TRIMMED/SOLD/IGNORED) against a recommendation."""
    def _do(s):
        row = DecisionHistory(
            recommendation_id=recommendation_id,
            security_id=security_id,
            user_decision=user_decision,
            note=note,
            decided_at=_now(),
            price_at_decision=price_at_decision,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        if recommendation_id is not None:
            rec = s.query(DecisionRecommendation).filter_by(id=recommendation_id).first()
            if rec is not None:
                rec.user_decision = user_decision
                rec.acknowledged = True
                rec.acknowledged_at = _now()
                s.commit()
        return int(row.id)

    if session is not None:
        return _do(session)
    s = get_decision_session()
    try:
        return _do(s)
    finally:
        s.close()


def acknowledge_recommendation(recommendation_id, session=None) -> bool:
    def _do(s):
        rec = s.query(DecisionRecommendation).filter_by(id=recommendation_id).first()
        if rec is None:
            return False
        rec.acknowledged = True
        rec.acknowledged_at = _now()
        s.commit()
        return True

    if session is not None:
        return _do(session)
    s = get_decision_session()
    try:
        return _do(s)
    finally:
        s.close()


def save_backtest(name, params: dict, result: dict, start_date=None, end_date=None, session=None) -> int:
    def _do(s):
        row = DecisionBacktest(
            name=name,
            start_date=start_date,
            end_date=end_date,
            params_json=json.dumps(params or {}, default=str),
            hit_rate=result.get("hit_rate"),
            false_positive_rate=result.get("false_positive_rate"),
            missed_upside_rate=result.get("missed_upside_rate"),
            alpha=result.get("signal_value_proxy"),
            result_json=json.dumps(result or {}, default=str),
            created_at=_now(),
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return int(row.id)

    if session is not None:
        return _do(session)
    s = get_decision_session()
    try:
        return _do(s)
    finally:
        s.close()


def _list(model, session, limit):
    def _do(s):
        return s.query(model).order_by(model.id.desc()).limit(limit).all()

    if session is not None:
        return _do(session)
    s = get_decision_session()
    try:
        return _do(s)
    finally:
        s.close()


def list_recent_recommendations(limit=20, session=None):
    return _list(DecisionRecommendation, session, limit)


def list_recent_signals(limit=50, session=None):
    return _list(DecisionSignal, session, limit)


def list_history(limit=50, session=None):
    return _list(DecisionHistory, session, limit)


def list_backtests(limit=20, session=None):
    return _list(DecisionBacktest, session, limit)

