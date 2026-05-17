from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


AlertType = Literal[
    "missing_discovery",
    "qualification_gap",
    "objection_unhandled",
    "next_step_due",
    "talk_ratio_or_monologue",
]


@dataclass(frozen=True)
class CoachAlert:
    priority: Literal["low", "medium", "high"]
    type: AlertType
    message: str
    reason: str
    suggested_question: str


@dataclass
class MeetingState:
    transcript: str = ""
    first_seen: datetime | None = None
    last_alert_by_type: dict[str, int] = field(default_factory=dict)
    confirmed: set[str] = field(default_factory=set)
    recent_alerts: list[CoachAlert] = field(default_factory=list)


class RealtimeSalesCoach:
    """Low-noise realtime sales coach based on the agreed MVP trigger set."""

    def __init__(self, *, meeting_duration_minutes: int = 90, min_minutes_between_same_type: int = 5):
        self.meeting_duration_minutes = meeting_duration_minutes
        self.min_minutes_between_same_type = min_minutes_between_same_type

    def evaluate(self, state: MeetingState, new_text: str, elapsed_minutes: int) -> CoachAlert | None:
        normalized = new_text.strip()
        if not normalized:
            return None

        state.transcript = append_transcript(state.transcript, normalized)
        update_confirmed_fields(state, normalized)

        candidates = [
            self._objection_unhandled(state, normalized, elapsed_minutes),
            self._missing_discovery(state, normalized, elapsed_minutes),
            self._qualification_gap(state, normalized, elapsed_minutes),
            self._next_step_due(state, elapsed_minutes),
            self._talk_ratio_or_monologue(normalized, elapsed_minutes),
        ]
        for alert in candidates:
            if alert and self._can_emit(state, alert.type, elapsed_minutes):
                state.last_alert_by_type[alert.type] = elapsed_minutes
                state.recent_alerts.append(alert)
                return alert
        return None

    def _can_emit(self, state: MeetingState, alert_type: str, elapsed_minutes: int) -> bool:
        last = state.last_alert_by_type.get(alert_type)
        return last is None or elapsed_minutes - last >= self.min_minutes_between_same_type

    def _missing_discovery(self, state: MeetingState, text: str, elapsed_minutes: int) -> CoachAlert | None:
        if elapsed_minutes < 5:
            return None
        if has_any(text, ["痛", "慢", "延期", "麻烦", "问题", "不系统", "不一致", "担心"]) and "impact" not in state.confirmed:
            return CoachAlert(
                priority="high",
                type="missing_discovery",
                message="客户已经表达了问题，可以马上追问业务影响，别急着讲功能。",
                reason="近期转写出现痛点信号，但尚未确认量化影响。",
                suggested_question="这个问题现在对预测准确率、赢单率或团队时间大概造成了多大影响？",
            )
        return None

    def _qualification_gap(self, state: MeetingState, text: str, elapsed_minutes: int) -> CoachAlert | None:
        if elapsed_minutes < 15:
            return None
        if has_any(text, ["试点", "方案", "采购", "推进", "上线", "合同"]):
            for field_name, question in [
                ("budget", "这类试点通常在什么预算范围内更容易推进？有没有审批门槛？"),
                ("authority", "除了您和周经理，还有谁会参与最终拍板或强影响这个决策？"),
                ("decision_process", "如果下周样本验证通过，后面从评审到合同大概要经过哪些步骤？"),
                ("timeline", "如果要赶上本季度或训练营，最晚什么时候需要确定？"),
            ]:
                if field_name not in state.confirmed:
                    return CoachAlert(
                        priority="high",
                        type="qualification_gap",
                        message="客户已有推进语境，但关键资格信息还没补齐。",
                        reason=f"当前还缺少 {field_name} 信息。",
                        suggested_question=question,
                    )
        return None

    def _objection_unhandled(self, _state: MeetingState, text: str, elapsed_minutes: int) -> CoachAlert | None:
        if elapsed_minutes < 3:
            return None
        if has_any(text, ["担心", "质疑", "怕", "安全", "出境", "准确率", "太长", "不用", "抵触", "贵"]):
            return CoachAlert(
                priority="medium",
                type="objection_unhandled",
                message="客户刚提出顾虑，建议先复述确认，再给处理路径。",
                reason="近期转写出现明显异议信号。",
                suggested_question="我确认一下，您最担心的是准确率本身，还是主管和销售是否愿意采纳？",
            )
        return None

    def _next_step_due(self, state: MeetingState, elapsed_minutes: int) -> CoachAlert | None:
        if elapsed_minutes < max(20, int(self.meeting_duration_minutes * 0.7)):
            return None
        if "next_step" in state.confirmed:
            return None
        return CoachAlert(
            priority="high",
            type="next_step_due",
            message="会议已进入后段，还没有明确下一步，建议现在收口。",
            reason="已超过计划时长的 70%，但未检测到明确 next step。",
            suggested_question="为了让这件事往前走，我们下次是否约一个样本评审会？谁需要一起参加，定在什么时候？",
        )

    def _talk_ratio_or_monologue(self, text: str, elapsed_minutes: int) -> CoachAlert | None:
        if elapsed_minutes < 8:
            return None
        sales_markers = text.count("销售") + text.count("李明")
        customer_markers = text.count("客户") + text.count("王总") + text.count("周经理")
        if sales_markers >= 4 and customer_markers == 0:
            return CoachAlert(
                priority="low",
                type="talk_ratio_or_monologue",
                message="销售连续讲得较多，可以停下来让客户确认。",
                reason="近期窗口里销售侧发言明显多于客户侧。",
                suggested_question="我先停一下，这部分和您现在的流程匹配吗？有没有哪里我理解偏了？",
            )
        return None


def append_transcript(current: str, new_text: str) -> str:
    return f"{current}\n{new_text}".strip() if current else new_text


def update_confirmed_fields(state: MeetingState, text: str) -> None:
    checks = {
        "impact": ["影响", "延期", "金额", "周期", "准确率", "赢单率", "1800", "缩到"],
        "budget": ["预算", "报价", "价格", "20万", "审批门槛", "费用"],
        "authority": ["拍板", "决策人", "总裁", "CFO", "王总", "sponsor"],
        "decision_process": ["流程", "法务", "IT", "评审", "采购", "合同"],
        "decision_criteria": ["标准", "准确率", "主管", "安全", "集成", "实施负担"],
        "timeline": ["什么时候", "时间线", "月底", "下周", "训练营", "最晚"],
        "next_step": ["下一步", "下次", "约", "日程", "会后", "明天", "下周二"],
    }
    for field_name, keywords in checks.items():
        if has_any(text, keywords):
            state.confirmed.add(field_name)


def has_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)
