from __future__ import annotations

import argparse
from pathlib import Path

from src.agentic_workflow import run_agentic_orchestrator
from src.data_builder import insert_game, populate_players
from src.game_description_parser import extract_game_profile_from_description
from src.db import connect, init_schema
from src.evaluation import assign_campaign_with_holdout, evaluate_all
from src.experiments import build_experiment_sql_groups, export_experiment_outputs
from src.online_learning import update_weights_from_feedback, write_real_feedback_from_campaign


def main() -> None:
    parser = argparse.ArgumentParser(description="端到端演示：数据库画像或自然语言描述驱动 Agent")
    parser.add_argument(
        "--from-description",
        metavar="TEXT",
        help="用自然语言描述游戏，由 Agent 做意图识别并抽取画像特征后入库再编排",
    )
    args = parser.parse_args()

    db_path = Path("circle_strategy.db")
    conn = connect(db_path)
    init_schema(conn)
    business_goal = "high_ltv"
    if args.from_description:
        extraction = extract_game_profile_from_description(args.from_description)
        insert_game(conn, extraction.profile)
        populate_players(conn, size=2400, seed=17)
        result = run_agentic_orchestrator(conn, extraction.profile["game_id"], business_goal=business_goal)
        result["extraction"] = {
            "intent": extraction.intent,
            "confidence": extraction.confidence,
            "method": extraction.method,
            "notes": extraction.notes,
            "profile": extraction.profile,
        }
    else:
        insert_game(
            conn,
            {
                "game_id": "G_SLG_001",
                "game_name": "IronEmpire",
                "genre": "SLG",
                "theme": "history",
                "art_style": "realism",
                "competition_level": "high",
                "org_dependency_level": "high",
                "identity_display_level": "high",
                "max_spend_level": "high",
                "depreciation_level": "slow",
                "update_mode": "season",
                "reputation_score": 4.15,
            },
        )
        populate_players(conn, size=2400, seed=17)
        result = run_agentic_orchestrator(conn, "G_SLG_001", business_goal=business_goal)
    metrics = evaluate_all(conn, result["sql_packages"])

    if "extraction" in result:
        ex = result["extraction"]
        print("=== 自然语言解析（意图+画像抽取）===")
        print(f"意图={ex['intent']} 置信度={ex['confidence']:.2f} 方式={ex['method']}")
        print(f"说明: {ex['notes']}")
        print(f"画像: {ex['profile']}\n")

    print("=== 需求向量 ===")
    for k, v in result["needs"].items():
        print(f"{k}: {v}")

    print("\n=== Agent执行轨迹 ===")
    for step in result["trace"]:
        print(step)

    print("\n=== 规则条目（前8条）===")
    for r in result["rules"][:8]:
        print(f"{r['tier']} | {r['need']} | {r['description']} | {r['evidence_source']} | conf={r['confidence']}")

    print("\n=== 证据摘要 ===")
    explain = result["explain"]
    print(
        "direct_rules={direct} proxy_rules={proxy} llm_rules={llm_rules} avg_confidence={conf} risk_flags={flags} llm_used={llm_used} llm_notes={notes}".format(
            direct=explain["direct_rules"],
            proxy=explain["proxy_rules"],
            llm_rules=explain["llm_rules"],
            conf=explain["avg_confidence"],
            flags=",".join(explain["risk_flags"]) if explain["risk_flags"] else "none",
            llm_used=result["llm_used"],
            notes=result["llm_notes"] or "none",
        )
    )

    print("\n=== 人群包效果 ===")
    for pkg, m in metrics.items():
        print(f"{pkg:20s} size={m.size:4d} install={m.install_rate:.3f} d7={m.d7_rate:.3f} pay={m.first_pay_rate:.3f} ltv30={m.ltv30_avg:.1f} hv={m.high_value_ratio:.3f}")

    print("\n=== Holdout评估与在线学习 ===")
    assigned = assign_campaign_with_holdout(
        conn,
        campaign_id="CMP_001",
        package_name="high_potential_expand",
        target_sql=result["sql_packages"]["high_potential_expand"],
        holdout_ratio=0.2,
        seed=17,
    )
    holdout_metrics = write_real_feedback_from_campaign(
        conn,
        campaign_id="CMP_001",
        package_name="high_potential_expand",
        business_goal=business_goal,
        needs=result["needs"],
        rules=result["rules"],
    )
    updated = update_weights_from_feedback(conn, business_goal=business_goal)
    print(
        "assigned={assigned} treat_pay={tp:.3f} holdout_pay={hp:.3f} pay_uplift={pu:+.3f} "
        "treat_ltv={tl:.1f} holdout_ltv={hl:.1f} ltv_uplift={lu:+.1f} updated_needs={updated}".format(
            assigned=assigned,
            tp=holdout_metrics.treatment_pay_rate,
            hp=holdout_metrics.holdout_pay_rate,
            pu=holdout_metrics.pay_uplift,
            tl=holdout_metrics.treatment_ltv30,
            hl=holdout_metrics.holdout_ltv30,
            lu=holdout_metrics.ltv_uplift,
            updated=updated,
        )
    )

    print("\n=== 三组对照实验导出 ===")
    groups = build_experiment_sql_groups(result["sql_packages"])
    exp_metrics, summary_path = export_experiment_outputs(conn, groups, Path("outputs"))
    for g, m in exp_metrics.items():
        print(f"{g:20s} size={m.size:4d} pay={m.first_pay_rate:.3f} ltv30={m.ltv30_avg:.1f} hv={m.high_value_ratio:.3f}")
    print(f"实验汇总文件: {summary_path}")


if __name__ == "__main__":
    main()

