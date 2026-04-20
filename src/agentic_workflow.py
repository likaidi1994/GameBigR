from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .data_builder import insert_game
from .game_description_parser import GameDescriptionExtraction, extract_game_profile_from_description
from .llm_rules import rewrite_rules_with_llm
from .online_learning import apply_need_multipliers, get_need_multipliers
from .strategy_engine import (
    StrategyOutput,
    build_sql_packages_from_rules,
    parse_game_to_needs,
    select_rules_and_build_sql,
)


class CircleState(TypedDict, total=False):
    conn: sqlite3.Connection
    game_id: str
    business_goal: str
    game_profile: Dict[str, Any]
    needs: Dict[str, float]
    strategy_output: StrategyOutput
    explain: Dict[str, Any]
    llm_notes: str
    llm_used: bool
    trace: List[str]


def _append_trace(state: CircleState, message: str) -> List[str]:
    trace = list(state.get("trace", []))
    trace.append(message)
    return trace


def _goal_adjustments(needs: Dict[str, float], business_goal: str) -> Dict[str, float]:
    #TODO: 业务目标调整需求向量 当前只是在已有需求的基础上进行微调，未来需要根据业务目标进行更复杂的调整
    boosts = {
        "high_ltv": {"status_control": 0.08, "long_term_value": 0.08},
        "first_pay": {"status_control": 0.10, "tension_drive": 0.05},
        "high_d7": {"collective_endorsement": 0.08, "audio_visual_soothe": 0.08},
        "acquisition": {"surprise_feedback": 0.10, "audio_visual_soothe": 0.05},
    }
    extra = boosts.get(business_goal, {})
    merged = dict(needs)
    for k, v in extra.items():
        merged[k] = round(merged.get(k, 0.0) + v, 4)
    if not merged:
        return merged
    max_score = max(merged.values())
    return {k: round(v / max_score, 4) for k, v in merged.items()}


def game_parser_agent(state: CircleState) -> CircleState:
    conn = state["conn"]
    game_row = conn.execute("SELECT * FROM games WHERE game_id = ?", (state["game_id"],)).fetchone()
    if not game_row:
        raise ValueError(f"game_id {state['game_id']} not found")
    profile = {k: game_row[k] for k in game_row.keys()}
    needs = parse_game_to_needs(game_row)
    needs = _goal_adjustments(needs, state.get("business_goal", "high_ltv"))
    multipliers = get_need_multipliers(conn, state.get("business_goal", "high_ltv"))
    needs = apply_need_multipliers(needs, multipliers)
    return {
        "game_profile": profile,
        "needs": needs,
        "trace": _append_trace(state, "GameParserAgent: 游戏特征已解析并生成需求向量"),
    }


def rule_selector_agent(state: CircleState) -> CircleState:
    conn = state["conn"]
    game_row = conn.execute("SELECT * FROM games WHERE game_id = ?", (state["game_id"],)).fetchone()
    result = select_rules_and_build_sql(state["needs"], game_row)
    return {
        "strategy_output": result,
        "trace": _append_trace(state, "RuleSelectorAgent: 已生成P0/P1/P2/Exclusion规则候选"),
    }


def llm_rewriter_agent(state: CircleState) -> CircleState:
    strategy = state["strategy_output"]
    rewrite = rewrite_rules_with_llm(
        base_rules=strategy.rules,
        game_profile=state["game_profile"],
        needs=state["needs"],
        business_goal=state.get("business_goal", "high_ltv"),
    )
    strategy.rules = rewrite.rewritten_rules
    strategy.sql_packages = build_sql_packages_from_rules(strategy.rules)
    return {
        "strategy_output": strategy,
        "llm_notes": rewrite.notes,
        "llm_used": rewrite.llm_used,
        "trace": _append_trace(
            state,
            f"LLMRuleRewriterAgent: 已完成规则重写与扩展 (llm_used={rewrite.llm_used})",
        ),
    }


