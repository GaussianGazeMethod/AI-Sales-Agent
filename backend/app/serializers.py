"""输出序列化：把 ORM 对象转成前端契约里的 JSON 结构。

刻意不用 response_model —— 字段较多且有派生值（停留天数、加权金额、是否超期），
集中在这里构造更容易与前端契约保持一致。
"""

from __future__ import annotations

from datetime import date, datetime

from app.core import dicts
from app.core.dicts import mask_phone
from app.core.sales_rules import sales_stage_days
from app.models.activity import Assignment, FollowUp, MarketTarget, Reminder
from app.models.ai import AiDiagnosis, AiDraft
from app.models.lead import Contact, Lead
from app.models.stage import (
    AmountChange,
    RiskAlert,
    StageCompletion,
    StageStandard,
    StageTransition,
)
from app.models.system import ImportBatch, Notification, OperationLog
from app.models.user import User


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def user_out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "role": user.role,
        "role_label": dicts.label_of(dicts.ROLE_LABELS, user.role),
        "data_scope": user.data_scope,
        "department": user.department,
        "phone": user.phone,
        "email": user.email,
        "is_active": user.is_active,
        "created_at": _iso(user.created_at),
    }


async def leads_with_metadata(db, leads):
    """列表、工作台和详情共用下一节点投影，不从旧日期字段推断节奏。"""
    from app.services import dossier_service

    metadata = await dossier_service.list_metadata(db, leads)
    return [{**lead_out(lead), **metadata[lead.id]} for lead in leads]


def lead_out(lead: Lead, *, reveal_phone: bool = False, today: date | None = None) -> dict:
    """线索/商机主表输出。"""
    today = today or date.today()
    stay_days = sales_stage_days(lead, today=today)
    stage_base = dicts.duration_of(lead.current_stage)
    return {
        "id": lead.id,
        # 客户-项目两层：项目名 + 所属客户（设计稿 04 标题「客户 / 项目名」）
        "project_name": lead.project_name or "",
        "customer_id": lead.customer_id,
        "customer_name": lead.name,
        "name": lead.name,
        "source": lead.source,
        "source_label": dicts.label_of(dicts.LEAD_SOURCE_LABELS, lead.source),
        "province": lead.province,
        "city": lead.city,
        "district": lead.district,
        "org_type": lead.org_type,
        "org_type_label": dicts.label_of(dicts.ORG_TYPE_LABELS, lead.org_type),
        "status": lead.status,
        "status_label": dicts.label_of(dicts.LEAD_STATUS_LABELS, lead.status),
        "owner_id": lead.owner_id,
        "owner_name": lead.owner_name,
        "creator_id": lead.creator_id,
        "creator_name": lead.creator_name,
        "parent_id": lead.parent_id,
        "is_suspect_duplicate": lead.is_suspect_duplicate,
        "duplicate_hint": lead.duplicate_hint,
        "dedup_status": lead.dedup_status,
        "is_deleted": lead.is_deleted,
        "budget_amount_fen": lead.budget_amount_fen,
        "contract_amount_fen": lead.contract_amount_fen,
        "paid_amount_fen": lead.paid_amount_fen,
        "unpaid_amount_fen": lead.unpaid_amount_fen,
        "expected_sign_date": _iso(lead.expected_sign_date),
        # 下一节点摘要由统一档案投影提供，不用旧日期字段推断。
        "current_stage": lead.current_stage,
        "current_stage_label": dicts.label_of(dicts.STAGE_LABELS, lead.current_stage),
        "stage_entered_at": _iso(lead.stage_entered_at),
        "stage_stay_days": stay_days,
        "stage_base_days": stage_base,
        "stage_overdue": bool(
            lead.status != "signed" and stage_base and stay_days is not None
            and stay_days > stage_base
        ),
        "win_probability": lead.win_probability,
        "weighted_amount_fen": lead.weighted_amount_fen,
        "risk_types": lead.risk_types or [],
        "risk_labels": [dicts.label_of(dicts.RISK_TYPE_LABELS, r) for r in (lead.risk_types or [])],
        "lost_reason_types": lead.lost_reason_types or [],
        "lost_reason_labels": [
            dicts.label_of(dicts.RISK_TYPE_LABELS, r) for r in (lead.lost_reason_types or [])
        ],
        "lost_at": _iso(lead.lost_at),
        "lost_remark": lead.lost_remark,
        "assign_at": _iso(lead.assign_at),
        "first_follow_at": _iso(lead.first_follow_at),
        "last_follow_at": _iso(lead.last_follow_at),
        "valid_at": _iso(lead.valid_at),
        "sign_date": _iso(lead.sign_date),
        "first_follow_deadline": _iso(lead.first_follow_deadline),
        "recycle_deadline": _iso(lead.recycle_deadline),
        "deliver_handoff_status": lead.deliver_handoff_status,
        "remark": lead.remark,
        "channel_unit": lead.channel_unit,
        "referrer": lead.referrer,
        "created_at": _iso(lead.created_at),
        "updated_at": _iso(lead.updated_at),
    }


