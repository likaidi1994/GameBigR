from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

from .ontology import GAME_TO_NEED_RULES, NEED_TO_RULE_TEMPLATES, PLAYER_AVAILABILITY, RuleDefinition


VALUE_ORDER = {
    "low": 1,
    "weak": 1,
    "medium": 2,
    "steady": 2,
    "mid": 2,
    "high": 3,
    "strong": 3,
}


@dataclass
class StrategyOutput:
    needs: Dict[str, float]
    rules: List[Dict[str, object]]
    sql_packages: Dict[str, str]

#TODO: 游戏标签解析 当前只是简单的游戏标签解析，未来需要根据业务目标调整
def parse_game_to_needs(game_row: sqlite3.Row) -> Dict[str, float]:
    tags = []
    if game_row["competition_level"] == "high":
        tags.append("high_competition")
    if game_row["identity_display_level"] == "high":
        tags.append("high_identity_display")
    if game_row["org_dependency_level"] == "high":
        tags.append("high_org_dependency")
    if game_row["max_spend_level"] == "high":
        tags.append("high_max_spend")
    if game_row["depreciation_level"] == "slow":
        tags.append("slow_depreciation")
    if game_row["update_mode"] == "season":
        tags.append("seasonal_update")
    if game_row["art_style"] == "anime":
        tags.append("anime_style")
    if game_row["reputation_score"] >= 4.4:
        tags.append("high_visual_quality")
    if game_row["genre"] in ("SLG", "action"):
        tags.append("fast_feedback")

    needs = defaultdict(float)
    for t in tags:
        for need, score in GAME_TO_NEED_RULES.get(t, {}).items():
            needs[need] += score

    max_score = max(needs.values()) if needs else 1.0
    return {k: round(v / max_score, 4) for k, v in sorted(needs.items(), key=lambda x: x[1], reverse=True)}


def _proxy_expr(column: str) -> str:
    #TODO: 代理变量表达式 当前只是简单的代理变量表达式，未来需要根据业务目标进行更复杂的调整
    if column == "org_engagement":
        return "(guild_page_views >= 20 OR voice_minutes >= 90 OR team_session_minutes >= 180)"
    if column == "busy_level":
        return "(session_fragments >= 10 OR login_slot_entropy >= 0.65)"
    if column == "visual_validation":
        return "(skin_preview_clicks >= 8 OR screenshot_actions >= 2)"
    return ""


def _value_cmp(column: str, operator: str, value: str) -> str:
    if operator == "in":
        vals = ",".join(f"'{x.strip()}'" for x in value.split(","))
        return f"{column} IN ({vals})"
    if operator == "=":
        return f"{column} = '{value}'"
    if operator == ">=" and value in VALUE_ORDER:
        return f"CASE {column} WHEN 'low' THEN 1 WHEN 'weak' THEN 1 WHEN 'medium' THEN 2 WHEN 'steady' THEN 2 WHEN 'mid' THEN 2 WHEN 'high' THEN 3 WHEN 'strong' THEN 3 END >= {VALUE_ORDER[value]}"
    if operator == "guardrail" and value == "high_if_low_reputation":
        return "(rating_sensitivity = 'high')"
    return "1=1"


def _build_rule_sql(rule: RuleDefinition, game_row: sqlite3.Row) -> Tuple[str, str]:
    availability = PLAYER_AVAILABILITY.get(rule.target_column)
    expr = _value_cmp(rule.target_column, rule.operator, rule.value)
    source = "direct"
    if availability and availability.status in {"partial", "missing"}:
        proxy = _proxy_expr(rule.target_column)
        if proxy:
            expr = proxy
            source = "proxy"
    if rule.tier == "EXCLUSION" and game_row["reputation_score"] < 4.2:
        return expr, source
    if rule.tier == "EXCLUSION":
        return "0=1", source
    return expr, source


def select_rules_and_build_sql(needs: Dict[str, float], game_row: sqlite3.Row) -> StrategyOutput:
    ranked_needs = [n for n, _ in sorted(needs.items(), key=lambda x: x[1], reverse=True)]
    # Keep the previous package size target: rules produced by top-7 needs.
    # We will globally rank all candidate rules by need_strength * confidence,
    # then truncate to this budget.
    chosen = ranked_needs[:7]
    target_rule_count = sum(len(NEED_TO_RULE_TEMPLATES.get(need, [])) for need in chosen)

    candidate_rows: List[Dict[str, object]] = []
    for need in ranked_needs:
        need_strength = float(needs.get(need, 0.0))
        for rule in NEED_TO_RULE_TEMPLATES.get(need, []):
            expr, source = _build_rule_sql(rule, game_row)
            #TODO: 数据可用性置信度 当前只是简单的数据可用性置信度，未来需要根据业务目标进行更复杂的调整
            conf = PLAYER_AVAILABILITY.get(rule.target_column).confidence if rule.target_column in PLAYER_AVAILABILITY else 0.7
            selection_score = round(need_strength * float(conf), 6)
            row = {
                "rule_id": rule.rule_id,
                "tier": rule.tier,
                "need": need,
                "description": rule.description,
                "sql_expr": expr,
                "evidence_source": source,
                "confidence": round(conf, 2),
                "selection_score": selection_score,
            }
            candidate_rows.append(row)

    sorted_rows = sorted(candidate_rows, key=lambda r: float(r.get("selection_score", 0.0)), reverse=True)
    rule_rows = sorted_rows[:target_rule_count] if target_rule_count > 0 else sorted_rows

    sql_packages = build_sql_packages_from_rules(rule_rows)
    return StrategyOutput(needs=needs, rules=rule_rows, sql_packages=sql_packages)


def build_sql_packages_from_rules(rule_rows: List[Dict[str, object]]) -> Dict[str, str]:
    p0_sql = [str(r["sql_expr"]) for r in rule_rows if r["tier"] == "P0"]
    p1_sql = [str(r["sql_expr"]) for r in rule_rows if r["tier"] == "P1"]
    p2_sql = [str(r["sql_expr"]) for r in rule_rows if r["tier"] == "P2"]
    excl_sql = [str(r["sql_expr"]) for r in rule_rows if r["tier"] == "EXCLUSION"]
    p0 = " AND ".join(f"({x})" for x in p0_sql) if p0_sql else "1=1"
    p1 = " AND ".join(f"({x})" for x in p1_sql) if p1_sql else "1=1"
    p2 = " AND ".join(f"({x})" for x in p2_sql) if p2_sql else "1=1"
    ex = " OR ".join(f"({x})" for x in excl_sql) if excl_sql else "0=1"
    return {
        "strong_match": f"SELECT * FROM players WHERE {p0} AND {p1} AND NOT ({ex})",
        "high_potential_expand": f"SELECT * FROM players WHERE {p0} AND ({p1} OR {p2}) AND NOT ({ex})",
        "low_cost_explore": f"SELECT * FROM players WHERE {p2} AND NOT ({ex})",
        "not_recommended": f"SELECT * FROM players WHERE ({ex})",
    }


def run_orchestrator(conn: sqlite3.Connection, game_id: str) -> StrategyOutput:
    game_row = conn.execute("SELECT * FROM games WHERE game_id = ?", (game_id,)).fetchone()
    if not game_row:
        raise ValueError(f"game_id {game_id} not found")
    needs = parse_game_to_needs(game_row)
    return select_rules_and_build_sql(needs, game_row)

