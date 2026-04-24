from __future__ import annotations

import sqlite3
from typing import Dict, Iterable, List, Mapping, Optional

from .evaluation import HoldoutMetrics, evaluate_campaign_holdout


DEFAULT_NEED_MULTIPLIER = 1.0


def get_need_multipliers(conn: sqlite3.Connection, business_goal: str) -> Dict[str, float]:
    rows = conn.execute(
        "SELECT need, weight_multiplier FROM need_weights WHERE business_goal = ?",
        (business_goal,),
    ).fetchall()
    return {r["need"]: float(r["weight_multiplier"]) for r in rows}


def get_rule_multipliers(conn: sqlite3.Connection, business_goal: str) -> Dict[str, float]:
    rows = conn.execute(
        "SELECT rule_id, weight_multiplier FROM rule_weights WHERE business_goal = ?",
        (business_goal,),
    ).fetchall()
    return {r["rule_id"]: float(r["weight_multiplier"]) for r in rows}


def apply_need_multipliers(needs: Dict[str, float], multipliers: Dict[str, float]) -> Dict[str, float]:
    if not needs:
        return {}
    scaled = {k: round(v * multipliers.get(k, DEFAULT_NEED_MULTIPLIER), 6) for k, v in needs.items()}
    max_score = max(scaled.values()) if scaled else 1.0
    return {k: round(v / max_score, 4) for k, v in scaled.items()}


def apply_rule_multipliers(
    rules: List[Dict[str, object]], multipliers: Mapping[str, float]
) -> List[Dict[str, object]]:
    if not rules:
        return []
    adjusted: List[Dict[str, object]] = []
    for rule in rules:
        copied = dict(rule)
        rule_id = str(copied.get("rule_id", ""))
        base_conf = float(copied.get("confidence", 0.7))
        multiplier = float(multipliers.get(rule_id, DEFAULT_NEED_MULTIPLIER))
        copied["learning_multiplier"] = round(multiplier, 4)
        copied["confidence"] = round(max(0.05, min(0.99, base_conf * multiplier)), 4)
        adjusted.append(copied)
    return adjusted


def update_weights_from_feedback(conn: sqlite3.Connection, business_goal: str, alpha: float = 0.15) -> int:
    need_rows = conn.execute(
        """
        SELECT need, AVG(reward_score) AS avg_reward
        FROM rule_feedback
        WHERE business_goal = ?
          AND feedback_level = 'need'
        GROUP BY need
        """,
        (business_goal,),
    ).fetchall()
    need_updated = 0
    for r in need_rows:
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
        need_updated += 1

    rule_rows = conn.execute(
        """
        SELECT rule_id, AVG(reward_score) AS avg_reward
        FROM rule_feedback
        WHERE business_goal = ?
          AND feedback_level = 'rule'
          AND rule_id IS NOT NULL
        GROUP BY rule_id
        """,
        (business_goal,),
    ).fetchall()
    rule_updated = 0
    for r in rule_rows:
        rule_id = str(r["rule_id"])
        avg_reward = float(r["avg_reward"])
        current = conn.execute(
            "SELECT weight_multiplier FROM rule_weights WHERE business_goal = ? AND rule_id = ?",
            (business_goal, rule_id),
        ).fetchone()
        old_weight = float(current["weight_multiplier"]) if current else DEFAULT_NEED_MULTIPLIER
        new_weight = max(0.5, min(1.8, old_weight + alpha * (avg_reward - 0.5)))
        conn.execute(
            """
            INSERT INTO rule_weights (business_goal, rule_id, weight_multiplier, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(business_goal, rule_id)
            DO UPDATE SET weight_multiplier = excluded.weight_multiplier, updated_at = excluded.updated_at
            """,
            (business_goal, rule_id, round(new_weight, 4)),
        )
        rule_updated += 1
    conn.commit()
    return need_updated + rule_updated

#TODO: 模拟反馈 当前只是简单的模拟反馈;后续用真实 ROI、转化、留存 作为 reward_score；区分 need 级 vs 规则级 vs 整包 SQL 反馈
def write_simulated_feedback(
    conn: sqlite3.Connection,
    campaign_id: str,
    package_name: str,
    business_goal: str,
    needs: Dict[str, float],
    rules: Optional[Iterable[Mapping[str, object]]] = None,
) -> None:
    conn.execute("DELETE FROM rule_feedback WHERE campaign_id = ?", (campaign_id,))
    for need, strength in needs.items():
        reward = min(0.95, max(0.05, 0.35 + strength * 0.5))
        conn.execute(
            """
            INSERT INTO rule_feedback (
                campaign_id, business_goal, package_name, need, rule_id, feedback_level, reward_score, created_at
            )
            VALUES (?, ?, ?, ?, NULL, 'need', ?, datetime('now'))
            """,
            (campaign_id, business_goal, package_name, need, round(reward, 4)),
        )
    if rules:
        for rule in rules:
            need = str(rule.get("need", ""))
            rule_id = str(rule.get("rule_id", ""))
            if not need or not rule_id or need not in needs:
                continue
            strength = float(needs.get(need, 0.5))
            conf = float(rule.get("confidence", 0.7))
            reward = _clamp_reward(0.4 + 0.4 * strength + 0.2 * conf)
            conn.execute(
                """
                INSERT INTO rule_feedback (
                    campaign_id, business_goal, package_name, need, rule_id, feedback_level, reward_score, created_at
                )
                VALUES (?, ?, ?, ?, ?, 'rule', ?, datetime('now'))
                """,
                (campaign_id, business_goal, package_name, need, rule_id, round(reward, 4)),
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
    rules: Optional[Iterable[Mapping[str, object]]] = None,
    pay_weight: float = 0.6,
    ltv_weight: float = 0.4,
) -> HoldoutMetrics:
    """
    基于 treatment/holdout 实际（或准真实）观测结果回写 need 与 rule 级 reward。
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
            INSERT INTO rule_feedback (
                campaign_id, business_goal, package_name, need, rule_id, feedback_level, reward_score, created_at
            )
            VALUES (?, ?, ?, ?, NULL, 'need', ?, datetime('now'))
            """,
            (campaign_id, business_goal, package_name, need, round(need_reward, 4)),
        )
    if rules:
        for rule in rules:
            need = str(rule.get("need", ""))
            rule_id = str(rule.get("rule_id", ""))
            if not need or not rule_id or need not in needs:
                continue
            need_strength = float(needs[need])
            conf = float(rule.get("confidence", 0.7))
            rule_reward = _clamp_reward(campaign_reward * (0.6 + 0.2 * need_strength + 0.2 * conf))
            conn.execute(
                """
                INSERT INTO rule_feedback (
                    campaign_id, business_goal, package_name, need, rule_id, feedback_level, reward_score, created_at
                )
                VALUES (?, ?, ?, ?, ?, 'rule', ?, datetime('now'))
                """,
                (campaign_id, business_goal, package_name, need, rule_id, round(rule_reward, 4)),
            )
    conn.commit()
    return metrics

