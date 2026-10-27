"""展示版播种：仅两个角色，所有客户、金额和跟进均为合成样例。"""
from __future__ import annotations
import logging
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from sqlalchemy import func, select
from app.core.dicts import STAGE_DURATION_DAYS, STAGE_STANDARD_SEED, STAGE_WIN_PROBABILITY
from app.core.security import hash_password
from app.database import async_session_factory
from app.models.activity import FollowUp
from app.models.customer import Customer
from app.models.enums import DataScope, HolidayKind, LeadSource, LeadStatus, OrgType, Stage, UserRole
from app.models.lead import Contact, Lead
from app.models.stage import StageStandard, StageCompletion, StageTransition, AmountChange
from app.models.dossier import ProjectDossier, ProjectFactEvent
from app.models.system import Holiday
from app.models.user import Department, User
logger = logging.getLogger(__name__)
HOLIDAYS_2026: list[tuple[str, str, str]] = [
    ("2026-01-01", HolidayKind.HOLIDAY, "元旦"),
    ("2026-01-02", HolidayKind.HOLIDAY, "元旦"),
    ("2026-01-03", HolidayKind.HOLIDAY, "元旦"),
    ("2026-01-04", HolidayKind.MAKEUP_WORKDAY, "元旦调休上班"),
    ("2026-02-15", HolidayKind.HOLIDAY, "春节"),
    ("2026-02-16", HolidayKind.HOLIDAY, "春节"),
    ("2026-02-17", HolidayKind.HOLIDAY, "春节"),
    ("2026-02-18", HolidayKind.HOLIDAY, "春节"),
    ("2026-02-19", HolidayKind.HOLIDAY, "春节"),
    ("2026-02-20", HolidayKind.HOLIDAY, "春节"),
    ("2026-02-21", HolidayKind.HOLIDAY, "春节"),
    ("2026-02-22", HolidayKind.HOLIDAY, "春节"),
    ("2026-02-23", HolidayKind.HOLIDAY, "春节"),
    ("2026-02-14", HolidayKind.MAKEUP_WORKDAY, "春节调休上班"),
    ("2026-02-28", HolidayKind.MAKEUP_WORKDAY, "春节调休上班"),
    ("2026-04-04", HolidayKind.HOLIDAY, "清明节"),
    ("2026-04-05", HolidayKind.HOLIDAY, "清明节"),
    ("2026-04-06", HolidayKind.HOLIDAY, "清明节"),
    ("2026-05-01", HolidayKind.HOLIDAY, "劳动节"),
    ("2026-05-02", HolidayKind.HOLIDAY, "劳动节"),
    ("2026-05-03", HolidayKind.HOLIDAY, "劳动节"),
    ("2026-05-04", HolidayKind.HOLIDAY, "劳动节"),
    ("2026-05-05", HolidayKind.HOLIDAY, "劳动节"),
    ("2026-05-09", HolidayKind.MAKEUP_WORKDAY, "劳动节调休上班"),
    ("2026-06-19", HolidayKind.HOLIDAY, "端午节"),
    ("2026-06-20", HolidayKind.HOLIDAY, "端午节"),
    ("2026-06-21", HolidayKind.HOLIDAY, "端午节"),
    ("2026-09-25", HolidayKind.HOLIDAY, "中秋节"),
    ("2026-09-26", HolidayKind.HOLIDAY, "中秋节"),
    ("2026-09-27", HolidayKind.HOLIDAY, "中秋节"),
    ("2026-10-01", HolidayKind.HOLIDAY, "国庆节"),
    ("2026-10-02", HolidayKind.HOLIDAY, "国庆节"),
    ("2026-10-03", HolidayKind.HOLIDAY, "国庆节"),
    ("2026-10-04", HolidayKind.HOLIDAY, "国庆节"),
    ("2026-10-05", HolidayKind.HOLIDAY, "国庆节"),
    ("2026-10-06", HolidayKind.HOLIDAY, "国庆节"),
    ("2026-10-07", HolidayKind.HOLIDAY, "国庆节"),
    ("2026-09-20", HolidayKind.MAKEUP_WORKDAY, "国庆调休上班"),
    ("2026-10-10", HolidayKind.MAKEUP_WORKDAY, "国庆调休上班"),
]

