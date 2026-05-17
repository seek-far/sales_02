from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .deepseek_client import DeepSeekClient
from .realtime_coach import AlertType, CoachAlert, MeetingState


LLM_COACH_SYSTEM_PROMPT = """\
你是实时 B2B 销售教练，正在旁听一场进行中的销售会谈。你的任务是根据最近一段逐字稿，判断销售是否需要即时提醒。

核心原则：
- 只基于逐字稿证据，不编造。
- 只在有明确信号时提醒，不确定则不提醒。
- 提醒必须具体、可执行，指出销售现在该说什么。
- 同类型提醒如果在最近 5 分钟内已经出现过，不要再重复。
- 提醒建议问题应自然、符合销售场景，不超过 40 个中文字。

你需要监控以下 5 种情况：

1. missing_discovery — 客户表达了痛点或问题（痛/慢/延期/麻烦/不一致/担心/出错），但销售没有追问业务影响或量化后果。此时提醒追问 impact。
2. qualification_gap — 会谈已进行超过 10 分钟，且客户提到了推进信号（试点/方案/采购/推进/上线/合同/预算/报价），但依然缺少关键资格信息（预算/决策人/决策流程/时间线/决策标准 中任一项）。
3. objection_unhandled — 客户明显表达了顾虑或异议（担心/质疑/怕/安全/出境/准确率/太贵/抵触/不用/太长），应立即提醒销售先确认再回应，不要直接解释。
4. next_step_due — 会谈已超过计划时长的 60%，但尚未明确下一步行动（没有约时间/责任人/交付物）。提醒收口。
5. talk_ratio_or_monologue — 销售方连续发言过多（累计超过 500 字无明显客户回应），提醒暂停确认。

输出严格 JSON，不要 Markdown，不要解释性文字：
- 如果不提醒：{"alert": null}
- 如果提醒：{"alert": {"priority": "high/medium/low", "type": "...", "message": "...", "reason": "...", "suggested_question": "..."}}
""".strip()

LLM_COACH_USER_TEMPLATE = """\
会谈已进行 {elapsed_minutes} 分钟，计划总时长 {total_minutes} 分钟。

目前已确认的信息：{confirmed_summary}

最近 {recent_alerts_count} 次提醒类型：{recent_alert_types}

=== 最近新增逐字稿 ===
{new_text}
=== 结束 ===

请判断此时是否需要提醒销售。"""


@dataclass
class LLMCoachState:
    """Lightweight state for the LLM-based coach."""
    confirmed: set[str] = field(default_factory=set)
    recent_alert_types: list[str] = field(default_factory=list)
    max_recent_alerts: int = 5
    cooldown_minutes: int = 5
    last_alert_minute: int = -999


class LLMRealtimeCoach:
    """LLM-powered realtime sales coach.

    Replaces keyword matching with DeepSeek API calls for higher-quality,
    context-aware coaching alerts. Maintains the same ``evaluate()``
    interface as ``RealtimeSalesCoach`` so it drops into existing runners.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        meeting_duration_minutes: int = 90,
        cooldown_minutes: int = 5,
        temperature: float = 0.0,
        verbose: bool = False,
        system_prompt: str | None = None,
    ):
        self._client = DeepSeekClient(settings)
        self.meeting_duration_minutes = meeting_duration_minutes
        self._cooldown_minutes = cooldown_minutes
        self._temperature = temperature
        self._verbose = verbose
        self._system_prompt = system_prompt or LLM_COACH_SYSTEM_PROMPT
        self._state = LLMCoachState(cooldown_minutes=cooldown_minutes)
        self.last_error: str | None = None

    def evaluate(
        self, state: MeetingState, new_text: str, elapsed_minutes: int
    ) -> CoachAlert | None:
        """Evaluate the recent transcript window and return a coaching alert if warranted."""
        normalized = new_text.strip()
        if not normalized:
            return None

        # Cooldown check — don't call LLM if we're still within the cooldown window
        if elapsed_minutes - self._state.last_alert_minute < self._cooldown_minutes:
            return None

        self._update_confirmed(state, normalized)

        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": LLM_COACH_USER_TEMPLATE.format(
                    elapsed_minutes=elapsed_minutes,
                    total_minutes=self.meeting_duration_minutes,
                    confirmed_summary=self._confirmed_summary(),
                    recent_alerts_count=len(self._state.recent_alert_types),
                    recent_alert_types=", ".join(self._state.recent_alert_types)
                    if self._state.recent_alert_types
                    else "无",
                    new_text=new_text[-6000:],  # limit context size
                ),
            },
        ]

        try:
            self.last_error = None
            result = self._client.complete_json(messages)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            if self._verbose:
                print(
                    f"[LLM t={elapsed_minutes:2d}m] \u2717 API error: {type(exc).__name__}: {exc}",
                    flush=True,
                )
            return None

        alert_payload = result.get("alert") if isinstance(result, dict) else None

        if self._verbose:
            if isinstance(alert_payload, dict):
                alert_type = str(alert_payload.get("type", "?"))
                alert_msg = str(alert_payload.get("message", ""))
                print(
                    f"[LLM t={elapsed_minutes:2d}m] \u26a0 {alert_type} \u2014 {alert_msg}",
                    flush=True,
                )
            else:
                print(f"[LLM t={elapsed_minutes:2d}m] \u2014", flush=True)

        if not isinstance(alert_payload, dict):
            return None

        alert_type = str(alert_payload.get("type", ""))
        if alert_type not in _VALID_ALERT_TYPES:
            return None

        self._state.last_alert_minute = elapsed_minutes
        self._state.recent_alert_types.append(alert_type)
        if len(self._state.recent_alert_types) > self._state.max_recent_alerts:
            self._state.recent_alert_types.pop(0)

        return CoachAlert(
            priority=_normalize_priority(str(alert_payload.get("priority", "medium"))),
            type=alert_type,  # type: ignore[arg-type]
            message=str(alert_payload.get("message", "")),
            reason=str(alert_payload.get("reason", "")),
            suggested_question=str(alert_payload.get("suggested_question", "")),
        )

    def _update_confirmed(self, state: MeetingState, text: str) -> None:
        """Mirror the keyword-based confirmed-field tracking for context."""
        checks: dict[str, list[str]] = {
            "impact": ["影响", "延期", "金额", "周期", "准确率", "赢单率"],
            "budget": ["预算", "报价", "价格", "审批门槛", "费用"],
            "authority": ["拍板", "决策人", "总裁", "CFO", "sponsor"],
            "decision_process": ["流程", "法务", "评审", "采购", "合同"],
            "decision_criteria": ["标准", "安全", "集成", "实施负担"],
            "timeline": ["时间线", "月底", "下周", "最晚"],
            "next_step": ["下一步", "下次", "约", "日程", "会后"],
        }
        for field_name, keywords in checks.items():
            if any(keyword in text for keyword in keywords):
                self._state.confirmed.add(field_name)

    def _confirmed_summary(self) -> str:
        if not self._state.confirmed:
            return "尚未确认关键信息"
        return "已确认：" + "、".join(sorted(self._state.confirmed))


_VALID_ALERT_TYPES: set[str] = {
    "missing_discovery",
    "qualification_gap",
    "objection_unhandled",
    "next_step_due",
    "talk_ratio_or_monologue",
}

_VALID_PRIORITIES: set[str] = {"low", "medium", "high"}


def _normalize_priority(raw: str) -> str:
    cleaned = raw.strip().lower()
    if cleaned in _VALID_PRIORITIES:
        return cleaned
    return "medium"
