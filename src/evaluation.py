from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from random import Random
from typing import Dict, List


@dataclass
class PackageMetrics:
    size: int
    install_rate: float
    d7_rate: float
    first_pay_rate: float
    ltv30_avg: float
    high_value_ratio: float


@dataclass
class HoldoutMetrics:
    treatment_size: int
    holdout_size: int
    treatment_pay_rate: float
    holdout_pay_rate: float
    pay_uplift: float
    treatment_ltv30: float
    holdout_ltv30: float
    ltv_uplift: float


def evaluate_sql(conn: sqlite3.Connection, sql: str) -> PackageMetrics:
    #TODO: 模拟指标 后续需要把sql 圈出来的结果落盘，评估的指标要根据真实数据进行评估
    rows = conn.execute(sql).fetchall()
    if not rows:
        return PackageMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    size = len(rows)
    install = sum(r["simulated_install_prob"] for r in rows) / size
    d7 = sum(r["simulated_d7_retention"] for r in rows) / size
    pay = sum(r["simulated_first_pay_prob"] for r in rows) / size
    ltv = sum(r["simulated_ltv30"] for r in rows) / size
    high_value = sum(1 for r in rows if r["simulated_ltv30"] >= 150) / size
    return PackageMetrics(size, round(install, 4), round(d7, 4), round(pay, 4), round(ltv, 2), round(high_value, 4))


def evaluate_all(conn: sqlite3.Connection, sql_packages: Dict[str, str]) -> Dict[str, PackageMetrics]:
    return {pkg: evaluate_sql(conn, sql) for pkg, sql in sql_packages.items()}


def assign_campaign_with_holdout(
    conn: sqlite3.Connection,
    campaign_id: str,
    package_name: str,
    target_sql: str,
    holdout_ratio: float = 0.2,
    seed: int = 42,
) -> int:
    rows = conn.execute(f"SELECT player_id FROM ({target_sql})").fetchall()
    player_ids = [str(r["player_id"]) for r in rows]
    if not player_ids:
        conn.execute(
            "DELETE FROM campaign_assignments WHERE campaign_id = ? AND package_name = ?",
            (campaign_id, package_name),
        )
        conn.execute(
            "DELETE FROM campaign_observations WHERE campaign_id = ? AND package_name = ?",
            (campaign_id, package_name),
        )
        conn.commit()
        return 0

    shuffler = Random(seed)
    shuffler.shuffle(player_ids)
    holdout_n = max(1, int(len(player_ids) * max(0.05, min(0.5, holdout_ratio))))
    holdout_set = set(player_ids[:holdout_n])

    conn.execute(
        "DELETE FROM campaign_assignments WHERE campaign_id = ? AND package_name = ?",
        (campaign_id, package_name),
    )
    conn.execute(
        "DELETE FROM campaign_observations WHERE campaign_id = ? AND package_name = ?",
        (campaign_id, package_name),
    )
    conn.executemany(
        """
        INSERT INTO campaign_assignments (campaign_id, package_name, player_id, is_holdout, assigned_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        """,
        [
            (campaign_id, package_name, player_id, 1 if player_id in holdout_set else 0)
            for player_id in player_ids
        ],
    )
    conn.execute(
        """
        INSERT INTO campaign_observations (
            campaign_id, package_name, player_id, install_label, d7_label, first_pay_label, ltv30_value, observed_at
        )
        SELECT
            a.campaign_id,
            a.package_name,
            p.player_id,
            p.quasi_real_install_label,
            p.quasi_real_d7_label,
            p.quasi_real_first_pay_label,
            p.quasi_real_ltv30,
            datetime('now')
        FROM campaign_assignments a
        JOIN players p ON p.player_id = a.player_id
        WHERE a.campaign_id = ? AND a.package_name = ?
        """,
        (campaign_id, package_name),
    )
    conn.commit()
    return len(player_ids)


def evaluate_campaign_holdout(
    conn: sqlite3.Connection,
    campaign_id: str,
    package_name: str,
) -> HoldoutMetrics:
    rows = conn.execute(
        """
        SELECT a.is_holdout, o.first_pay_label, o.ltv30_value
        FROM campaign_assignments a
        JOIN campaign_observations o
          ON o.campaign_id = a.campaign_id
         AND o.package_name = a.package_name
         AND o.player_id = a.player_id
        WHERE a.campaign_id = ? AND a.package_name = ?
        """,
        (campaign_id, package_name),
    ).fetchall()
    bucket: Dict[int, List[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        bucket[int(row["is_holdout"])].append(row)

    treatment_rows = bucket.get(0, [])
    holdout_rows = bucket.get(1, [])
    treatment_size = len(treatment_rows)
    holdout_size = len(holdout_rows)
    treatment_pay = (
        sum(float(r["first_pay_label"]) for r in treatment_rows) / treatment_size if treatment_size else 0.0
    )
    holdout_pay = sum(float(r["first_pay_label"]) for r in holdout_rows) / holdout_size if holdout_size else 0.0
    treatment_ltv = sum(float(r["ltv30_value"]) for r in treatment_rows) / treatment_size if treatment_size else 0.0
    holdout_ltv = sum(float(r["ltv30_value"]) for r in holdout_rows) / holdout_size if holdout_size else 0.0
    pay_uplift = treatment_pay - holdout_pay
    ltv_uplift = treatment_ltv - holdout_ltv
    return HoldoutMetrics(
        treatment_size=treatment_size,
        holdout_size=holdout_size,
        treatment_pay_rate=round(treatment_pay, 4),
        holdout_pay_rate=round(holdout_pay, 4),
        pay_uplift=round(pay_uplift, 4),
        treatment_ltv30=round(treatment_ltv, 2),
        holdout_ltv30=round(holdout_ltv, 2),
        ltv_uplift=round(ltv_uplift, 2),
    )