async def _seed_standards(db) -> None:
    count = int((await db.execute(select(func.count()).select_from(StageStandard))).scalar() or 0)
    if count:
        return
    for order, (stage, seq, content, is_hard_gate, hint) in enumerate(STAGE_STANDARD_SEED):
        db.add(
            StageStandard(
                stage=stage,
                seq=seq,
                content=content,
                is_hard_gate=is_hard_gate,
                duration_days=STAGE_DURATION_DAYS.get(stage, 0),
                evidence_hint=hint,
                sort_order=order,
            )
        )
    logger.info("已播种 7 环节标准卡 %s 条", len(STAGE_STANDARD_SEED))

async def _seed_holidays(db) -> None:
    count = int((await db.execute(select(func.count()).select_from(Holiday))).scalar() or 0)
    if count:
        return
    for day, kind, name in HOLIDAYS_2026:
        db.add(Holiday(day=date.fromisoformat(day), kind=kind, name=name))
    logger.info("已播种 2026 年法定节假日 %s 条", len(HOLIDAYS_2026))

async def run_seed() -> None:
    """首次启动初始化合成数据；重复启动保留演示操作。"""
    from app.services import risk_service
    async with async_session_factory() as db:
        users = {}
        for username, name, role, scope in [
            ("sales", "演示销售", UserRole.MARKET_STAFF, DataScope.SELF),
            ("manager", "演示经理", UserRole.MARKET_MANAGER, DataScope.ALL),
        ]:
            user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
            if user is None:
                user = User(username=username, name=name, password_hash=hash_password("123456"),
                            role=role, data_scope=scope, department="销售部", phone="")
                db.add(user)
                await db.flush()
            users[username] = user
        if not (await db.execute(select(Department))).first():
            db.add(Department(name="销售部"))
        await _seed_standards(db)
        await _seed_holidays(db)
        await _seed_demo_cases(db, users)
        await risk_service.ensure_rule_defaults(db)
        await db.commit()


