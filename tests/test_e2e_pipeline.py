from __future__ import annotations

from pathlib import Path

from src.agentic_workflow import run_agentic_orchestrator
from src.data_builder import insert_game, populate_players
from src.db import connect, init_schema
from src.evaluation import evaluate_all


def test_e2e_package_quality_order(tmp_path: Path):
    conn = connect(tmp_path / "e2e.db")
    init_schema(conn)
    insert_game(
        conn,
        {
            "game_id": "G2",
            "game_name": "WarFront",
            "genre": "SLG",
            "theme": "war",
            "art_style": "realism",
            "competition_level": "high",
            "org_dependency_level": "high",
            "identity_display_level": "high",
            "max_spend_level": "high",
            "depreciation_level": "slow",
            "update_mode": "season",
            "reputation_score": 4.0,
        },
    )
    populate_players(conn, size=1200, seed=13)

    result = run_agentic_orchestrator(conn, "G2", business_goal="high_ltv")
    metrics = evaluate_all(conn, result["sql_packages"])

    assert metrics["strong_match"].size > 0
    assert metrics["high_potential_expand"].size >= metrics["strong_match"].size
    assert metrics["strong_match"].ltv30_avg >= metrics["low_cost_explore"].ltv30_avg
    assert metrics["strong_match"].first_pay_rate >= metrics["low_cost_explore"].first_pay_rate
    assert result["explain"]["avg_confidence"] > 0.6

