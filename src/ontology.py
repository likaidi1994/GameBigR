from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class Availability:
    status: str
    proxy_columns: List[str]
    confidence: float


@dataclass(frozen=True)
class RuleDefinition:
    rule_id: str
    tier: str
    target_column: str
    operator: str
    value: str
    requirement: str
    description: str


PLAYER_AVAILABILITY: Dict[str, Availability] = {
    "history_spend_level": Availability("available", [], 0.95),
    "spend_potential_level": Availability("available", [], 0.92),
    "genre_pref": Availability("available", [], 0.90),
    "theme_pref": Availability("available", [], 0.90),
    "active_time_slot": Availability("available", [], 0.88),
    "community_role": Availability("partial", ["guild_page_views", "team_session_minutes"], 0.72),
    "org_engagement": Availability("missing", ["guild_page_views", "voice_minutes", "team_session_minutes"], 0.65),
    "busy_level": Availability("missing", ["session_fragments", "login_slot_entropy"], 0.63),
    "visual_validation": Availability("missing", ["skin_preview_clicks", "screenshot_actions"], 0.60),
    "rating_sensitivity": Availability("available", [], 0.86),
    "device_tier": Availability("available", [], 0.93),
}


GAME_TO_NEED_RULES: Dict[str, Dict[str, float]] = {
    "high_competition": {"status_control": 0.35, "tension_drive": 0.25},
    "high_identity_display": {"identity_isolation": 0.30, "status_control": 0.15},
    "high_org_dependency": {"collective_endorsement": 0.30, "benefit_distribution": 0.25},
    "high_max_spend": {"status_control": 0.25, "long_term_value": 0.20},
    "slow_depreciation": {"long_term_value": 0.30, "honor_certification": 0.20},
    "seasonal_update": {"honor_certification": 0.25, "long_term_value": 0.15},
    "anime_style": {"destiny_attachment": 0.35, "audio_visual_soothe": 0.30},
    "high_visual_quality": {"audio_visual_soothe": 0.30, "surprise_feedback": 0.15},
    "fast_feedback": {"surprise_feedback": 0.25, "tension_drive": 0.20},
}

#TODO: 规则模板 当前只是简单的规则模板，未来需要根据业务目标进行实际的调整
NEED_TO_RULE_TEMPLATES: Dict[str, List[RuleDefinition]] = {
    "status_control": [
        RuleDefinition("r_spend_hist", "P0", "history_spend_level", ">=", "medium", "status_control", "历史投入中高"),
        RuleDefinition("r_spend_potential", "P0", "spend_potential_level", ">=", "strong", "status_control", "消费潜力高"),
        RuleDefinition("r_comp_pref", "P1", "genre_pref", "in", "SLG,Strategy", "status_control", "偏好策略竞争"),
    ],
    "identity_isolation": [
        RuleDefinition("r_community", "P1", "community_role", "in", "leader,active_fan", "identity_isolation", "社区身份高"),
    ],
    "benefit_distribution": [
        RuleDefinition("r_org_engagement", "P1", "org_engagement", ">=", "medium", "benefit_distribution", "组织参与倾向"),
    ],
    "collective_endorsement": [
        RuleDefinition("r_time_match", "P0", "active_time_slot", "=", "evening", "collective_endorsement", "晚间可参与组织玩法"),
    ],
    "long_term_value": [
        RuleDefinition("r_rating_guard", "EXCLUSION", "rating_sensitivity", "guardrail", "high_if_low_reputation", "long_term_value", "评分敏感高且口碑低排除"),
    ],
    "audio_visual_soothe": [
        RuleDefinition("r_theme_pref", "P0", "theme_pref", "in", "history,war,fantasy,anime", "audio_visual_soothe", "题材偏好匹配"),
        RuleDefinition("r_visual_proxy", "P2", "visual_validation", ">=", "low", "audio_visual_soothe", "视觉验证代理特征"),
    ],
    "tension_drive": [
        RuleDefinition("r_device", "P0", "device_tier", ">=", "mid", "tension_drive", "设备性能达标"),
    ],
}

