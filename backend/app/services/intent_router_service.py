from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..config import get_settings

INTENT_ROUTER_SUGGESTIONS = [
    {"key": "ask_vpn", "label": "VPN 连不上", "enabled": True},
    {"key": "ask_account", "label": "账号登录问题", "enabled": True},
    {"key": "create_issue", "label": "提交在线记录", "enabled": True},
]

PROMPT_PATH = Path(__file__).resolve().parents[1] / "agents" / "intent_router" / "prompt.md"


@dataclass(frozen=True)
class IntentRoute:
    kind: str
    intent: str
    intent_label: str
    should_rag: bool
    should_handoff: bool
    answer: str = ""
    confidence: float = 1.0
    risk_level: str = "low"
    reason: str = ""
    source: str = "rules"
    evidence: dict[str, Any] = field(default_factory=dict)
    next_actions: list[dict[str, Any]] = field(default_factory=lambda: INTENT_ROUTER_SUGGESTIONS.copy())

    def to_metadata(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("answer", None)
        return data


class IntentRouterService:
    """Scope router inspired by Rasa fallback/out-of-scope and Haystack conditional routing.

    The router is intentionally deterministic by default. Optional LLM routing
    is advisory and only used for uncertain inputs when explicitly enabled.
    """

    GREETINGS = {
        "hi", "hello", "hey", "你好", "您好", "哈喽", "嗨", "在吗", "在不在",
        "早", "早上好", "下午好", "晚上好",
    }
    THANKS = {"谢谢", "感谢", "谢了", "多谢", "thanks", "thankyou", "thx"}
    GOODBYES = {"再见", "拜拜", "bye", "byebye", "回头见"}
    CAPABILITIES = {"你是谁", "你能做什么", "能做什么", "怎么用", "帮助", "help", "使用帮助", "云维能做什么"}
    VAGUE_INPUTS = {
        "测试", "test", "问一下", "咨询一下", "我想问一下", "帮我看看", "帮忙看看",
        "有问题", "出问题了", "不行", "不对", "怎么办", "报修", "我要报修",
        "好的", "好", "ok", "嗯", "哦", "打不开", "不能用", "坏了", "异常",
    }
    OPS_TERMS = {
        "vpn", "mfa", "2fa", "otp", "dns", "wifi", "wi-fi", "outlook", "office", "excel", "word", "teams",
        "remote", "remote access", "access", "certificate", "expired", "login", "password", "permission",
        "account", "network", "email", "printer", "database", "server", "error", "timeout", "failed", "failure", "locked",
        "账号", "账户", "密码", "登录", "登陆", "权限", "审批", "工单", "报修", "在线记录",
        "网络", "无线", "有线", "远程", "内网", "外网", "证书", "网关", "代理", "堡垒机",
        "邮箱", "邮件", "打印机", "打印", "共享盘", "共享文件夹", "网盘", "文件恢复",
        "电脑", "终端", "笔记本", "蓝屏", "键盘", "鼠标", "显示器", "投屏", "会议室",
        "软件", "软件中心", "客户端", "安装", "升级", "浏览器", "网页", "文件夹", "摄像头", "麦克风",
        "系统", "业务系统", "应用", "页面", "接口", "服务", "服务器", "虚拟机", "容器",
        "数据库", "mysql", "oracle", "redis", "sql", "中间件", "连接池",
        "报错", "错误", "失败", "超时", "白屏", "卡住", "闪退", "离线", "收不到", "连不上", "打不开",
        "生产", "发布", "部署", "变更", "回滚", "重启", "告警", "监控", "日志", "磁盘", "内存", "cpu",
        "知识库", "知识候选", "处理案例", "沉淀",
    }
    GENERIC_ONLY_TERMS = {"系统", "应用", "服务", "问题", "异常", "打不开", "不能用", "不行", "怎么办"}
    OUT_OF_SCOPE_TERMS = {
        "天气", "气温", "下雨", "股票", "基金", "理财", "彩票", "电影", "电视剧", "小说", "写诗",
        "诗歌", "笑话", "菜谱", "做饭", "旅游", "酒店", "机票", "星座", "算命", "恋爱", "相亲",
        "nba", "世界杯", "足球", "篮球", "游戏攻略", "高考", "作业", "论文润色", "翻译英文",
        "讲个故事", "生成图片", "画一张", "健身计划", "减肥", "医疗诊断",
    }
    CONTROLLED_TERMS = {
        "绕过审批", "跳过审批", "免审批", "破解", "撞库", "盗号", "提权", "越权", "root", "sudo",
        "管理员权限", "高权限", "删除数据", "清空数据", "批量删除", "生产变更", "重启数据库",
    }

    def __init__(self) -> None:
        self.prompt = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else ""

    def route(self, question: str) -> IntentRoute:
        text = (question or "").strip()
        normalized = self.normalize_short_text(text)
        if not normalized:
            return self._low_information("empty_input")

        deterministic = self._route_by_rules(text, normalized)
        if deterministic:
            return deterministic

        llm_route = self._route_by_llm(text)
        if llm_route:
            return llm_route

        return IntentRoute(
            kind="ops_support",
            intent="ops_issue",
            intent_label="运维咨询",
            should_rag=True,
            should_handoff=False,
            confidence=0.62,
            reason="default_to_ops_support_after_scope_check",
        )

    def normalize_short_text(self, value: str) -> str:
        return re.sub(r"[\s,，。.!！?？~～、:：；;]+", "", value or "").lower()

    def empty_issue_draft(self, question: str) -> dict[str, Any]:
        return {
            "title": question[:40] or "咨询云维",
            "description": question,
            "priority": "low",
            "category": "general",
            "impact_scope": "",
            "contact_phone": "",
            "attachment_url": "",
            "log_excerpt": "",
            "missing_fields": [],
            "confidence": 1.0,
            "extraction_source": "intent_router",
            "extraction_error": "",
            "llm_status": "",
        }

    def agent_result(self, route: IntentRoute) -> dict[str, Any]:
        prompt_path = str(PROMPT_PATH.relative_to(Path(__file__).resolve().parents[3])) if PROMPT_PATH.exists() else ""
        return {
            "mode": "intent_router",
            "agents": [
                {
                    "name": "intent_router",
                    "role": "Scope, intent, and fallback router",
                    "prompt_path": prompt_path,
                    "prompt_loaded": bool(self.prompt.strip()),
                    "tools": ["intent_route"],
                    "writes": False,
                }
            ],
            "trace": [
                {
                    "agent": "intent_router",
                    "phase": "Route",
                    "prompt_loaded": bool(self.prompt.strip()),
                    "prompt_path": prompt_path,
                    "tool": "intent_route",
                    "thought": "Route conversational, low-information, and out-of-scope input before RAG.",
                    "observation": route.to_metadata(),
                }
            ],
            "llm_reviews": {"enabled": False, "mode": "not_required"},
            "risk": {"level": route.risk_level},
            "evaluator": {"llm_allowed": False, "answer_ready": True},
            "intent_route": route.to_metadata(),
        }

    def _route_by_rules(self, text: str, normalized: str) -> IntentRoute | None:
        evidence = self._route_evidence(text)
        has_ops_signal = bool(evidence["ops_terms"]) or bool(evidence["technical_identifier"]) or bool(evidence["named_system"])
        has_specific_ops_signal = bool(evidence["specific_ops_terms"]) or bool(evidence["technical_identifier"]) or bool(evidence["named_system"])

        if normalized in self.GREETINGS:
            return IntentRoute(
                kind="greeting",
                intent="greeting",
                intent_label="日常咨询",
                should_rag=False,
                should_handoff=False,
                answer=(
                    "你好，我是云维。你可以直接告诉我遇到的系统、账号或报错现象，我会先给你自助建议；"
                    "需要人工处理时，我会帮你整理在线记录。"
                ),
                reason="exact_greeting",
                evidence=evidence,
            )
        if normalized in self.THANKS:
            return IntentRoute(
                kind="thanks",
                intent="thanks",
                intent_label="日常咨询",
                should_rag=False,
                should_handoff=False,
                answer="不客气。后续如果还有账号、网络、邮箱、业务系统等问题，直接告诉我现象和报错即可。",
                reason="exact_thanks",
                evidence=evidence,
            )
        if normalized in self.GOODBYES:
            return IntentRoute(
                kind="goodbye",
                intent="goodbye",
                intent_label="日常咨询",
                should_rag=False,
                should_handoff=False,
                answer="再见。需要查处理进度或继续咨询时，随时回来找我。",
                reason="exact_goodbye",
                evidence=evidence,
            )
        if normalized in self.CAPABILITIES:
            return IntentRoute(
                kind="capability",
                intent="capability",
                intent_label="日常咨询",
                should_rag=False,
                should_handoff=False,
                answer=(
                    "我可以帮你排查常见企业运维问题，比如 VPN、账号登录、MFA、邮箱、打印机、共享盘和业务系统报错；"
                    "如果自助处理不了，也可以帮你整理在线记录转给运维人员。"
                ),
                reason="capability_question",
                evidence=evidence,
            )

        if evidence["controlled_terms"]:
            return IntentRoute(
                kind="controlled_operation",
                intent="controlled_operation",
                intent_label="受控操作",
                should_rag=True,
                should_handoff=True,
                confidence=0.95,
                risk_level="high",
                reason="controlled_operation_signal",
                evidence=evidence,
            )

        if self._is_low_information(text, normalized, has_specific_ops_signal):
            return self._low_information("vague_or_too_short", evidence=evidence)

        if self._is_out_of_scope(text, has_ops_signal, evidence):
            return IntentRoute(
                kind="out_of_scope",
                intent="out_of_scope",
                intent_label="非运维问题",
                should_rag=False,
                should_handoff=False,
                answer=(
                    "我主要处理企业 IT 运维问题。这个问题看起来不属于账号、网络、邮箱、业务系统、终端设备、"
                    "权限审批或知识库维护范围，所以不会进入知识库检索。请改成具体运维现象后再发给我。"
                ),
                confidence=0.9,
                reason="outside_enterprise_ops_scope",
                evidence=evidence,
            )

        if has_ops_signal:
            return IntentRoute(
                kind="ops_support",
                intent="ops_issue",
                intent_label="运维咨询",
                should_rag=True,
                should_handoff=False,
                confidence=0.82 if has_specific_ops_signal else 0.68,
                reason="ops_scope_signal",
                evidence=evidence,
            )
        return None

    def _route_by_llm(self, text: str) -> IntentRoute | None:
        settings = get_settings()
        if not settings.enable_intent_router_llm or not self.prompt.strip():
            return None
        try:
            from .llm_service import llm_service

            result = llm_service.generate_agent_json(
                agent_name="intent_router",
                prompt=self.prompt,
                task="intent_routing",
                state={"question": text},
                schema_hint={
                    "kind": "ops_support|controlled_operation|low_information|out_of_scope|greeting|thanks|goodbye|capability",
                    "confidence": "number from 0 to 1",
                    "risk_level": "low|medium|high",
                    "reason": "short machine-readable reason",
                },
            )
        except Exception:
            return None
        parsed = result.get("parsed", {})
        if not isinstance(parsed, dict):
            return None
        confidence = self._clamp_float(parsed.get("confidence"), 0.0)
        if confidence < settings.intent_router_llm_min_confidence:
            return None
        kind = str(parsed.get("kind") or "").strip()
        reason = str(parsed.get("reason") or "llm_intent_router").strip()[:120]
        if kind == "out_of_scope":
            return IntentRoute(
                kind=kind,
                intent="out_of_scope",
                intent_label="非运维问题",
                should_rag=False,
                should_handoff=False,
                answer=(
                    "我主要处理企业 IT 运维问题。这个问题不属于当前运维支持范围，所以不会进入知识库检索。"
                    "请补充账号、网络、设备、系统报错或权限审批等具体运维信息。"
                ),
                confidence=confidence,
                reason=reason,
                source="llm",
            )
        if kind == "low_information":
            return self._low_information(reason, confidence=confidence, source="llm")
        if kind in {"greeting", "thanks", "goodbye", "capability"}:
            fallback = self._route_by_rules(text, self.normalize_short_text(text))
            if fallback and not fallback.should_rag:
                return IntentRoute(**{**asdict(fallback), "source": "llm", "confidence": confidence})
        if kind == "controlled_operation":
            return IntentRoute(
                kind=kind,
                intent="controlled_operation",
                intent_label="受控操作",
                should_rag=True,
                should_handoff=True,
                confidence=confidence,
                risk_level="high",
                reason=reason,
                source="llm",
            )
        if kind == "ops_support":
            return IntentRoute(
                kind=kind,
                intent="ops_issue",
                intent_label="运维咨询",
                should_rag=True,
                should_handoff=False,
                confidence=confidence,
                reason=reason,
                source="llm",
            )
        return None

    def _low_information(
        self,
        reason: str,
        confidence: float = 1.0,
        source: str = "rules",
        evidence: dict[str, Any] | None = None,
    ) -> IntentRoute:
        return IntentRoute(
            kind="low_information",
            intent="low_information",
            intent_label="信息不足",
            should_rag=False,
            should_handoff=False,
            answer="可以，我来帮你看。请补充一下具体问题：哪个系统或账号、看到什么报错、影响你一个人还是多人？",
            confidence=confidence,
            reason=reason,
            source=source,
            evidence=evidence or {},
        )

    def _has_ops_signal(self, text: str) -> bool:
        evidence = self._route_evidence(text)
        return bool(evidence["ops_terms"]) or bool(evidence["technical_identifier"]) or bool(evidence["named_system"])

    def _has_specific_ops_signal(self, text: str) -> bool:
        evidence = self._route_evidence(text)
        return bool(evidence["specific_ops_terms"]) or bool(evidence["technical_identifier"]) or bool(evidence["named_system"])

    def _has_controlled_signal(self, text: str) -> bool:
        return bool(self._route_evidence(text)["controlled_terms"])

    def _route_evidence(self, text: str) -> dict[str, Any]:
        lowered = text.lower()
        ops_terms = sorted({term for term in self.OPS_TERMS if term in lowered or term in text}, key=len, reverse=True)[:12]
        specific_ops_terms = [term for term in ops_terms if term not in self.GENERIC_ONLY_TERMS]
        out_of_scope_terms = sorted({term for term in self.OUT_OF_SCOPE_TERMS if term in lowered or term in text}, key=len, reverse=True)[:8]
        controlled_terms = sorted({term for term in self.CONTROLLED_TERMS if term in lowered or term in text}, key=len, reverse=True)[:8]
        technical_identifier = bool(re.search(r"[a-z0-9]+[_-][a-z0-9_-]+", lowered) or re.search(r"\b(?:err|error|e)[-_]?\d{2,6}\b", lowered))
        named_system = bool(
            re.search(r"\b(?:sap|erp|oa|crm|hr|sso|iam|itil|jira|gitlab|jenkins|k8s|kubernetes)\b", lowered)
            or re.search(r"[A-Za-z]{2,}(?:系统|页面|登录|登陆|报错|打不开|失败|超时)", text)
            or re.search(r"(?:财务|报销|采购|考勤|人事|客户|订单|库存|审批|门户)系统", text)
        )
        return {
            "controlled_terms": controlled_terms,
            "named_system": named_system,
            "ops_terms": ops_terms,
            "out_of_scope_terms": out_of_scope_terms,
            "specific_ops_terms": specific_ops_terms,
            "technical_identifier": technical_identifier,
        }

    def _is_low_information(self, text: str, normalized: str, has_specific_ops_signal: bool) -> bool:
        if normalized in self.VAGUE_INPUTS or len(normalized) <= 1:
            return True
        if has_specific_ops_signal:
            return False
        if len(normalized) <= 8 and any(term in normalized for term in self.GENERIC_ONLY_TERMS):
            return True
        vague_patterns = [
            r"^(帮我)?看(一下)?$",
            r"^(咨询|问)(一下)?$",
            r"^(有|出了?).{0,4}问题$",
            r"^(系统|应用|服务)?(不行|异常|坏了|不能用)$",
        ]
        return any(re.search(pattern, text.strip(), flags=re.I) for pattern in vague_patterns)

    def _is_out_of_scope(self, text: str, has_ops_signal: bool, evidence: dict[str, Any]) -> bool:
        if has_ops_signal:
            return False
        lowered = text.lower()
        if evidence["out_of_scope_terms"]:
            return True
        if re.search(r"\d+\s*[+\-*/÷]\s*\d+", text):
            return True
        if evidence["technical_identifier"]:
            return False
        if len(self.normalize_short_text(text)) >= 2:
            return True
        return False

    def _clamp_float(self, value: Any, fallback: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = fallback
        return max(0.0, min(1.0, number))


intent_router_service = IntentRouterService()
