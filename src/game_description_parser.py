from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Set

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate

try:
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover
    ChatOpenAI = None  # type: ignore

from .llm_rules import _extract_json, build_LLM_args

load_dotenv(dotenv_path=".env")

ALLOWED_GENRES: Set[str] = {"SLG", "RPG", "casual", "action"}
ALLOWED_THEMES: Set[str] = {"history", "war", "fantasy", "anime"}
ALLOWED_ART_STYLES: Set[str] = {"realism", "anime"}
ALLOWED_LEVELS: Set[str] = {"low", "medium", "high"}
ALLOWED_DEPRECIATION: Set[str] = {"slow", "medium", "fast"}
ALLOWED_UPDATE_MODES: Set[str] = {"season", "rolling", "continuous"}

INTENTS = {"extract_game_profile", "needs_clarification", "not_game_related"}


@dataclass
class GameDescriptionExtraction:
    """从自然语言描述中解析出的游戏画像与意图。"""

    intent: str
    confidence: float
    profile: Dict[str, Any]
    notes: str
    method: str


def _clamp_choice(value: Any, allowed: Set[str], default: str) -> str:
    if value is None:
        return default
    s = str(value).strip()
    if s in allowed:
        return s
    lower = {a.lower(): a for a in allowed}
    if s.lower() in lower:
        return lower[s.lower()]
    return default


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, x))


def _normalize_profile(
    raw: Dict[str, Any],
    game_id: str,
    game_name_fallback: str,
) -> Dict[str, Any]:
    return {
        "game_id": str(raw.get("game_id") or game_id),
        "game_name": str(raw.get("game_name") or game_name_fallback)[:128],
        "genre": _clamp_choice(raw.get("genre"), ALLOWED_GENRES, "casual"),
        "theme": _clamp_choice(raw.get("theme"), ALLOWED_THEMES, "fantasy"),
        "art_style": _clamp_choice(raw.get("art_style"), ALLOWED_ART_STYLES, "realism"),
        "competition_level": _clamp_choice(raw.get("competition_level"), ALLOWED_LEVELS, "medium"),
        "org_dependency_level": _clamp_choice(raw.get("org_dependency_level"), ALLOWED_LEVELS, "medium"),
        "identity_display_level": _clamp_choice(raw.get("identity_display_level"), ALLOWED_LEVELS, "medium"),
        "max_spend_level": _clamp_choice(raw.get("max_spend_level"), ALLOWED_LEVELS, "medium"),
        "depreciation_level": _clamp_choice(raw.get("depreciation_level"), ALLOWED_DEPRECIATION, "medium"),
        "update_mode": _clamp_choice(raw.get("update_mode"), ALLOWED_UPDATE_MODES, "season"),
        "reputation_score": round(_clamp_float(raw.get("reputation_score"), 1.0, 5.0, 4.0), 2),
    }


def _stable_game_id(description: str) -> str:
    h = hashlib.sha256(description.strip().encode("utf-8")).hexdigest()[:12]
    return f"G_NL_{h}"


def _infer_heuristic(description: str) -> Dict[str, Any]:
    text = description.lower()
    # 中英混用：同时匹配常见中文
    genre = "casual"
    if any(k in text for k in ("slg", "策略", "沙盘", "国战", "4x", "strategy")):
        genre = "SLG"
    elif any(k in text for k in ("rpg", "角色扮演", "arpg", "mmorpg")):
        genre = "RPG"
    elif any(k in text for k in ("action", "动作", "格斗", "射击", "fps")):
        genre = "action"

    theme = "fantasy"
    if any(k in text for k in ("历史", "三国", "古代", "王朝", "history")):
        theme = "history"
    elif any(k in text for k in ("战争", "军事", "war", "战场")):
        theme = "war"
    elif any(k in text for k in ("二次元", "动漫", "日系", "番", "anime")):
        theme = "anime"

    art_style = "realism"
    if any(k in text for k in ("二次元", "动漫", "日系", "番", "anime", "卡通")):
        art_style = "anime"

    def level_high_if(
        high_keys: Iterable[str], med_keys: Iterable[str], default: str = "medium"
    ) -> str:
        if any(k in text for k in high_keys):
            return "high"
        if any(k in text for k in med_keys):
            return "medium"
        return default

    competition_level = level_high_if(
        ("竞技", "对抗", "pvp", "强竞争", "排行榜", "赛季", "high competition"),
        ("竞技", "排名"),
    )
    org_dependency_level = level_high_if(
        ("公会", "联盟", "组织", "帮派", "guild", "联盟战", "同盟", "国战"),
        ("协作", "组队", "team"),
    )
    identity_display_level = level_high_if(
        ("皮肤", "外观", "时装", "展示", "幻化", "avatar", "skin"),
        ("装扮", "形象"),
    )
    max_spend_level = level_high_if(
        ("重氪", "氪金", "付费", "抽卡", "gacha", "whale", "大r"),
        ("付费", "月卡", "pay"),
    )

    depreciation_level = "medium"
    if any(k in text for k in ("保值", "长期养成", "资产", "slow depreciation", "慢贬值")):
        depreciation_level = "slow"
    elif any(k in text for k in ("快消", "快迭代", "快贬值")):
        depreciation_level = "fast"

    update_mode = "season"
    if any(k in text for k in ("赛季", "赛季制", "season")):
        update_mode = "season"
    elif any(k in text for k in ("持续更新", "周更", "rolling")):
        update_mode = "rolling"
    elif any(k in text for k in ("常驻", "continuous", "长线")):
        update_mode = "continuous"

    reputation_score = 4.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:分|星|/5)", description)
    if m:
        reputation_score = _clamp_float(m.group(1), 1.0, 5.0, 4.0)
    elif any(k in text for k in ("口碑极佳", "大作", "3a", "高分")):
        reputation_score = 4.5
    elif any(k in text for k in ("差评", "口碑差", "低分")):
        reputation_score = 3.2

    return {
        "genre": genre,
        "theme": theme,
        "art_style": art_style,
        "competition_level": competition_level,
        "org_dependency_level": org_dependency_level,
        "identity_display_level": identity_display_level,
        "max_spend_level": max_spend_level,
        "depreciation_level": depreciation_level,
        "update_mode": update_mode,
        "reputation_score": reputation_score,
    }


