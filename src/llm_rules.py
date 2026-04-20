from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List

from langchain_core.prompts import ChatPromptTemplate

try:
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover
    ChatOpenAI = None  # type: ignore
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

ALLOWED_COLUMNS = {
    "history_spend_level",
    "spend_potential_level",
    "genre_pref",
    "theme_pref",
    "active_time_slot",
    "community_role",
    "rating_sensitivity",
    "device_tier",
    "guild_page_views",
    "voice_minutes",
    "team_session_minutes",
    "session_fragments",
    "login_slot_entropy",
    "skin_preview_clicks",
    "screenshot_actions",
}
ALLOWED_TIERS = {"P0", "P1", "P2", "EXCLUSION"}
ALLOWED_OPERATORS = {"=", ">=", "<=", "in"}


@dataclass
class RuleRewriteResult:
    rewritten_rules: List[Dict[str, Any]]
    llm_used: bool
    notes: str


def _heuristic_new_rules(game_profile: Dict[str, Any], business_goal: str) -> List[Dict[str, Any]]:
    rules: List[Dict[str, Any]] = []
    if game_profile.get("genre") == "SLG":
        rules.append(
            {
                "rule_id": "llm_org_signal",
                "tier": "P1",
                "need": "collective_endorsement",
                "description": "组织协同代理信号增强",
                "sql_expr": "(guild_page_views >= 15 OR team_session_minutes >= 120)",
                "evidence_source": "llm_proxy",
                "confidence": 0.66,
            }
        )
    if business_goal == "high_d7":
        rules.append(
            {
                "rule_id": "llm_retention_time",
                "tier": "P1",
                "need": "collective_endorsement",
                "description": "优先稳定活跃时段人群",
                "sql_expr": "(active_time_slot IN ('evening','night'))",
                "evidence_source": "llm_direct",
                "confidence": 0.71,
            }
        )
    return rules


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[-1]
    return json.loads(text)


def _normalize_in_values(value: str) -> List[str]:
    raw = value.strip()
    if raw.startswith("[") and raw.endswith("]"):
        try:
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, (list, tuple)):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except (ValueError, SyntaxError):
            pass
    return [part.strip().strip("'\"") for part in raw.split(",") if part.strip().strip("'\"")]


def _normalize_rule(rule: Dict[str, Any]) -> Dict[str, Any] | None:
    if rule.get("target_column") not in ALLOWED_COLUMNS:
        return None
    if rule.get("tier") not in ALLOWED_TIERS:
        return None
    if rule.get("operator") not in ALLOWED_OPERATORS:
        return None
    value = str(rule.get("value", "")).strip()
    col = rule["target_column"]
    op = rule["operator"]
    if op == "in":
        values = _normalize_in_values(value)
        vals = ",".join(f"'{item}'" for item in values)
        expr = f"{col} IN ({vals})"
    else:
        expr = f"{col} {op} '{value}'"
    return {
        "rule_id": str(rule.get("rule_id", "llm_rule")),
        "tier": rule["tier"],
        "need": str(rule.get("need", "llm_discovered_need")),
        "description": str(rule.get("description", "LLM发现新规则")),
        "sql_expr": expr,
        "evidence_source": "llm_direct",
        "confidence": float(rule.get("confidence", 0.62)),
    }
def build_LLM_args(model_name="deepseek-chat", temperature=0.1):
    model_name = os.getenv("RULE_LLM_MODEL", model_name)
    llm_kwargs = {
        "model": model_name,
        "temperature": temperature,
        }

    api_key = os.getenv("OPENAI_API_KEY") 
    if api_key:
        llm_kwargs["api_key"] = api_key
    else:
        raise RuntimeError("未找到 OPENAI_API_KEY，无法初始化LLM客户端")
        
        
   
    base_url = os.getenv("OPENAI_BASE_URL") 
    if base_url:
        # 确保base_url格式正确（以/v1结尾）
        # DeepSeek: https://api.deepseek.com/v1
        # OpenAI: https://api.openai.com/v1
        if not base_url.endswith("/v1") and not base_url.endswith("/v1/"):
            if base_url.endswith("/"):
                base_url = base_url + "v1"
            else:
                base_url = base_url + "/v1"
        llm_kwargs["base_url"] = base_url
    
    llm = ChatOpenAI(**llm_kwargs)
    return llm

def rewrite_rules_with_llm(
    base_rules: List[Dict[str, Any]],
    game_profile: Dict[str, Any],
    needs: Dict[str, float],
    business_goal: str,
) -> RuleRewriteResult:
    
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or ChatOpenAI is None:
        merged = base_rules + _heuristic_new_rules(game_profile, business_goal)
        return RuleRewriteResult(merged, False, "未检测到LLM配置，使用启发式规则扩展")

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "你是游戏圈群规则专家。请基于输入重写规则并最多新增2条规则。"
                    "必须输出JSON对象：{{'new_rules':[...], 'notes':'...'}}。"
                    "new_rules每条必须包含 rule_id,tier,need,target_column,operator,value,description,confidence。"
                    "tier仅可为P0/P1/P2/EXCLUSION，operator仅可为=,>=,<=,in。"
                ),
            ),
            (
                "human",
                "game_profile={game_profile}\nneeds={needs}\nbusiness_goal={business_goal}\nbase_rules={base_rules}",
            ),
        ]
    )
    llm = build_LLM_args()
    #llm = ChatOpenAI(model=model_name, temperature=0.1, api_key=api_key)
    msg = prompt.format_messages(
        game_profile=json.dumps(game_profile, ensure_ascii=True),
        needs=json.dumps(needs, ensure_ascii=True),
        business_goal=business_goal,
        base_rules=json.dumps(base_rules[:8], ensure_ascii=True),
    )
    raw = llm.invoke(msg).content
    parsed = _extract_json(str(raw))
    normalized: List[Dict[str, Any]] = []
    for r in parsed.get("new_rules", [])[:2]:
        nr = _normalize_rule(r)
        if nr:
            normalized.append(nr)
    merged = base_rules + normalized
    return RuleRewriteResult(merged, True, str(parsed.get("notes", "")))

