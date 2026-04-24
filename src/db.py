from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    game_name TEXT NOT NULL,
    genre TEXT NOT NULL,
    theme TEXT NOT NULL,
    art_style TEXT NOT NULL,
    competition_level TEXT NOT NULL,
    org_dependency_level TEXT NOT NULL,
    identity_display_level TEXT NOT NULL,
    max_spend_level TEXT NOT NULL,
    depreciation_level TEXT NOT NULL,
    update_mode TEXT NOT NULL,
    reputation_score REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY,
    history_spend_level TEXT NOT NULL,
    spend_potential_level TEXT NOT NULL,
    genre_pref TEXT NOT NULL,
    theme_pref TEXT NOT NULL,
    active_time_slot TEXT NOT NULL,
    community_role TEXT NOT NULL,
    rating_sensitivity TEXT NOT NULL,
    device_tier TEXT NOT NULL,
    guild_page_views INTEGER NOT NULL,
    voice_minutes INTEGER NOT NULL,
    team_session_minutes INTEGER NOT NULL,
    session_fragments INTEGER NOT NULL,
    login_slot_entropy REAL NOT NULL,
    skin_preview_clicks INTEGER NOT NULL,
    screenshot_actions INTEGER NOT NULL,
    simulated_install_prob REAL NOT NULL,
    simulated_d7_retention REAL NOT NULL,
    simulated_first_pay_prob REAL NOT NULL,
    simulated_ltv30 REAL NOT NULL,
    quasi_real_install_label INTEGER NOT NULL DEFAULT 0,
    quasi_real_d7_label INTEGER NOT NULL DEFAULT 0,
    quasi_real_first_pay_label INTEGER NOT NULL DEFAULT 0,
    quasi_real_ltv30 REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS campaign_results (
    campaign_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    player_id TEXT NOT NULL,
    expected_quality_score REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_assignments (
    campaign_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    player_id TEXT NOT NULL,
    is_holdout INTEGER NOT NULL,
    assigned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_observations (
    campaign_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    player_id TEXT NOT NULL,
    install_label INTEGER NOT NULL,
    d7_label INTEGER NOT NULL,
    first_pay_label INTEGER NOT NULL,
    ltv30_value REAL NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_feedback (
    campaign_id TEXT NOT NULL,
    business_goal TEXT NOT NULL,
    package_name TEXT,
    need TEXT NOT NULL,
    rule_id TEXT,
    feedback_level TEXT NOT NULL DEFAULT 'need',
    reward_score REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS need_weights (
    business_goal TEXT NOT NULL,
    need TEXT NOT NULL,
    weight_multiplier REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (business_goal, need)
);

CREATE TABLE IF NOT EXISTS rule_weights (
    business_goal TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    weight_multiplier REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (business_goal, rule_id)
);
"""

#TODO: 数据库连接,当前穿刺用sqlite 来保存模拟数据，后面根据需要修改为clickhouse
def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    # Compatibility migration for pre-existing local db files.
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(players)").fetchall()}
    missing_cols = [
        ("quasi_real_install_label", "INTEGER NOT NULL DEFAULT 0"),
        ("quasi_real_d7_label", "INTEGER NOT NULL DEFAULT 0"),
        ("quasi_real_first_pay_label", "INTEGER NOT NULL DEFAULT 0"),
        ("quasi_real_ltv30", "REAL NOT NULL DEFAULT 0"),
    ]
    for col, sql_type in missing_cols:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE players ADD COLUMN {col} {sql_type}")
    feedback_cols = {row["name"] for row in conn.execute("PRAGMA table_info(rule_feedback)").fetchall()}
    if "package_name" not in feedback_cols:
        conn.execute("ALTER TABLE rule_feedback ADD COLUMN package_name TEXT")
    if "rule_id" not in feedback_cols:
        conn.execute("ALTER TABLE rule_feedback ADD COLUMN rule_id TEXT")
    if "feedback_level" not in feedback_cols:
        conn.execute("ALTER TABLE rule_feedback ADD COLUMN feedback_level TEXT NOT NULL DEFAULT 'need'")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rule_weights (
            business_goal TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            weight_multiplier REAL NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (business_goal, rule_id)
        )
        """
    )
    conn.commit()

