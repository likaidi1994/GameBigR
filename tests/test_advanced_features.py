from __future__ import annotations

from pathlib import Path

from src.agentic_workflow import run_agentic_orchestrator
from src.data_builder import insert_game, populate_players
from src.db import connect, init_schema
from src.evaluation import assign_campaign_with_holdout, evaluate_campaign_holdout
from src.experiments import build_experiment_sql_groups, export_experiment_outputs
from src.llm_rules import _normalize_rule
from src.online_learning import update_weights_from_feedback, write_real_feedback_from_campaign, write_simulated_feedback


def _bootstrap(tmp_path: Path):
    conn = connect(tmp_path / "adv.db")
    init_schema(conn)
    insert_game(
        conn,
        {
            "game_id": "GA",
            "game_name": "AllianceWar",
            "genre": "SLG",
            "theme": "war",
            "art_style": "realism",
            "competition_level": "high",
            "org_dependency_level": "high",
            "identity_display_level": "high",
            "max_spend_level": "high",
            "depreciation_level": "slow",
            "update_mode": "season",
            "reputation_score": 4.05,
        },
    )
    populate_players(conn, size=1000, seed=31)
    return conn


def test_online_learning_weight_update(tmp_path: Path):
    conn = _bootstrap(tmp_path)
    first = run_agentic_orchestrator(conn, "GA", business_goal="high_ltv")
    write_simulated_feedback(conn, "CMP_X", "high_ltv", first["needs"])
    updated_count = update_weights_from_feedback(conn, "high_ltv")
    second = run_agentic_orchestrator(conn, "GA", business_goal="high_ltv")
    assert updated_count > 0
    assert first["needs"] != second["needs"]


def test_experiment_export_three_groups(tmp_path: Path):
    conn = _bootstrap(tmp_path)
    result = run_agentic_orchestrator(conn, "GA", business_goal="first_pay")
    groups = build_experiment_sql_groups(result["sql_packages"])
    metrics, summary = export_experiment_outputs(conn, groups, tmp_path / "outputs")
    assert set(groups.keys()) == {"manual_group", "pure_model_group", "agent_model_group"}
    assert summary.exists()
    assert metrics["agent_model_group"].size > 0


def test_normalize_rule_accepts_python_list_string_for_in_operator():
    rule = {
        "rule_id": "llm_theme_rule",
        "tier": "P1",
        "need": "long_term_value",
        "target_column": "theme_pref",
        "operator": "in",
        "value": "['history','war','strategy']",
        "description": "主题偏好",
        "confidence": 0.75,
    }
    normalized = _normalize_rule(rule)
    assert normalized is not None
    assert normalized["sql_expr"] == "theme_pref IN ('history','war','strategy')"


def test_holdout_evaluation_and_real_feedback(tmp_path: Path):
    conn = _bootstrap(tmp_path)
    result = run_agentic_orchestrator(conn, "GA", business_goal="high_ltv")
    assigned = assign_campaign_with_holdout(
        conn,
        campaign_id="CMP_REAL",
        package_name="high_potential_expand",
        target_sql=result["sql_packages"]["high_potential_expand"],
        holdout_ratio=0.2,
        seed=23,
    )
    assert assigned > 0

    metrics = evaluate_campaign_holdout(conn, campaign_id="CMP_REAL", package_name="high_potential_expand")
    assert metrics.treatment_size > 0
    assert metrics.holdout_size > 0

    write_real_feedback_from_campaign(
        conn,
        campaign_id="CMP_REAL",
        package_name="high_potential_expand",
        business_goal="high_ltv",
        needs=result["needs"],
    )
    updated = update_weights_from_feedback(conn, business_goal="high_ltv")
    assert updated > 0

