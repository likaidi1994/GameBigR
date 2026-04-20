from __future__ import annotations

from pathlib import Path

from src.agentic_workflow import run_agentic_orchestrator_from_description
from src.data_builder import populate_players
from src.db import connect, init_schema
from src.game_description_parser import extract_game_profile_from_description


def test_extract_slg_season_heuristic():
    text = "一款三国题材的赛季制策略手游，强公会协作与国战，重氪外观展示，口碑4.6分"
    r = extract_game_profile_from_description(text)
    assert r.profile["genre"] == "SLG"
    assert r.profile["theme"] == "history"
    assert r.profile["update_mode"] == "season"
    assert r.profile["org_dependency_level"] == "high"
    assert r.method == "heuristic" or r.method == "llm"


def test_stable_game_id_same_description():
    t = "二次元卡牌养成，轻竞技，赛季活动多"
    a = extract_game_profile_from_description(t).profile["game_id"]
    b = extract_game_profile_from_description(t).profile["game_id"]
    assert a == b


def test_run_orchestrator_from_description(tmp_path: Path):
    conn = connect(tmp_path / "gd.db")
    init_schema(conn)
    desc = "SLG沙盘国战，组织依赖强，赛季制，写实画风，口碑好"
    ext = extract_game_profile_from_description(desc, game_id="G_TEST_DESC")
    assert ext.profile["game_id"] == "G_TEST_DESC"
    populate_players(conn, size=400, seed=2)
    out = run_agentic_orchestrator_from_description(
        conn,
        desc,
        business_goal="high_ltv",
        game_id="G_TEST_DESC",
        upsert_db=True,
    )
    assert "extraction" in out
    assert out["extraction"]["profile"]["game_id"] == "G_TEST_DESC"
    assert len(out["needs"]) > 0