def classify_intent_heuristic(description: str) -> tuple[str, float]:
    """轻量意图：判断是否为游戏画像相关描述。"""
    text = description.strip()
    if len(text) < 4:
        return "needs_clarification", 0.35
    off_topic = any(
        k in text.lower()
        for k in (
            "今天天气",
            "hello world",
            "写个排序算法",
            "python tutorial",
        )
    )
    if off_topic:
        return "not_game_related", 0.55
    game_hints = any(
        k in text
        for k in (
            "游戏",
            "手游",
            "玩家",
            "赛季",
            "公会",
            "氪",
            "抽卡",
            "slg",
            "rpg",
            "game",
            "mobile",
        )
    ) or any(k in text.lower() for k in ("slg", "rpg", "mmorpg", "gacha", "pvp"))
    if game_hints:
        return "extract_game_profile", 0.78
    return "extract_game_profile", 0.55


def llm_extract_game_profile(
    description: str,
    game_id: str,
    game_name_fallback: str,
) -> tuple[Dict[str, Any], str, str]:
    """调用 LLM 做意图识别与结构化字段抽取。返回 (payload, intent, notes)。"""
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "你是游戏行业结构化信息抽取助手。根据用户描述判断意图并抽取游戏画像字段。"
                    "意图 intent 必须是以下之一：extract_game_profile（与游戏/产品相关）、"
                    "needs_clarification（信息过少）、not_game_related（与游戏无关）。"
                    "必须输出一个 JSON 对象，键为："
                    "intent, confidence, game_name, genre, theme, art_style, "
                    "competition_level, org_dependency_level, identity_display_level, "
                    "max_spend_level, depreciation_level, update_mode, reputation_score, notes。"
                    "genre 仅可为 SLG,RPG,casual,action；theme 仅可为 history,war,fantasy,anime；"
                    "art_style 仅可为 realism,anime；"
                    "competition_level/org_dependency_level/identity_display_level/max_spend_level "
                    "仅可为 low,medium,high；depreciation_level 可为 slow,medium,fast；"
                    "update_mode 可为 season,rolling,continuous；reputation_score 为 1-5 的浮点数。"
                ),
            ),
            (
                "human",
                "game_id={game_id}\ndescription={description}",
            ),
        ]
    )
    llm = build_LLM_args(temperature=0.0)
    msg = prompt.format_messages(game_id=game_id, description=description)
    raw = llm.invoke(msg).content
    parsed = _extract_json(str(raw))
    intent = str(parsed.get("intent", "extract_game_profile"))
    if intent not in INTENTS:
        intent = "extract_game_profile"
    notes = str(parsed.get("notes", ""))
    return parsed, intent, notes


def extract_game_profile_from_description(
    description: str,
    *,
    game_id: Optional[str] = None,
    game_name: Optional[str] = None,
) -> GameDescriptionExtraction:
    """
    从用户自然语言描述中解析游戏特征（意图识别 + 字段对齐 games 表）。

    优先使用 LLM（需 OPENAI_API_KEY）；否则使用启发式规则。
    """
    desc = description.strip()
    if not desc:
        raise ValueError("游戏描述不能为空")

    gid = game_id or _stable_game_id(desc)
    name_fallback = game_name or (desc[:24] + ("…" if len(desc) > 24 else "")) or "未命名游戏"

    api_key = os.getenv("OPENAI_API_KEY", "")
    if api_key and ChatOpenAI is not None:
        try:
            parsed, intent, notes = llm_extract_game_profile(desc, gid, name_fallback)
            confidence = _clamp_float(parsed.get("confidence"), 0.0, 1.0, 0.7)
            merged = {**_infer_heuristic(desc), **{k: v for k, v in parsed.items() if v is not None}}
            merged["game_id"] = gid
            merged["game_name"] = parsed.get("game_name") or name_fallback
            profile = _normalize_profile(merged, gid, name_fallback)
            return GameDescriptionExtraction(
                intent=intent,
                confidence=confidence,
                profile=profile,
                notes=notes,
                method="llm",
            )
        except Exception:  # pragma: no cover - 网络/模型失败时回退
            pass

    intent, ic = classify_intent_heuristic(desc)
    heuristic_fields = _infer_heuristic(desc)
    merged = {
        "game_id": gid,
        "game_name": game_name or name_fallback,
        **heuristic_fields,
    }
    profile = _normalize_profile(merged, gid, name_fallback)
    notes = "未检测到可用 LLM 或调用失败，已使用启发式意图与特征抽取"
    return GameDescriptionExtraction(
        intent=intent,
        confidence=ic,
        profile=profile,
        notes=notes,
        method="heuristic",
    )
