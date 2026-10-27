"""持久项目档案及销售行动；写入在调用方事务内，绝不等待模型。"""

from datetime import datetime, time
from uuid import uuid4

from sqlalchemy import func, select, update

from app.ai.assistant_facts import parse_time
from app.core.errors import ConflictError, ValidationError
from app.core.rules_version import risk_rules_digest
from app.core.sales_rules import rhythm_issue
from app.models.activity import FollowUp, Reminder
from app.models.dossier import AssistantAction, ProjectDossier, ProjectFactEvent
from app.models.enums import AlertKind, RiskStatus, RiskType
from app.models.stage import RiskAlert, StageCompletion
from app.services import lead_service


async def get(db, lead_id):
    return (
        await db.execute(
            select(ProjectDossier).where(ProjectDossier.lead_id == lead_id)
        )
    ).scalar_one_or_none()


async def snapshot(db, user, lead_id, *, compact=False):
    lead = await lead_service.get_lead(db, lead_id)
    await lead_service.assert_can_view_lead(db, user, lead)
    dossier = await get(db, lead_id)
    events = (
        (
            await db.execute(
                select(ProjectFactEvent)
                .where(ProjectFactEvent.lead_id == lead_id)
                .order_by(
                    ProjectFactEvent.occurred_at.desc(),
                    ProjectFactEvent.created_at.desc(),
                )
                .limit(40)
            )
        )
        .scalars()
        .all()
    )
    follows = (
        await db.execute(
            (
                select(FollowUp)
                if not compact
                else select(
                    FollowUp.id,
                    FollowUp.followed_at,
                    func.length(FollowUp.content).label("chars"),
                )
            )
            .where(FollowUp.lead_id == lead_id)
            .order_by(FollowUp.followed_at.desc())
            .limit(40)
        )
    ).all()
    follows = [r[0] for r in follows] if not compact else follows
    actions = (
        (
            await db.execute(
                select(AssistantAction)
                .where(
                    AssistantAction.lead_id == lead_id,
                    AssistantAction.owner_id == user.id,
                )
                .order_by(AssistantAction.created_at.desc())
                .limit(40)
            )
        )
        .scalars()
        .all()
    )
    evidence = [{"key": "E0", "content": "", "occurred_at": None}]
    evidence += (
        [
            {
                "key": f"E{i}",
                "content": f.content,
                "occurred_at": f.followed_at.isoformat(),
                # ：宿主侧带上"这条证据是哪条消息"，判定层才能区分
                # 本轮原文与旧引用；下发给模型仍由 stage_input 白名单投影成三键。
                "followup_id": f.id,
            }
            for i, f in enumerate(follows, 1)
        ]
        if not compact
        else []
    )
    if compact and dossier:
        quotes = {}
        for field, proof in (dossier.evidence or {}).items():
            # compact 只补「当前事实的首次确立来源 + 最近确认来源」（S02：避免把首次来源
            # 当成最新状态），不加载 superseded 完整历史。
            for source in (proof, proof.get("confirmation") or {}):
                if source.get("quote"):
                    quotes.setdefault(
                        source["quote"], (source.get("occurred_at"), source.get("turn_id"))
                    )
        evidence += [
            {
                "key": f"E{i}",
                "content": text,
                "occurred_at": time,
                # 同上：compact 槽用档案里记着的原始 turn_id 作为来源身份。
                "turn_id": tid or "",
            }
            for i, (text, (time, tid)) in enumerate(quotes.items(), 1)
        ]
    from app.models.ai import AiDiagnosis

    requirement = (
        await db.execute(
            select(AiDiagnosis)
            .where(
                AiDiagnosis.lead_id == lead_id, AiDiagnosis.trigger == "requirements"
            )
            .order_by(AiDiagnosis.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    requirement_candidate_id = requirement.id if requirement else None
    if (
        requirement
        and (requirement.structured or {}).get("requirements_status") == "draft"
    ):
        requirement = None  # 草稿仍保留，但不作为阶段达成材料，也不阻止后续补齐更新。
    if requirement:
        evidence.append(
            {
                "key": "ER",
                "content": "已根据客户事实整理需求报告：" + requirement.content,
                "occurred_at": requirement.created_at.isoformat(),
            }
        )
    completions = (
        (
            await db.execute(
                select(StageCompletion)
                .where(
                    StageCompletion.lead_id == lead_id,
                    StageCompletion.is_done.is_(True),
                )
                .order_by(StageCompletion.stage, StageCompletion.done_at)
            )
        )
        .scalars()
        .all()
    )
    result = {
        "requirements_report": requirement.id if requirement else None,
        "requirements_report_candidate": requirement_candidate_id,
        "followup_id": events[0].followup_id if events else None,
        "revision": dossier.revision if dossier else None,
        "facts": dossier.facts if dossier else {},
        "project_amount_fen": lead.budget_amount_fen,
        "record": events[0].record if events else {},
        "record_occurred_at": events[0].occurred_at.isoformat() if events else None,
        "evidence": evidence,
        "stage_history": [
            {
                "stage": c.stage,
                "standard": c.standard_content,
                "evidence": c.evidence,
                "achieved_at": c.done_at.isoformat() if c.done_at else None,
            }
            for c in completions
        ],
        "actions": [
            {
                "key": f"A{i}",
                "id": a.id,
                "content": a.content,
                "due_at": a.due_at.isoformat() if a.due_at else None,
                "status": a.status,
                "effect": a.effect,
                "customer_confirmed": a.customer_confirmed,
            }
            for i, a in enumerate(actions, 1)
        ],
        "stage": lead.current_stage,
        "customer": lead.name,
        "project": lead.project_name,
        "organization_type": lead.org_type,
    }
    if compact:
        from app.services.project_context_source import ProjectEvidenceSource

        descriptors = [
            {
                "key": f"F{i}",
                "id": f.id,
                "occurred_at": f.followed_at.isoformat(),
                "chars": f.chars or 0,
            }
            for i, f in enumerate(follows, 1)
        ]
        result["evidence_catalog"] = [
            {k: v for k, v in d.items() if k != "id"} for d in descriptors
        ]
        result["_evidence_source"] = ProjectEvidenceSource(
            db=db,
            user=user,
            lead_id=lead_id,
            revision=result["revision"],
            descriptors=descriptors,
        )
    return result


def proof_effective_time(proof):
    """字段当前事实的时序水位：最近一次确认/变更的业务发生时间。

    首次确立来源（`turn_id/quote/occurred_at`）与最近确认时序是两种职责（S02）：
    同值再确认只推进这里读到的水位，不改写首次来源。旧档案没有新元数据时
    回退 `occurred_at`，保持既有护栏语义。依据始终是业务发生时间，不是录入时间。
    """
    proof = proof or {}
    return parse_time(proof.get("confirmed_at")) or parse_time(proof.get("occurred_at"))


def condition_prior_summary(dossier):
    """固定双条件的**历史累计快照**（含来源日期），供阶段节点区分历史与当前。

    C03 缺陷：`facts` 里 `quote_completion_state=not_completed`（昨日）与本轮
    `implementation_progress`（今日报价完成）平铺，模型把历史当约束。宿主把这份
    "历史累计 + 它自己的来源日期"单独交付，模型才能判断本轮是不是更晚的新事实；
    条件状态本身仍由模型按逐字原文判断，宿主不代判。
    """
    from app.core.sales_rules import condition_ids, fact_key

    evidence = (getattr(dossier, "evidence", None) or {}) if dossier is not None else {}
    facts = (getattr(dossier, "facts", None) or {}) if dossier is not None else {}
    out = {}
    for cid in condition_ids():
        key = fact_key(cid)
        proof = evidence.get(key) or {}
        stamp = proof_effective_time(proof) if key else None
        out[cid] = {
            "state": facts.get(key) or "unknown",
            "occurred_at": stamp.isoformat() if stamp is not None else "",
        }
    return out


def condition_prior_times(dossier):
    """固定双条件在档案里的现行时序水位（condition_id → ISO 业务时间）。

    助手与 PC 两个入口都以此判断本轮候选是不是**事后补录**：
    校验、累计、实际生效写入与最终 AND 必须用同一组时序合法的状态，
    不能一处拒绝旧事实、另一处照用它勾选标准。
    """
    from app.core.sales_rules import condition_ids, fact_key

    evidence = (getattr(dossier, "evidence", None) or {}) if dossier is not None else {}
    times = {}
    for cid in condition_ids():
        key = fact_key(cid)
        stamp = proof_effective_time(evidence.get(key)) if key else None
        times[cid] = stamp.isoformat() if stamp is not None else ""
    return times


def _apply_fact_update(
    facts,
    evidence,
    *,
    key,
    value,
    quote,
    occurred,
    turn_id,
    rule_revision=None,
    knowledge_state=None,
    reaffirm=True,
):
    """单字段事实更新：`persist` 与宿主共享规则写入（PC 采纳固定条件）都走这里，不留第二套语义。

    历史补录不覆盖更晚发生的事实；水位是该字段最近一次确认/变更的业务发生时间（S02），
    不是首次确立时间；同日更正沿用最新记录。
    """
    prior = evidence.get(key, {})
    prior_time = proof_effective_time(prior)
    if prior_time and occurred < prior_time:
        return False
    old = facts.get(key)
    # 当前字段只保存已核验的最新文本：语义合并与取舍归模型负责，
    # 宿主不把旧值拼回当前摘要（否则同一字段同时持有互斥状态），
    # 也不在此用关键词或子串猜语义（Spec20 §7.4）。
    facts[key] = value
    # 值变化：当前来源改指向本轮原话，被取代的旧值连同它自己的原话与时点追加进
    # 该字段的历史（Spec20 §7.3/§7.4「明确更正保留旧值和来源」），不与最新状态混为一段。
    # 值未变化的再次确认：当前事实与它最初确立时的来源都不动，历史索引原样携带；
    # 本次原话仍按事件留在 ProjectFactEvent 里。既不因同值确认清空链条，
    # 也不凭空生成重复的「被取代」条目（S01）。
    superseded_now = isinstance(old, str) and bool(old) and old != value
    history = list(prior.get("superseded") or [])
    if superseded_now:
        entry = {"value": old, "superseded_by_turn_id": turn_id}
        for other in (
            "turn_id",
            "quote",
            "occurred_at",
            "confirmed_at",
            "confirmation",
            "knowledge_state",
        ):
            if prior.get(other):
                entry[other] = prior[other]
        history.append(entry)
    same_value_reaffirmed = (
        not superseded_now and isinstance(old, str) and old == value and bool(prior)
    )
    if same_value_reaffirmed and not reaffirm:
        # ：引用旧证据的再引证不是"本轮重新确认"。该字段与它的水位、
        # 确认来源全部原样保留（不推进 confirmed_at、不写 confirmation）；
        # 真正本轮的再确认由调用方带 reaffirm=True 走下面的既有分支。
        return False
    if same_value_reaffirmed:
        proof = dict(prior)
        # 只把最近确认水位推进到更晚的业务发生时间；乱序到达的更早确认不倒退时序（S02）。
        best = proof_effective_time(prior)
        if best is None or occurred > best:
            proof["confirmed_at"] = occurred.isoformat()
            proof["confirmation"] = {
                "turn_id": turn_id,
                "quote": quote,
                "occurred_at": occurred.isoformat(),
            }
    else:
        proof = {
            "turn_id": turn_id,
            "quote": quote,
            "occurred_at": occurred.isoformat(),
        }
    if knowledge_state is not None:
        proof["knowledge_state"] = knowledge_state
    if rule_revision:
        # 宿主按共享规则写入的结构化状态锚定当时的规则修订；
        # 旧行保持无修订，不回填、不重判（口径同 biz_risk_alert.rule_revision）。
        proof["rule_revision"] = rule_revision
    if history:
        proof["superseded"] = history
    evidence[key] = proof
    return True


async def record_condition_states(db, *, lead, user, entries, followup, rule_revision):
    """PC 采纳时把已逐字核验的固定条件状态写进项目档案（与助手入口同一存储与同一时序语义）。

    - 每个条件按**自己实际定位到的那条跟进**存来源与业务日期
      （`turn_id=pc-followup:<该跟进id>`、`occurred_at=该跟进.followed_at`）；只有确实没有
      独立来源跟进时才回落到主引用；
    - 来源跟进必须属于本商机，否则整批拒绝（不跨项目凑来源）；
    - unknown 或无可定位原话的条件不写入：不能以默认 unknown 清空已知项；档案里已累计的
      条件因此保留其**原来源**，不会被 PC 采纳伪装成本轮才完成；
    - 规则修订随来源一起锚定，历史达成由此可审计「当时按哪版规则」；同一条跟进重复采纳幂等不重链；
    - 状态**发生变化**时按来源跟进各写一条 `biz_project_fact_event`（与助手入口同表同结构：
      `turn_id/occurred_at/changes{字段:值}/followup_id`），使采纳历史不只是备注里的一段文字；
      同值重复采纳不重链也不重复记事件。
    """
    writes = []
    for entry in entries or []:
        key, state = entry["key"], entry.get("state")
        quote = entry.get("quote")
        if not key or state not in {"completed", "not_completed"} or not quote:
            continue
        writes.append((key, state, quote, entry.get("followup_id") or followup.id))
    if not writes:
        return None
    sources = {}
    for fid in {item[3] for item in writes}:
        source = await db.get(FollowUp, fid)
        if source is None or source.lead_id != lead.id:
            raise ValidationError("条件来源跟进不属于该商机，不能作为达成依据")
        sources[fid] = source
    dossier = await get(db, lead.id)
    if dossier is None:
        dossier = ProjectDossier(lead_id=lead.id, revision=0)
        db.add(dossier)
        await db.flush()
    changed = await db.execute(
        update(ProjectDossier)
        .where(
            ProjectDossier.id == dossier.id, ProjectDossier.revision == dossier.revision
        )
        .values(revision=dossier.revision + 1)
    )
    if changed.rowcount != 1:
        raise ConflictError("项目档案已变化，请重试本次采纳")
    facts, evidence = dict(dossier.facts or {}), dict(dossier.evidence or {})
    changed_by_source: dict[str, dict] = {}
    quote_by_source: dict[str, str] = {}
    for key, state, quote, fid in writes:
        source = sources[fid]
        before = facts.get(key)
        applied = _apply_fact_update(
            facts,
            evidence,
            key=key,
            value=state,
            quote=quote,
            occurred=source.followed_at or datetime.now(),
            turn_id=f"pc-followup:{source.id}",
            rule_revision=rule_revision,
        )
        evidence[key].setdefault("followup_id", source.id)
        # 只在状态真的变化时留事实事件（同值重复采纳不重链、也不重复记历史）；
        # 事件与助手入口同表同结构：turn_id / occurred_at / changes{字段:值} / followup_id。
        if applied and before != state:
            changed_by_source.setdefault(fid, {})[key] = state
            quote_by_source.setdefault(fid, quote)
    dossier.facts, dossier.evidence = facts, evidence
    for fid, changes in changed_by_source.items():
        source = sources[fid]
        db.add(
            ProjectFactEvent(
                lead_id=lead.id,
                turn_id=f"pc:{uuid4().hex}",
                owner_id=user.id,
                occurred_at=source.followed_at or datetime.now(),
                content=quote_by_source[fid],
                changes=changes,
                record={},
                followup_id=source.id,
            )
        )
    await db.flush()
    return dossier


async def sync_signed_contract(db, *, user, lead, amount_fen, sign_date):
    """Persist the confirmed PC sign operation without deriving a customer budget."""
    dossier = await get(db, lead.id)
    if dossier is None:
        dossier = ProjectDossier(lead_id=lead.id, revision=0)
        db.add(dossier)
        await db.flush()
    changed = await db.execute(
        update(ProjectDossier)
        .where(
            ProjectDossier.id == dossier.id,
            ProjectDossier.revision == dossier.revision,
        )
        .values(revision=dossier.revision + 1)
    )
    if changed.rowcount != 1:
        raise ConflictError("项目档案已变化，请重试本次签约")

    occurred = datetime.combine(sign_date, time.min) if sign_date else datetime.now()
    event_id = f"sign-contract:{uuid4().hex}"
    facts = dict(dossier.facts or {})
    evidence = dict(dossier.evidence or {})
    changes = {}
    pc_quote = (
        "PC签约确认参数（非客户原话）："
        f"合同金额{amount_fen}分；"
        f"签约日{sign_date.isoformat() if sign_date else '未提供'}"
        if type(amount_fen) is int and amount_fen > 0
        else f"PC签约操作（非客户原话）：签约日{sign_date.isoformat() if sign_date else '未提供'}"
    )
    updates = [("contract_signed", True)]
    if type(amount_fen) is int and amount_fen > 0:
        updates.extend(
            (
                ("contract_amount_fen", amount_fen),
                ("project_amount_fen", amount_fen),
                ("project_amount_basis", "contract"),
            )
        )
    for key, value in updates:
        prior_fact = facts.get(key)
        prior_proof = dict(evidence.get(key) or {})
        if prior_fact != value:
            changes[key] = value
            if prior_fact is not None:
                history = list(prior_proof.get("superseded") or [])
                superseded = {"value": prior_fact, "superseded_by_turn_id": event_id}
                for field in (
                    "turn_id",
                    "quote",
                    "occurred_at",
                    "source",
                    "actor_id",
                    "confirmed_at",
                    "confirmation",
                ):
                    if prior_proof.get(field) is not None:
                        superseded[field] = prior_proof[field]
                history.append(superseded)
            facts[key] = value
        proof = {
            "turn_id": event_id,
            "quote": pc_quote,
            "occurred_at": occurred.isoformat(),
            "source": "pc_sign_contract",
            "actor_id": user.id,
            "sign_date": sign_date.isoformat() if sign_date else None,
        }
        history = list(prior_proof.get("superseded") or [])
        if prior_fact is not None and prior_fact != value:
            history.append(superseded)
        if history:
            proof["superseded"] = history
        evidence[key] = proof

    dossier.facts, dossier.evidence = facts, evidence
    if changes:
        db.add(
            ProjectFactEvent(
                lead_id=lead.id,
                turn_id=event_id,
                owner_id=user.id,
                occurred_at=occurred,
                content=pc_quote,
                changes=changes,
                record={
                    "source": "pc_sign_contract",
                    "operator_id": user.id,
                    "contract_amount_fen": amount_fen
                    if type(amount_fen) is int and amount_fen > 0
                    else None,
                    "sign_date": sign_date.isoformat() if sign_date else None,
                },
            )
        )
    await db.flush()
    return dossier


async def persist(
    db,
    *,
    user,
    lead,
    task,
    extracted,
    message,
    turn_id,
    revision,
    record=None,
    followup_id=None,
):
    dossier = await get(db, lead.id)
    if dossier is None:
        if revision is not None:
            raise ConflictError("项目档案已变化，请重新发送本次记录")
        dossier = ProjectDossier(lead_id=lead.id, revision=0)
        db.add(dossier)
        await db.flush()
    elif dossier.revision != revision:
        raise ConflictError("其他会话已更新项目档案，请重试本次消息")
    changed = await db.execute(
        update(ProjectDossier)
        .where(
            ProjectDossier.id == dossier.id, ProjectDossier.revision == dossier.revision
        )
        .values(revision=dossier.revision + 1)
    )
    if changed.rowcount != 1:
        raise ConflictError("项目档案已变化，请重试")
    facts, evidence = (
        {**(task.get("facts") or {}), **(dossier.facts or {})},
        dict(dossier.evidence or {}),
    )
    occurred = parse_time(extracted.get("occurred_at")) or datetime.now()
    updates = {u["key"]: u["value"] for u in extracted["updates"]}
    for u in extracted["updates"]:
        # ：条件更新自带「实际来源」时按来源写入（旧消息的时刻与消息身份），
        # 不再一律顶成本轮 turn_id/occurred；其余字段沿用本轮时点。
        src_occurred = parse_time(u.get("source_occurred_at"))
        applied = _apply_fact_update(
            facts,
            evidence,
            key=u["key"],
            value=u["value"],
            quote=u["quote"],
            occurred=src_occurred or occurred,
            turn_id=u.get("source_turn_id") or turn_id,
            rule_revision=u.get("rule_revision"),
            knowledge_state=u.get("knowledge_state"),
            reaffirm=bool(u.get("new_confirmation", True)),
        )
        if u.get("new_confirmation") is False and not applied:
            # 旧引用（再引证）没有造成本字段任何变化：本轮事件不把它记成变化，
            # 也不留下"本轮确认"的痕迹（S01）；写入层已把该字段原样保留。
            updates.pop(u["key"], None)
    dossier.facts, dossier.evidence = facts, evidence
    db.add(
        ProjectFactEvent(
            lead_id=lead.id,
            turn_id=turn_id,
            owner_id=user.id,
            occurred_at=occurred,
            content=message,
            changes=updates,
            record=record or extracted.get("record") or {},
            followup_id=followup_id,
        )
    )
    task["facts"] = facts
    return dossier


async def apply_actions(db, *, user, lead, extracted, context, turn_id):
    by_key = {a["key"]: a["id"] for a in context.get("actions", [])}
    results = []
    for index, op in enumerate(extracted.get("actions", [])):
        if op["operation"] == "create":
            if not op.get("content"):
                raise ValidationError("下一步行动内容不能为空")
            action = AssistantAction(
                lead_id=lead.id,
                owner_id=user.id,
                origin=f"{turn_id}:{index}",
                content=op["content"],
                status="pending",
                history=[],
            )
            db.add(action)
        else:
            action = await db.get(AssistantAction, by_key.get(op.get("action_key"), ""))
            if not action or action.lead_id != lead.id or action.owner_id != user.id:
                raise ValidationError("不能更新未提供或不属于本项目的行动")
        before = action_dict(action)
        if action.id:
            reminders = (
                (
                    await db.execute(
                        select(Reminder).where(
                            Reminder.dedup_key.like(f"assistant_action:{action.id}:%"),
                            Reminder.status == "pending",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for reminder in reminders:
                reminder.status, reminder.done_at, reminder.done_by = (
                    "done",
                    datetime.now(),
                    user.id,
                )
        if op.get("content"):
            action.content = op["content"]
        if op["operation"] != "complete" and not (
            op.get("effect") is not None
            and op.get("content") is None
            and op.get("due_at") is None
        ):
            due = parse_time(op.get("due_at"))
            # update 只更新资料条件而未给出新日期时沿用原日期
            # （与 record 合并「null=沿用旧值」一致；暂定性质保留在 content），
            # 显式给出日期才覆盖，避免更新旧条件把已约定的汇报日期抹掉。
            if due is not None:
                action.due_at = due
            action.customer_confirmed = bool(op.get("customer_confirmed"))
        else:
            action.status, action.completed_at = "done", datetime.now()
        if op.get("effect") is not None:
            action.effect = op["effect"]
        # 日期依据：引文/锚点/模型候选/规范化值/待确认都落在既有
        # history JSON 里，便于读取链路追溯；不新增 SQL 列，也不写进客户事实摘要。
        date_basis = {
            key: op.get(key)
            for key in ("time_expression", "time_source_quote", "time_anchor",
                        "due_at_model_candidate", "due_at_basis", "due_at_pending",
                        "due_at_unsupported", "due_at_conflict")
            if op.get(key) not in (None, "")
        }
        if op["operation"] == "update" and op.get("due_at") is None:
            # 更新待办时没给日期：要么沿用旧值，要么明确待确认——两者必须可区分。
            if op.get("due_at_pending"):
                date_basis["due_at_basis"] = "pending_confirmation"
                date_basis["due_at_pending"] = True
            else:
                prior_basis = before.get("date_basis") or {}
                date_basis = {
                    **prior_basis,
                    **date_basis,
                    "due_at_basis": "kept_previous" if prior_basis else "pending_no_date",
                    "due_at_pending": False,
                }
            date_basis["previous_due_at"] = before.get("due_at")
        action.history = [
            *(action.history or []),
            {
                "turn_id": turn_id,
                "quote": op["quote"],
                "before": before,
                "operation": op["operation"],
                **({"date_basis": date_basis} if date_basis else {}),
            },
        ]
        await db.flush()
        results.append(action_dict(action))
    return results


def action_dict(a):
    """行动读取视图：除业务字段外，暴露**受控的日期依据**（来自 history 最近一条）。

    只读投影，不改业务语义；`date_status` 让读取方区分三种情况：
    normalized（按锚点规范化）、kept_previous（本轮没给日期，沿用旧值）、
    pending_confirmation（明确待确认，当前日期不可当已确认）。
    """
    basis = {}
    for entry in reversed(list(a.history or [])):
        if isinstance(entry, dict) and entry.get("date_basis"):
            basis = dict(entry["date_basis"])
            break
    date_status = "normal" if a.due_at else "none"
    if basis.get("due_at_conflict"):
        date_status = "time_conflict"
    elif basis.get("due_at_unsupported"):
        date_status = "unsupported_time"
    elif basis.get("due_at_pending"):
        date_status = "pending_confirmation"
    elif basis.get("due_at_basis") in {"pending_no_date", "kept_previous"}:
        date_status = "kept_previous"
    elif basis.get("due_at_basis", "").startswith("relative"):
        date_status = "normalized"
    return {
        "id": a.id,
        "lead_id": a.lead_id,
        "content": a.content,
        "due_at": a.due_at.isoformat() if a.due_at else None,
        "customer_confirmed": a.customer_confirmed,
        "status": a.status,
        "effect": a.effect,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "date_status": date_status,
        **({"date_basis": basis} if basis else {}),
    }


async def refresh_risks(db, *, lead, dossier, today=None):
    """按唯一事实口径复核风险；缺档案表示未知，不回退旧日期判定。"""
    f = (dossier.facts or {}) if dossier else {}
    evidence = (dossier.evidence or {}) if dossier else {}
    created_types = []

    def confirmed_fact(key):
        """Only an explicit typed confirmation can complete a risk fact."""
        proof = evidence.get(key) or {}
        return (
            bool(f.get(key))
            and bool(proof.get("quote"))
            and proof.get("knowledge_state") == "confirmed"
        )

    from app.core.dicts import STAGE_ORDER

    quote_stage = (
        lead.current_stage in STAGE_ORDER and STAGE_ORDER.index(lead.current_stage) >= 2
    )
    findings = {
        RiskType.R1_BUDGET_UNCLEAR: "客户资金来源或项目金额/预算区间尚未明确"
        if quote_stage
        and not confirmed_fact("funding_source")
        or quote_stage
        and not (
            confirmed_fact("available_amount_fen")
            or confirmed_fact("budget_range")
            or confirmed_fact("budget_amount_fen")
            or (
                confirmed_fact("project_amount_fen")
                and confirmed_fact("project_amount_basis")
                and f.get("project_amount_basis")
                in {"application_plan", "customer_budget", "award", "contract"}
            )
        )
        else None,
        RiskType.R3_DECISION_CHAIN: "关键角色及审批路径尚未明确"
        if quote_stage
        and not (confirmed_fact("decision_roles") and confirmed_fact("approval_path"))
        else None,
        RiskType.R4_DEMAND_UNCLEAR: "具体需求场景或建设范围尚未明确"
        if quote_stage
        and not (
            confirmed_fact("need")
            and confirmed_fact("scenario")
            and confirmed_fact("scope")
        )
        else None,
        RiskType.R5_PROJECT_RHYTHM: rhythm_issue(f, today=today),
    }
    for key, field in [
        (RiskType.R2_TRUST_MISSING, "trust_obstacle"),
        (RiskType.R6_PRODUCT_VALUE_MISMATCH, "product_obstacle"),
    ]:
        text = f.get(field)
        if text is not None:
            findings[key] = None if text.startswith("已解除：") else text
    if lead.status == "signed":
        findings = {
            **findings,
            **{
                key: None
                for key in (
                    RiskType.R1_BUDGET_UNCLEAR,
                    RiskType.R3_DECISION_CHAIN,
                    RiskType.R4_DEMAND_UNCLEAR,
                    RiskType.R5_PROJECT_RHYTHM,
                )
            },
        }
    rows = (
        (
            await db.execute(
                select(RiskAlert).where(
                    RiskAlert.lead_id == lead.id,
                    RiskAlert.risk_type.in_(list(findings)),
                    RiskAlert.status != RiskStatus.RESOLVED,
                )
            )
        )
        .scalars()
        .all()
    )
    legacy_sensitive_keys = {
        RiskType.R1_BUDGET_UNCLEAR: (
            "funding_source",
            "available_amount_fen",
            "budget_range",
            "budget_amount_fen",
            "project_amount_fen",
            "project_amount_basis",
        ),
        RiskType.R3_DECISION_CHAIN: ("decision_roles", "approval_path"),
        RiskType.R4_DEMAND_UNCLEAR: ("need", "scenario", "scope"),
    }
    resolved_rows = (
        (
            await db.execute(
                select(RiskAlert).where(
                    RiskAlert.lead_id == lead.id,
                    RiskAlert.risk_type.in_(list(legacy_sensitive_keys)),
                    RiskAlert.status == RiskStatus.RESOLVED,
                )
            )
        )
        .scalars()
        .all()
    )
    for key, description in findings.items():
        active = [r for r in rows if r.risk_type == key]
        if key == RiskType.R5_PROJECT_RHYTHM:
            for row in active:
                if row.kind in {AlertKind.RHYTHM_UNCLEAR, AlertKind.RHYTHM_WINDOW}:
                    row.status, row.handled_at = RiskStatus.RESOLVED, datetime.now()
                    row.handle_note = "旧日期/年度窗口口径停用，现由下一节点及预计时间统一复核。"
            active = [r for r in active if r.status != RiskStatus.RESOLVED]
        if description:
            has_prior_resolution = any(r.risk_type == key for r in resolved_rows)
            has_explicit_unknown = any(
                bool((evidence.get(fact_key) or {}).get("quote"))
                and (evidence.get(fact_key) or {}).get("knowledge_state")
                == "explicitly_unknown"
                for fact_key in legacy_sensitive_keys.get(key, ())
            )
            if not active and has_prior_resolution and not has_explicit_unknown:
                # Missing legacy status cannot grant a new resolution or undo one.
                continue
            own = next((r for r in active if r.source == "assistant"), None)
            if own:
                own.description = description
            elif not active:
                db.add(
                    RiskAlert(
                        lead_id=lead.id,
                        kind=AlertKind.BUSINESS_RISK,
                        risk_type=key,
                        description=description,
                        source="assistant",
                        owner_id=lead.owner_id,
                        owner_name=lead.owner_name,
                        dedup_key=f"assistant:{lead.id}:{key}:{dossier.revision if dossier else 0}",
                        rule_revision=risk_rules_digest(),
                    )
                )
                created_types.append(key)
        else:
            for r in active:
                # R2/R6只有明确解除依据才能走到此处，不能因本轮没提就消失。
                r.status, r.handled_at = RiskStatus.RESOLVED, datetime.now()
                r.handle_note = "项目档案事实复核解除；证据见档案修订" + str(
                    dossier.revision if dossier else 0
                )
    pending = (
        (
            await db.execute(
                select(RiskAlert.risk_type).where(
                    RiskAlert.lead_id == lead.id,
                    RiskAlert.status != RiskStatus.RESOLVED,
                    RiskAlert.risk_type != "",
                )
            )
        )
        .scalars()
        .all()
    )
    lead.risk_types = sorted(set(pending))
    return created_types


async def list_metadata(db, leads):
    """批量列表摘要，不拉取历史或附件。"""
    from app.models.lead import Contact

    ids = [lead.id for lead in leads]
    if not ids:
        return {}
    result = {
        lead.id: {"next_node": None, "next_node_time": None,
                  "rhythm_clear": lead.status == "signed"}
        for lead in leads
    }
    owners = {lead.id: lead.owner_id for lead in leads}
    signed = {lead.id: lead.status == "signed" for lead in leads}
    contacts = (
        (
            await db.execute(
                select(Contact)
                .where(Contact.lead_id.in_(ids))
                .order_by(Contact.is_primary.desc(), Contact.created_at)
            )
        )
        .scalars()
        .all()
    )
    for contact in contacts:
        if "contact_name" not in result[contact.lead_id]:
            result[contact.lead_id].update(
                contact_name=contact.name, contact_position=contact.position
            )
    actions = (
        (
            await db.execute(
                select(AssistantAction)
                .where(
                    AssistantAction.lead_id.in_(ids),
                    AssistantAction.status == "pending",
                )
                .order_by(
                    AssistantAction.due_at.asc().nulls_last(),
                    AssistantAction.created_at,
                )
            )
        )
        .scalars()
        .all()
    )
    for action in actions:
        if (
            action.owner_id == owners[action.lead_id]
            and "next_action" not in result[action.lead_id]
        ):
            result[action.lead_id].update(
                next_action=action.content,
                next_action_at=action.due_at.isoformat() if action.due_at else None,
                appointment_waiting=bool(
                    action.customer_confirmed
                    and action.due_at
                    and action.due_at >= datetime.now()
                ),
            )
    dossiers = (
        (
            await db.execute(
                select(ProjectDossier).where(ProjectDossier.lead_id.in_(ids))
            )
        )
        .scalars()
        .all()
    )
    for dossier in dossiers:
        facts = dossier.facts or {}
        result[dossier.lead_id].update(
            dossier_revision=dossier.revision,
            next_node=facts.get("next_node"),
            next_node_time=facts.get("next_node_time"),
            rhythm_clear=signed[dossier.lead_id] or rhythm_issue(facts) is None,
        )
    return result
