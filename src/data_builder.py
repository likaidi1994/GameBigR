from __future__ import annotations

import random
import sqlite3
from typing import Dict


def insert_game(conn: sqlite3.Connection, game: Dict[str, object]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO games (
            game_id, game_name, genre, theme, art_style,
            competition_level, org_dependency_level, identity_display_level,
            max_spend_level, depreciation_level, update_mode, reputation_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game["game_id"],
            game["game_name"],
            game["genre"],
            game["theme"],
            game["art_style"],
            game["competition_level"],
            game["org_dependency_level"],
            game["identity_display_level"],
            game["max_spend_level"],
            game["depreciation_level"],
            game["update_mode"],
            game["reputation_score"],
        ),
    )
    conn.commit()


def _simulated_targets(is_target_like: bool) -> Dict[str, float]:
    if is_target_like:
        return {
            "install": random.uniform(0.45, 0.82),
            "d7": random.uniform(0.35, 0.70),
            "pay": random.uniform(0.18, 0.55),
            "ltv30": random.uniform(80, 450),
        }
    return {
        "install": random.uniform(0.05, 0.35),
        "d7": random.uniform(0.04, 0.30),
        "pay": random.uniform(0.01, 0.15),
        "ltv30": random.uniform(5, 120),
    }


def _to_binary_label(prob: float, jitter: float = 0.06) -> int:
    sampled = max(0.01, min(0.99, prob + random.uniform(-jitter, jitter)))
    return 1 if random.random() < sampled else 0


def populate_players(conn: sqlite3.Connection, size: int = 2000, seed: int = 7) -> None:
    random.seed(seed)
    conn.execute("DELETE FROM players")
    for i in range(size):
        target_like = i < int(size * 0.34)
        spend = random.choices(["low", "medium", "high"], [1, 2, 3] if target_like else [5, 3, 1])[0]
        potential = random.choices(["weak", "steady", "strong"], [1, 2, 3] if target_like else [5, 3, 1])[0]
        genre = random.choices(["SLG", "RPG", "casual"], [5, 2, 1] if target_like else [1, 3, 4])[0]
        theme = random.choices(["history", "war", "fantasy", "anime"], [3, 3, 1, 1] if target_like else [1, 1, 3, 3])[0]
        time_slot = random.choices(["evening", "night", "day"], [5, 2, 1] if target_like else [2, 2, 4])[0]
        community = random.choices(["leader", "active_fan", "silent"], [3, 4, 1] if target_like else [1, 2, 6])[0]
        rating = random.choices(["low", "medium", "high"], [4, 4, 2] if target_like else [2, 3, 5])[0]
        device = random.choices(["low", "mid", "high"], [1, 4, 4] if target_like else [4, 4, 2])[0]

        guild_views = random.randint(8, 80) if target_like else random.randint(0, 20)
        voice = random.randint(20, 300) if target_like else random.randint(0, 50)
        team_minutes = random.randint(60, 500) if target_like else random.randint(0, 90)
        fragments = random.randint(6, 20) if target_like else random.randint(2, 15)
        entropy = random.uniform(0.35, 0.90)
        skin_clicks = random.randint(10, 60) if target_like else random.randint(0, 12)
        screenshots = random.randint(2, 30) if target_like else random.randint(0, 4)
        targets = _simulated_targets(target_like)
        install_label = _to_binary_label(targets["install"], jitter=0.05)
        d7_base = targets["d7"] * (1.12 if install_label else 0.82)
        d7_label = _to_binary_label(d7_base, jitter=0.04)
        pay_base = targets["pay"] * (1.10 if d7_label else 0.88)
        pay_label = _to_binary_label(pay_base, jitter=0.05)
        ltv_label = max(
            0.0,
            targets["ltv30"] * random.uniform(0.82, 1.18) + (36.0 if pay_label else 0.0),
        )

        conn.execute(
            """
            INSERT INTO players VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"P{i:05d}",
                spend,
                potential,
                genre,
                theme,
                time_slot,
                community,
                rating,
                device,
                guild_views,
                voice,
                team_minutes,
                fragments,
                entropy,
                skin_clicks,
                screenshots,
                targets["install"],
                targets["d7"],
                targets["pay"],
                targets["ltv30"],
                install_label,
                d7_label,
                pay_label,
                round(ltv_label, 2),
            ),
        )
    conn.commit()

