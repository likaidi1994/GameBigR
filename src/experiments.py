from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Dict, Tuple

from .evaluation import PackageMetrics, evaluate_sql


def _manual_sql() -> str:
    return (
        "SELECT * FROM players WHERE "
        "genre_pref IN ('SLG','Strategy') AND "
        "theme_pref IN ('history','war') AND "
        "history_spend_level IN ('medium','high')"
    )


def _pure_model_sql() -> str:
    return (
        "SELECT * FROM players "
        "ORDER BY (0.55 * simulated_ltv30 + 120 * simulated_first_pay_prob + 60 * simulated_d7_retention) DESC "
        "LIMIT 300"
    )


def _agent_plus_model_sql(agent_sql: str) -> str:
    return (
        "SELECT * FROM (" + agent_sql + ") "
        "ORDER BY (0.6 * simulated_ltv30 + 140 * simulated_first_pay_prob + 50 * simulated_d7_retention) DESC "
        "LIMIT 300"
    )


def build_experiment_sql_groups(agent_sql_packages: Dict[str, str]) -> Dict[str, str]:
    agent_core = agent_sql_packages["high_potential_expand"]
    return {
        "manual_group": _manual_sql(),
        "pure_model_group": _pure_model_sql(),
        "agent_model_group": _agent_plus_model_sql(agent_core),
    }


def export_experiment_outputs(
    conn: sqlite3.Connection,
    sql_groups: Dict[str, str],
    output_dir: Path,
) -> Tuple[Dict[str, PackageMetrics], Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics = {name: evaluate_sql(conn, sql) for name, sql in sql_groups.items()}
    summary_path = output_dir / "experiment_summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["group", "size", "install_rate", "d7_rate", "first_pay_rate", "ltv30_avg", "high_value_ratio"])
        for g, m in metrics.items():
            writer.writerow([g, m.size, m.install_rate, m.d7_rate, m.first_pay_rate, m.ltv30_avg, m.high_value_ratio])

    for name, sql in sql_groups.items():
        rows = conn.execute(sql).fetchall()
        path = output_dir / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["player_id", "simulated_install_prob", "simulated_d7_retention", "simulated_first_pay_prob", "simulated_ltv30"])
            for r in rows:
                writer.writerow(
                    [
                        r["player_id"],
                        r["simulated_install_prob"],
                        r["simulated_d7_retention"],
                        r["simulated_first_pay_prob"],
                        r["simulated_ltv30"],
                    ]
                )
    return metrics, summary_path

