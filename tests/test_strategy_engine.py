from __future__ import annotations

from pathlib import Path

from src.agentic_workflow import run_agentic_orchestrator
from src.data_builder import insert_game, populate_players
from src.db import connect, init_schema


def _setup_db(tmp_path: Path):
    conn = connect(tmp_path / "unit.db")
    init_schema(conn)
    insert_game(
        conn,
        {
            "game_id": "G1",
            "game_name": "SLG-X",
            "genre": "SLG",
            "theme": "history",
            "art_style": "realism",
            "competition_level": "high",
            "org_dependency_level": "high",
            "identity_display_level": "high",
            "max_spend_level": "high",
            "depreciation_level": "slow",
            "update_mode": "season",
            "reputation_score": 4.1,
        },
    )
    populate_players(conn, size=400, seed=23)
    return conn


def test_orchestrator_generates_tiers_and_sql(tmp_path: Path):
    conn = _setup_db(tmp_path)
    result = run_agentic_orchestrator(conn, "G1", business_goal="first_pay")
    tiers = {r["tier"] for r in result["rules"]}
    assert "P0" in tiers
    assert "P1" in tiers
    assert "EXCLUSION" in tiers
    assert "strong_match" in result["sql_packages"]
    assert "not_recommended" in result["sql_packages"]
    assert any("GameParserAgent" in step for step in result["trace"])
    assert any("EvidenceAgent" in step for step in result["trace"])


def test_missing_field_uses_proxy_sql(tmp_path: Path):
    conn = _setup_db(tmp_path)
    result = run_agentic_orchestrator(conn, "G1")
    proxy_rules = [r for r in result["rules"] if r["evidence_source"] == "proxy"]
    assert proxy_rules
    assert any("guild_page_views" in r["sql_expr"] for r in proxy_rules)