def contact_out(contact: Contact, *, reveal_phone: bool = False) -> dict:
    return {
        "id": contact.id,
        "lead_id": contact.lead_id,
        "name": contact.name,
        "position": contact.position,
        "phone": contact.phone if reveal_phone else mask_phone(contact.phone),
        "phone_raw": contact.phone if reveal_phone else None,
        "wechat": contact.wechat,
        "is_primary": contact.is_primary,
        "remark": contact.remark,
        "created_at": _iso(contact.created_at),
    }


def followup_out(f: FollowUp) -> dict:
    return {
        "id": f.id,
        "lead_id": f.lead_id,
        "user_id": f.user_id,
        "user_name": f.user_name,
        "method": f.method,
        "method_label": dicts.label_of(dicts.FOLLOW_METHOD_LABELS, f.method),
        "followed_at": _iso(f.followed_at),
        "content": f.content,
        "is_valid": f.is_valid,
        "next_action": f.next_action,
        "next_action_label": dicts.label_of(dicts.NEXT_ACTION_LABELS, f.next_action),
        "next_follow_at": _iso(f.next_follow_at),
        "stage": f.stage,
        "stage_label": dicts.label_of(dicts.STAGE_LABELS, f.stage),
        "related_standard_id": f.related_standard_id,
        "source": f.source,
        "created_at": _iso(f.created_at),
    }


def assignment_out(a: Assignment) -> dict:
    return {
        "id": a.id,
        "lead_id": a.lead_id,
        "action": a.action,
        "action_label": dicts.label_of(dicts.ASSIGNMENT_ACTION_LABELS, a.action),
        "from_owner_id": a.from_owner_id,
        "from_owner_name": a.from_owner_name,
        "to_owner_id": a.to_owner_id,
        "to_owner_name": a.to_owner_name,
        "operator_id": a.operator_id,
        "operator_name": a.operator_name,
        "remark": a.remark,
        "created_at": _iso(a.created_at),
    }


def reminder_out(r: Reminder) -> dict:
    return {
        "id": r.id,
        "lead_id": r.lead_id,
        "lead_name": r.lead_name,
        "type": r.type,
        "type_label": dicts.label_of(dicts.REMINDER_TYPE_LABELS, r.type),
        "content": r.content,
        "remind_at": _iso(r.remind_at),
        "receiver_id": r.receiver_id,
        "receiver_name": r.receiver_name,
        "status": r.status,
        "done_at": _iso(r.done_at),
        "source_diagnosis_id": r.source_diagnosis_id,
        "source_action_index": r.source_action_index,
        "source_suggestion": r.source_suggestion,
        "completion_followup_id": r.completion_followup_id,
        "created_at": _iso(r.created_at),
    }


def target_out(t: MarketTarget, actual_value: float | None = None) -> dict:
    actual = actual_value if actual_value is not None else 0.0
    rate = (actual / t.target_value) if t.target_value else 0.0
    return {
        "id": t.id,
        "period_type": t.period_type,
        "period": t.period,
        "user_id": t.user_id,
        "user_name": t.user_name,
        "metric": t.metric,
        "metric_label": METRIC_LABELS.get(t.metric, t.metric),
        "target_value": t.target_value,
        "actual_value": actual,
        "achievement_rate": round(rate, 4),
        "setter_name": t.setter_name,
        "updated_at": _iso(t.updated_at),
    }


METRIC_LABELS: dict[str, str] = {
    "valid_lead_count": "有效商机数",
    "new_lead_count": "新增线索数",
    "valid_rate": "有效率",
    "sign_amount": "签单金额",
    "paid_amount": "回款金额",
}


def log_out(log: OperationLog) -> dict:
    return {
        "id": log.id,
        "operator_id": log.operator_id,
        "operator_name": log.operator_name,
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "target_name": log.target_name,
        "detail": log.detail,
        "ip": log.ip,
        "created_at": _iso(log.created_at),
    }


def notification_out(n: Notification) -> dict:
    return {
        "id": n.id,
        "title": n.title,
        "content": n.content,
        "type": n.type,
        "is_read": n.is_read,
        "biz_type": n.biz_type,
        "biz_id": n.biz_id,
        "created_at": _iso(n.created_at),
    }