async def _seed_demo_cases(db, users) -> None:
    """导入完整合成场景；预置依据与模型运行结果严格区分。"""
    source = Path(__file__).resolve().parents[1] / "examples" / "sales_cases.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    seller = users["sales"]
    now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    standards = list((await db.execute(select(StageStandard).order_by(StageStandard.sort_order))).scalars())
    stage_order = list(STAGE_DURATION_DAYS)
    from app.services import stage_service

    for case in payload["cases"]:
        customer = (await db.execute(select(Customer).where(Customer.name == case["customer_name"]))).scalar_one_or_none()
        if customer is None:
            customer = Customer(name=case["customer_name"], org_type=OrgType.PUBLIC_SCHOOL,
                                owner_id=seller.id, owner_name=seller.name, remark="虚构演示机构")
            db.add(customer)
            await db.flush()
        for project in case["projects"]:
            existing = (await db.execute(select(Lead.id).where(Lead.customer_id == customer.id, Lead.project_name == project["name"]))).first()
            if existing:
                continue
            items = project["events"]
            current = project["current_stage"]
            current_index = stage_order.index(current)
            started = now - timedelta(days=items[0]["days_before_today"])
            lead = Lead(name=customer.name, project_name=project["name"], customer_id=customer.id,
                        source=LeadSource.MARKET_EVENT, org_type=OrgType.PUBLIC_SCHOOL,
                        creator_id=seller.id, creator_name=seller.name, owner_id=seller.id, owner_name=seller.name,
                        status=LeadStatus.VALID, current_stage=current, stage_entered_at=started,
                        assign_at=started, first_follow_at=started, valid_at=started,
                        budget_amount_fen=project["initial_budget_fen"],
                        win_probability=STAGE_WIN_PROBABILITY[current],
                        remark="完整合成展示场景；标准及历史为明确预置，不是模型验收记录")
            db.add(lead)
            await db.flush()
            db.add(Contact(lead_id=lead.id, name="演示主任", position="信息中心主任", phone="",
                           wechat="demo_" + project["key"].replace("-", "_"), is_primary=True,
                           remark="虚构演示联系ID，不对应真实账号"))
            follows = []
            for index, item in enumerate(items):
                occurred = now - timedelta(days=item["days_before_today"])
                content = "【合成场景】" + item["content"]
                follow = FollowUp(lead_id=lead.id, customer_id=customer.id, user_id=seller.id, user_name=seller.name,
                                  stage=item["stage"], method="visit", followed_at=occurred,
                                  content=content, source="manual")
                db.add(follow)
                await db.flush()
                record = {"action_goal": "推进当前项目", "customer_feedback": content,
                          "achievement": "见本次合成场景原文", "next_action": "按项目下一步安排执行"}
                db.add(ProjectFactEvent(lead_id=lead.id, turn_id="demo-" + follow.id, owner_id=seller.id,
                                        occurred_at=occurred, content=content, changes={"demo_event": index},
                                        record=record, followup_id=follow.id))
                follows.append(follow)
            lead.last_follow_at = follows[-1].followed_at
            for index, stage in enumerate(stage_order[:current_index + 1]):
                stage_follows = [f for f in follows if f.stage == stage]
                if not stage_follows:
                    raise ValueError("合成案例缺少阶段来源：" + project["key"] + stage)
                entered = stage_follows[0].followed_at
                if stage == current:
                    lead.stage_entered_at = entered
                db.add(StageTransition(lead_id=lead.id, from_stage=stage_order[index-1] if index else None,
                                       to_stage=stage, transition_at=entered, operator_id=seller.id,
                                       operator_name=seller.name, basis="合成场景预置流转",
                                       remark="非模型执行轨迹"))
                completed = index < current_index or project["signed"]
                evidence = "\n".join(f.content for f in stage_follows)
                for standard in (x for x in standards if x.stage == stage):
                    db.add(StageCompletion(lead_id=lead.id, stage=stage, standard_id=standard.id,
                                           standard_content=standard.content, is_done=completed,
                                           evidence=evidence if completed else None,
                                           done_by=seller.id if completed else None,
                                           done_by_name=seller.name if completed else None,
                                           done_at=stage_follows[-1].followed_at if completed else None,
                                           source="manual", remark="合成场景预置；非AI判定"))
            facts = dict(project["facts"])
            facts.update(next_node="签约后交接待处理" if project["signed"] else "下一次项目沟通",
                         next_node_time=(now + timedelta(days=3)).date().isoformat())
            statements = dict(project["fact_sources"])
            statements.update({key: "【合成安排】" + key + " = " + str(facts[key])
                               for key in ("next_node", "next_node_time")})
            source_content = "\n".join(statements.values())
            summary = FollowUp(lead_id=lead.id, customer_id=customer.id, user_id=seller.id,
                               user_name=seller.name, stage=current, method="other",
                               followed_at=follows[-1].followed_at, content=source_content, source="manual")
            db.add(summary)
            await db.flush()
            proof = {key: {"turn_id": "demo-" + summary.id, "quote": quote,
                           "occurred_at": summary.followed_at.isoformat(), "source": "synthetic_demo"}
                     for key, quote in statements.items()}
            db.add(ProjectDossier(lead_id=lead.id, revision=1, facts=facts, evidence=proof))
            await db.flush()
            if project["signed"]:
                await stage_service.sign_contract(db, user=seller, lead=lead,
                                                  contract_amount_fen=project["contract_amount_fen"],
                                                  sign_date=follows[-1].followed_at.date())
            elif project["current_budget_fen"] != project["initial_budget_fen"]:
                await stage_service.change_amount(db, user=seller, lead=lead,
                                                  amount_fen=project["current_budget_fen"],
                                                  reason="合成场景中的范围或金额变更")

    for name, assigned in [("练习线索学校丁", True), ("练习公海学校戊", False)]:
        if (await db.execute(select(Lead.id).where(Lead.name == name))).first():
            continue
        creator = seller if assigned else users["manager"]
        lead = Lead(name=name, source=LeadSource.COMPANY_POOL, org_type=OrgType.PUBLIC_SCHOOL,
                    creator_id=creator.id, creator_name=creator.name,
                    owner_id=seller.id if assigned else None, owner_name=seller.name if assigned else None,
                    status=LeadStatus.FOLLOWING if assigned else LeadStatus.UNASSIGNED,
                    assign_at=now if assigned else None, remark="用于四项录入、信息核实、分配和验证的虚构练习记录")
        db.add(lead)
        await db.flush()
        if assigned:
            from app.services import calendar_service
            from app.config import get_settings
            settings = get_settings()
            lead.first_follow_deadline = await calendar_service.deadline_after_working_days(db, now, settings.LEAD_FIRST_FOLLOW_WORKING_DAYS)
            lead.recycle_deadline = await calendar_service.deadline_after_working_days(db, now, settings.LEAD_RECYCLE_WORKING_DAYS)
        db.add(Contact(lead_id=lead.id, name="演示联系人", position="项目负责人", phone="", is_primary=True))
