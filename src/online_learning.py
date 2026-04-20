from __future__ import annotations

import sqlite3
from typing import Dict

from .evaluation import HoldoutMetrics, evaluate_campaign_holdout


DEFAULT_NEED_MULTIPLIER = 1.0


def get_need_multipliers(conn: sqlite3.Connection, business_goal: str) -> Dict[str, float]:
    rows = conn.execute(
        "SELECT need, weight_multiplier FROM need_weights WHERE business_goal = ?",
        (business_goal,),
    ).fetchall()
    return {r["need"]: float(r["weight_multiplier"]) for r in rows}


def apply_need_multipliers(needs: Dict[str, float], multipliers: Dict[str, float]) -> Dict[str, float]:
    if not needs:
        return {}
    scaled = {k: round(v * multipliers.get(k, DEFAULT_NEED_MULTIPLIER), 6) for k, v in needs.items()}
    max_score = max(scaled.values()) if scaled else 1.0
    return {k: round(v / max_score, 4) for k, v in scaled.items()}


def update_weights_from_feedback(conn: sqlite3.Connection, business_goal: str, alpha: float = 0.15) -> int:
    rows = conn.execute(
        """
        SELECT need, AVG(reward_score) AS avg_reward
        FROM rule_feedback
        WHERE business_goal = ?
        GROUP BY need
        """,
        (business_goal,),
    ).fetchall()
    updated = 0
    for r in rows:
        need = r["need"]
        avg_reward = float(r["avg_reward"])
        current = conn.execute(
            "SELECT weight_multiplier FROM need_weights WHERE business_goal = ? AND need = ?",
            (business_goal, need),
        ).fetchone()
        old_weight = float(current["weight_multiplier"]) if current else DEFAULT_NEED_MULTIPLIER
        new_weight = max(0.5, min(1.8, old_weight + alpha * (avg_reward - 0.5)))
        conn.execute(
            """
            INSERT INTO need_weights (business_goal, need, weight_multiplier, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(business_goal, need)
            DO UPDATE SET weight_multiplier = excluded.weight_multiplier, updated_at = excluded.updated_at
            """,
            (business_goal, need, round(new_weight, 4)),
        )
        updated += 1
    conn.commit()
    return updated

#TODO: 模拟反馈 当前只是简单的模拟反馈;后续用真实 ROI、转化、留存 作为 reward_score；区分 need 级 vs 规则级 vs 整包 SQL 反馈
def write_simulated_feedback(
    conn: sqlite3.Connection,
    campaign_id: str,
    business_goal: str,
    needs: Dict[str, float],
) -> None:
    conn.execute("DELETE FROM rule_feedback WHERE campaign_id = ?", (campaign_id,))
    for need, strength in needs.items():
        reward = min(0.95, max(0.05, 0.35 + strength * 0.5))
        conn.execute(
            """
            INSERT INTO rule_feedback (campaign_id, business_goal, need, reward_score, created_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (campaign_id, business_goal, need, round(reward, 4)),
        )
    conn.commit()


def _clamp_reward(value: float) -> float:
    return max(0.05, min(0.95, value))


def write_real_feedback_from_campaign(
    conn: sqlite3.Connection,
    campaign_id: str,
    package_name: str,
    business_goal: str,
    needs: Dict[str, float],
    pay_weight: float = 0.6,
    ltv_weight: float = 0.4,
) -> HoldoutMetrics:
    """
    基于 treatment/holdout 实际（或准真实）观测结果回写 need 级 reward。
    """
    metrics = evaluate_campaign_holdout(conn, campaign_id=campaign_id, package_name=package_name)
    # Convert uplift to [0.05, 0.95] reward scale.
    pay_score = _clamp_reward(0.5 + metrics.pay_uplift * 2.0)
    ltv_denom = max(metrics.holdout_ltv30, 1.0)
    ltv_uplift_ratio = metrics.ltv_uplift / ltv_denom
    ltv_score = _clamp_reward(0.5 + ltv_uplift_ratio)
    campaign_reward = _clamp_reward(pay_weight * pay_score + ltv_weight * ltv_score)

    conn.execute("DELETE FROM rule_feedback WHERE campaign_id = ?", (campaign_id,))
    for need, strength in needs.items():
        need_reward = _clamp_reward(campaign_reward * (0.7 + 0.3 * strength))
        conn.execute(
            """
            INSERT INTO rule_feedback (campaign_id, business_goal, need, reward_score, created_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (campaign_id, business_goal, need, round(need_reward, 4)),
        )
    conn.commit()
    return metrics