def import_batch_out(b: ImportBatch) -> dict:
    return {
        "id": b.id,
        "batch_no": b.batch_no,
        "filename": b.filename,
        "file_name": b.filename,  # 前端字段名（与 filename 同值，避免二选一造成不一致）
        "importer_id": b.importer_id,
        "importer_name": b.importer_name,
        "total": b.total,
        "success": b.success,
        "failed": b.failed,
        "success_rate": round(b.success / b.total, 4) if b.total else 0.0,
        "errors": b.errors or [],
        "created_at": _iso(b.created_at),
    }


def stage_standard_out(s: StageStandard) -> dict:
    return {
        "id": s.id,
        "stage": s.stage,
        "stage_label": dicts.label_of(dicts.STAGE_LABELS, s.stage),
        "seq": s.seq,
        "content": s.content,
        "is_hard_gate": s.is_hard_gate,
        "duration_days": s.duration_days,
        "evidence_hint": s.evidence_hint,
        "sort_order": s.sort_order,
        "is_active": s.is_active,
    }


def stage_completion_out(c: StageCompletion) -> dict:
    return {
        "id": c.id,
        "lead_id": c.lead_id,
        "stage": c.stage,
        "standard_id": c.standard_id,
        "standard_content": c.standard_content,
        "is_done": c.is_done,
        "evidence": c.evidence,
        "done_by": c.done_by,
        "done_by_name": c.done_by_name,
        "done_at": _iso(c.done_at),
        "remark": c.remark,
        "source": c.source,
        "source_label": dicts.label_of(dicts.STANDARD_SOURCE_LABELS, c.source),
    }


def stage_transition_out(t: StageTransition) -> dict:
    return {
        "id": t.id,
        "lead_id": t.lead_id,
        "from_stage": t.from_stage,
        "from_stage_label": dicts.label_of(dicts.STAGE_LABELS, t.from_stage),
        "to_stage": t.to_stage,
        "to_stage_label": dicts.label_of(dicts.STAGE_LABELS, t.to_stage),
        "transition_at": _iso(t.transition_at),
        "operator_name": t.operator_name,
        "stay_days": t.stay_days,
        "basis": t.basis,
        "remark": t.remark,
    }


def amount_change_out(a: AmountChange) -> dict:
    return {
        "id": a.id,
        "lead_id": a.lead_id,
        "before_fen": a.before_fen,
        "after_fen": a.after_fen,
        "delta_fen": a.after_fen - a.before_fen,
        "reason": a.reason,
        "stage": a.stage,
        "stage_label": dicts.label_of(dicts.STAGE_LABELS, a.stage),
        "operator_name": a.operator_name,
        "source": a.source,
        "created_at": _iso(a.created_at),
    }


def risk_out(r: RiskAlert) -> dict:
    return {
        "id": r.id,
        "lead_id": r.lead_id,
        "kind": r.kind or "business_risk",
        "kind_label": dicts.label_of(dicts.ALERT_KIND_LABELS, r.kind or "business_risk"),
        "risk_type": r.risk_type,
        "risk_label": dicts.label_of(dicts.RISK_TYPE_LABELS, r.risk_type),
        "owner_id": r.owner_id,
        "owner_name": r.owner_name,
        "trigger_rule": r.trigger_rule,
        "description": r.description,
        "status": r.status,
        "status_label": dicts.label_of(dicts.RISK_STATUS_LABELS, r.status),
        "handler_name": r.handler_name,
        "handled_at": _iso(r.handled_at),
        "handle_note": r.handle_note,
        "source": r.source,
        "created_at": _iso(r.created_at),
    }


def ai_draft_out(d: AiDraft) -> dict:
    return {
        "id": d.id,
        "source_message": d.source_message,
        "channel": d.channel,
        "lead_id": d.lead_id,
        "lead_name": d.lead_name,
        "match_confidence": d.match_confidence,
        "match_candidates": d.match_candidates or [],
        "summary": d.summary,
        "payload": d.payload or {},
        "status": d.status,
        "created_by": d.created_by,
        "created_by_name": d.created_by_name,
        "confirmed_by_name": d.confirmed_by_name,
        "confirmed_at": _iso(d.confirmed_at),
        "apply_result": d.apply_result,
        "parse_mode": d.parse_mode,
        "model_used": d.model_used,
        "created_at": _iso(d.created_at),
    }


def ai_diagnosis_out(d: AiDiagnosis) -> dict:
    return {
        "id": d.id,
        "lead_id": d.lead_id,
        "lead_name": d.lead_name,
        "stage": d.stage,
        "stage_label": dicts.label_of(dicts.STAGE_LABELS, d.stage),
        "trigger": d.trigger,
        "content": d.content,
        "structured": d.structured,
        "adopted": d.adopted,
        "created_by_name": d.created_by_name,
        "parse_mode": d.parse_mode,
        "model_used": d.model_used,
        "created_at": _iso(d.created_at),
    }