def evidence_agent(state: CircleState) -> CircleState:
    strategy = state["strategy_output"]
    direct_rules = sum(1 for r in strategy.rules if r["evidence_source"] == "direct")
    proxy_rules = sum(1 for r in strategy.rules if r["evidence_source"] == "proxy")
    avg_confidence = round(sum(float(r["confidence"]) for r in strategy.rules) / max(len(strategy.rules), 1), 4)
    explain = {
        "direct_rules": direct_rules,
        "proxy_rules": proxy_rules,
        "llm_rules": sum(1 for r in strategy.rules if str(r["evidence_source"]).startswith("llm")),
        "avg_confidence": avg_confidence,
        "risk_flags": ["proxy_dependency_high"] if proxy_rules >= direct_rules else [],
    }
    return {
        "explain": explain,
        "trace": _append_trace(state, "EvidenceAgent: 已完成可用性校验与代理证据评估"),
    }


def audience_builder_agent(state: CircleState) -> CircleState:
    # SQL packages are already built; this node marks them as executable products.
    return {
        "trace": _append_trace(state, "AudienceBuilderAgent: 已生成可执行人群包SQL"),
    }


def build_circle_workflow():
    #TODO: 圈群工作流 当前只有game_parser_agent,llm_rewriter_agent使用了LLM做智能推理，后续需要根据实际情况调整；
    graph = StateGraph(CircleState)
    graph.add_node("game_parser_agent", game_parser_agent)
    graph.add_node("rule_selector_agent", rule_selector_agent)
    graph.add_node("llm_rewriter_agent", llm_rewriter_agent)
    graph.add_node("evidence_agent", evidence_agent)
    graph.add_node("audience_builder_agent", audience_builder_agent)
    graph.add_edge(START, "game_parser_agent")
    graph.add_edge("game_parser_agent", "rule_selector_agent")
    graph.add_edge("rule_selector_agent", "llm_rewriter_agent")
    graph.add_edge("llm_rewriter_agent", "evidence_agent")
    graph.add_edge("evidence_agent", "audience_builder_agent")
    graph.add_edge("audience_builder_agent", END)
    return graph.compile()


def run_agentic_orchestrator(
    conn: sqlite3.Connection,
    game_id: str,
    business_goal: str = "high_ltv",
) -> Dict[str, Any]:
    app = build_circle_workflow()
    result_state: CircleState = app.invoke({"conn": conn, "game_id": game_id, "business_goal": business_goal, "trace": []})
    return {
        "game_profile": result_state["game_profile"],
        "needs": result_state["needs"],
        "rules": result_state["strategy_output"].rules,
        "sql_packages": result_state["strategy_output"].sql_packages,
        "explain": result_state["explain"],
        "llm_used": result_state.get("llm_used", False),
        "llm_notes": result_state.get("llm_notes", ""),
        "trace": result_state["trace"],
    }


def run_agentic_orchestrator_from_description(
    conn: sqlite3.Connection,
    game_description: str,
    business_goal: str = "high_ltv",
    *,
    game_id: Optional[str] = None,
    game_name: Optional[str] = None,
    upsert_db: bool = True,
) -> Dict[str, Any]:
    """
    从自然语言游戏描述解析画像并写入 games 表，再执行与 run_agentic_orchestrator 相同的编排。

    若数据库依赖 players 等表做评估，请在调用本函数之前先写入模拟/真实玩家数据。

    返回结果在 run_agentic_orchestrator 基础上增加 extraction 字段（意图、置信度、解析方式等）。
    """
    extraction: GameDescriptionExtraction = extract_game_profile_from_description(
        game_description,
        game_id=game_id,
        game_name=game_name,
    )
    profile = extraction.profile
    if upsert_db:
        insert_game(conn, profile)

    base = run_agentic_orchestrator(conn, str(profile["game_id"]), business_goal=business_goal)
    base["extraction"] = {
        "intent": extraction.intent,
        "confidence": extraction.confidence,
        "method": extraction.method,
        "notes": extraction.notes,
        "profile": profile,
    }
    return base

