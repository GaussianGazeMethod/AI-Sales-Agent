"""导入导出服务：Excel 线索库导入（模板下载 + 校验 + 失败明细）。

 表头口径：严格按 `docs/references/需求确认工作稿-逐项确认.md` §2「逐表表头」的
   **表 1 客户线索表 + 表 2 客户联系人表**，
   确保迁移自有 Excel 时不漏字段。

为兼容真实历史表格，列名支持一组常见别名（如「区 / 县」「区县」「区」、「联系人」
「联系人姓名」、「手机号」「联系电话」），只要列名命中别名即可正确落库。
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dicts
from app.core.errors import ValidationError
from app.models.enums import IntakeChannel
from app.models.system import ImportBatch
from app.models.user import User
from app.schemas import LeadCreate
from app.services import audit_service, lead_service

# ══════════════ 模板表头══════════════

TEMPLATE_HEADERS = [
    # ── 表 1 客户线索表 ──
    "客户名称",
    "来源",
    "省份",
    "城市",
    "区/县",
    "类型",
    "录入人员",
    "归属人",
    "报备单位",
    "报备人",
    # ── 表 2 客户联系人表 ──
    "联系人姓名",
    "职位",
    "电话",
    "微信",
    "是否主联系人",
    # ── 主表备注 ──
    "备注",
]

#: 一行示例（模板里给出，导入时会自动跳过以「示例：」开头的行）
TEMPLATE_EXAMPLE = [
    "示例：XX市第三中学",
    "市场活动",
    "江苏省",
    "南京市",
    "鼓楼区",
    "公办学校",
    "演示销售",
    "演示销售",
    "",
    "",
    "刘主任",
    "信息中心主任",
    "00000000000",
    "liu_wechat",
    "是",
    "教育装备展现场收集",
]

#: 列名别名 → 内部字段名（按顺序匹配，命中即止，避免同名列被重复占用）
HEADER_ALIASES: list[tuple[str, list[str]]] = [
    ("name", ["客户名称", "客户", "学校名称", "机构名称", "单位名称"]),
    ("source", ["来源", "线索来源"]),
    ("province", ["省份", "省"]),
    ("city", ["城市", "市"]),
    ("district", ["区/县", "区县", "区 / 县", "区、县", "区", "县"]),
    ("org_type", ["类型", "客户类型", "机构类型"]),
    ("creator_name", ["录入人员", "录入人", "创建人"]),
    ("owner_name", ["归属人", "负责人", "归属", "销售负责人"]),
    ("channel_unit", ["报备单位", "渠道单位", "报备渠道商"]),
    ("referrer", ["报备人", "介绍人"]),
    ("contact_name", ["联系人姓名", "联系人", "姓名"]),
    ("contact_position", ["职位", "职务", "联系人职位", "联系人职务"]),
    ("contact_phone", ["电话", "联系电话", "手机号", "手机", "联系人电话"]),
    ("contact_wechat", ["微信", "微信号", "联系人微信"]),
    ("contact_primary", ["是否主联系人", "主联系人"]),
    ("remark", ["备注", "说明"]),
]

#: 文档中标记  必填的列（缺失则该行失败，并在失败明细里指出缺哪几列）
REQUIRED_FIELDS: list[tuple[str, str]] = [
    ("name", "客户名称"),
    ("source", "来源"),
    ("province", "省份"),
    ("city", "城市"),
    ("district", "区/县"),
    ("org_type", "类型"),
]

# 中文标签 → 枚举值（反向字典）
_SOURCE_REVERSE = {v: k for k, v in dicts.LEAD_SOURCE_LABELS.items()}
_ORG_REVERSE = {v: k for k, v in dicts.ORG_TYPE_LABELS.items()}
# 允许用户写简称
_SOURCE_ALIAS = {
    "公司客户池": "company_pool",
    "公司池": "company_pool",
    "客户转介绍": "referral",
    "转介绍": "referral",
    "渠道商": "channel_partner",
    "渠道": "channel_partner",
    "内部人员介绍": "internal_referral",
    "内部介绍": "internal_referral",
    "协会": "association",
    "协会/机构": "association",
    "机构": "association",
    "市场活动": "market_event",
    "展会": "market_event",
    "公开数据平台": "public_data",
    "公开数据": "public_data",
    "运营商": "telecom_operator",
}
_ORG_ALIAS = {
    "教育局": "bureau",
    "公办学校": "public_school",
    "公办": "public_school",
    "民办学校": "private_school",
    "民办": "private_school",
    "中职": "secondary_voc",
    "中专": "secondary_voc",
    "高职": "higher_voc",
    "高校": "university",
    "大学": "university",
}

_WS_RE = re.compile(r"[\s\u3000]+")


def build_template() -> bytes:
    """生成导入模板：表头 + 一行示例 + 列宽与必填提示。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "线索导入模板"
    ws.append(TEMPLATE_HEADERS)
    ws.append(TEMPLATE_EXAMPLE)

    for idx, header in enumerate(TEMPLATE_HEADERS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = max(
            12, len(header) * 2 + 4
        )

    # 第二张表：填写说明（不改导入逻辑，只做提示）
    tips = wb.create_sheet("填写说明")
    tips.append(["列名", "是否必填", "说明"])
    for _, label in REQUIRED_FIELDS:
        tips.append([label, "必填", ""])
    tips.append(["来源", "必填", "8 类：" + "、".join(dicts.LEAD_SOURCE_LABELS.values())])
    tips.append(["类型", "必填", "6 类：" + "、".join(dicts.ORG_TYPE_LABELS.values())])
    tips.append(
        [
            "归属人",
            "选填",
            "填系统里的用户姓名；命中则直接指派并启动跟进计时，未命中会写入备注并在失败明细中提示",
        ]
    )
    tips.append(
        ["录入人员", "选填", "历史 Excel 里的原录入人；命中系统用户则记为创建人，否则记为导入人"]
    )
    tips.append(["报备单位/报备人", "选填", "仅「来源=渠道商 / 内部人员介绍」时填写"])
    tips.append(["是否主联系人", "选填", "填「是」则该联系人为该客户的主联系人"])
    tips.append(
        [
            "同一客户多联系人",
            "选填",
            "同一客户名称 + 省市区的多行会被识别为重复并标记，可在「疑似重复裁决台」合并",
        ]
    )
    tips.column_dimensions["A"].width = 20
    tips.column_dimensions["B"].width = 10
    tips.column_dimensions["C"].width = 80

    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


def _normalize_header(value: object) -> str:
    """列名归一化：去空白（含全角空格），便于匹配「区 / 县」这类写法。"""
    return _WS_RE.sub("", str(value or "").strip())


def _build_index(header: list[str]) -> dict[str, int]:
    """列名 → 内部字段名 的索引（别名匹配 + 每个字段只取第一次出现的列）。"""
    alias_map: list[tuple[str, set[str]]] = [
        (field, {_normalize_header(a) for a in aliases}) for field, aliases in HEADER_ALIASES
    ]
    index: dict[str, int] = {}
    for pos, raw in enumerate(header):
        norm = _normalize_header(raw)
        if not norm:
            continue
        for field, aliases in alias_map:
            if field in index:
                continue
            if norm in aliases:
                index[field] = pos
                break
    return index


def _resolve_source(value: str) -> str | None:
    value = _normalize_header(value)
    if not value:
        return None
    return _SOURCE_REVERSE.get(value) or _SOURCE_ALIAS.get(value)


def _resolve_org(value: str) -> str | None:
    value = _normalize_header(value)
    if not value:
        return None
    return _ORG_REVERSE.get(value) or _ORG_ALIAS.get(value)


def _truthy(value: str) -> bool:
    return _normalize_header(value) in {"是", "Y", "y", "YES", "yes", "true", "True", "1", "主"}


async def _user_by_name(db: AsyncSession, name: str) -> User | None:
    name = (name or "").strip()
    if not name:
        return None
    return (
        (await db.execute(select(User).where(User.name == name, User.is_active.is_(True))))
        .scalars()
        .first()
    )


async def import_leads(db: AsyncSession, *, user: User, filename: str, content: bytes) -> dict:
    """导入线索：逐行校验 → 建线索（撞单只标记不阻断）→ 记录批次与失败明细。"""
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"无法解析 Excel 文件：{exc}") from exc

    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValidationError("Excel 内容为空")

    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    index = _build_index(header)

    # 整表级校验：必需列必须在表头里出现（缺列直接拒绝，避免整批静默丢字段）
    missing_columns = [label for field, label in REQUIRED_FIELDS if field not in index]
    if missing_columns:
        raise ValidationError(
            "表头缺少必需列：" + "、".join(missing_columns) + "。请先下载模板对照修改。"
        )

    batch = ImportBatch(
        # 批次号带 4 位随机后缀：同一秒内连续导入两次不会撞 batch_no 的唯一约束
        batch_no=f"IMP{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid4().hex[:4].upper()}",
        filename=filename,
        importer_id=user.id,
        importer_name=user.name,
    )
    db.add(batch)
    await db.flush()

    total = 0
    success = 0
    errors: list[dict] = []

    for row_no, row in enumerate(rows[1:], start=2):
        if row is None or all(c is None or str(c).strip() == "" for c in row):
            continue
        total += 1

        def cell(field: str) -> str:
            pos = index.get(field)
            if pos is None or pos >= len(row):
                return ""
            value = row[pos]
            return "" if value is None else str(value).strip()

        name = cell("name")
        if not name:
            errors.append({"row": row_no, "message": "客户名称为空"})
            continue
        if name.startswith("示例："):
            total -= 1  # 模板示例行不计入总数
            continue

        # 必填校验：一次性把缺失列都报出来，便于一次改完
        missing = [label for field, label in REQUIRED_FIELDS if not cell(field)]
        if missing:
            errors.append({"row": row_no, "message": "缺少必填列：" + "、".join(missing)})
            continue

        source = _resolve_source(cell("source"))
        if not source:
            errors.append(
                {
                    "row": row_no,
                    "message": f"来源无法识别：{cell('source')}（合法值见「填写说明」工作表）",
                }
            )
            continue

        org_type = _resolve_org(cell("org_type"))
        if not org_type:
            errors.append(
                {
                    "row": row_no,
                    "message": f"类型无法识别：{cell('org_type')}（合法值见「填写说明」工作表）",
                }
            )
            continue

        # 归属人 / 录入人员：按姓名匹配系统用户；匹配不到不阻断，写进备注留痕
        notes: list[str] = []
        owner = await _user_by_name(db, cell("owner_name"))
        if cell("owner_name") and owner is None:
            notes.append(f"导入时归属人「{cell('owner_name')}」未匹配到系统用户")
        creator = await _user_by_name(db, cell("creator_name"))
        creator_name = cell("creator_name")
        if creator_name and creator is None:
            notes.append(f"原录入人员「{creator_name}」未匹配到系统用户")

        remark = cell("remark")
        if notes:
            remark = (remark + "；" if remark else "") + "；".join(notes)

        try:
            lead, _warning = await lead_service.create_lead(
                db,
                user=creator or user,
                payload=LeadCreate(
                    name=name,
                    source=source,
                    province=cell("province"),
                    city=cell("city"),
                    district=cell("district"),
                    org_type=org_type,
                    channel_unit=cell("channel_unit") or None,
                    referrer=cell("referrer") or None,
                    contact_name=cell("contact_name") or None,
                    contact_phone=cell("contact_phone") or None,
                    contact_wechat=cell("contact_wechat") or None,
                    contact_position=cell("contact_position") or None,
                    remark=remark or None,
                ),
            )
            # 归属人命中 → 走正规指派流程（启动 1 工作日/7 工作日计时 + 留痕 + 通知）
            if owner is not None:
                from app.services import assign_service

                await assign_service.change_owner(
                    db,
                    lead=lead,
                    new_owner_id=owner.id,
                    operator=user,
                    remark="Excel 导入时按「归属人」列直接指派",
                )
            # 主联系人：文档表 2 有该字段；未填时首个联系人默认主联系人
            contacts = await lead_service.list_contacts(db, lead.id)
            if contacts:
                primary_flag = _truthy(cell("contact_primary"))
                if primary_flag or not any(c.is_primary for c in contacts):
                    contacts[0].is_primary = True

            success += 1
        except Exception as exc:  # noqa: BLE001 - 单行失败不影响整批
            errors.append({"row": row_no, "message": str(exc)})

    batch.total = total
    batch.success = success
    batch.failed = total - success
    batch.errors = errors[:200]

    await audit_service.write_log(
        db,
        operator=user,
        action="import",
        target_type="lead",
        target_id=batch.id,
        target_name=filename,
        detail=f"Excel 导入：共 {total} 条，成功 {success}，失败 {total - success}",
    )
    return {
        "batch": batch,
        "total": total,
        "success": success,
        "failed": total - success,
        "errors": errors[:200],
        "channel": IntakeChannel.EXCEL_IMPORT,
    }


async def list_batches(
    db: AsyncSession, *, offset: int = 0, limit: int = 20
) -> tuple[list[ImportBatch], int]:
    stmt = select(ImportBatch).order_by(ImportBatch.created_at.desc())
    total = int((await db.execute(select(func.count()).select_from(ImportBatch))).scalar() or 0)
    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return list(rows), total


# ══════════════ 通用行读取与客户匹配（供交付物 / 运营记录 / 运营指标导入复用）══════════════


def read_first_sheet(content: bytes) -> tuple[list[str], list[tuple]]:
    """读取 Excel 第一个工作表 → (表头, 数据行)。解析失败抛 ValidationError。"""
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"无法解析 Excel 文件：{exc}") from exc
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValidationError("Excel 内容为空")
    return [_cell_text(c) for c in rows[0]], rows[1:]


def _cell_text(value: object) -> str:
    """单元格取文本：None → 空串；日期 → YYYY-MM-DD；其余 strip。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def cell_reader(header: list[str], aliases: dict[str, list[str]], row: tuple):
    """生成取值函数：按别名找列，返回 cell("字段名")。"""
    alias_map = [(field, {_normalize_header(a) for a in names}) for field, names in aliases.items()]
    index: dict[str, int] = {}
    for pos, raw in enumerate(header):
        norm = _normalize_header(raw)
        if not norm:
            continue
        for field, names in alias_map:
            if field in index:
                continue
            if norm in names:
                index[field] = pos
                break

    def cell(name: str) -> str:
        pos = index.get(name)
        if pos is None or pos >= len(row):
            return ""
        return _cell_text(row[pos])

    return cell, index


async def resolve_customer(
    db: AsyncSession,
    *,
    contract_no: str = "",
    customer_name: str = "",
    contract: "Any | None" = None,
) -> tuple["Any | None", "Any | None", str | None]:
    """按「合同编号」或「客户名称」定位客户与合同 → (lead, contract, 错误文案)。

    导入专用：**确定性匹配**（精确相等），不做语义判断；同名多条时要求改用合同编号，
    避免把数据挂到错误的客户上。
    """
    from app.models.delivery import Contract
    from app.models.lead import Lead

    if contract is not None:
        lead = (
            await db.execute(
                select(Lead).where(Lead.id == contract.lead_id, Lead.is_deleted.is_(False))
            )
        ).scalar_one_or_none()
        return lead, contract, None

    if contract_no:
        contract = (
            await db.execute(select(Contract).where(Contract.contract_no == contract_no))
        ).scalar_one_or_none()
        if contract is None:
            return None, None, f"合同编号不存在：{contract_no}"
        lead = (
            await db.execute(
                select(Lead).where(Lead.id == contract.lead_id, Lead.is_deleted.is_(False))
            )
        ).scalar_one_or_none()
        return lead, contract, None

    name = (customer_name or "").strip()
    if not name:
        return None, None, "客户名称为空（或填合同编号）"

    leads = list(
        (await db.execute(select(Lead).where(Lead.name == name, Lead.is_deleted.is_(False))))
        .scalars()
        .all()
    )
    if not leads:
        return None, None, f"客户不存在：{name}（请先录入线索/商机）"
    if len(leads) > 1:
        return None, None, f"客户名称重复（{len(leads)} 条），请改用「合同编号」列"
    lead = leads[0]
    contract = (
        (
            await db.execute(
                select(Contract)
                .where(Contract.lead_id == lead.id)
                .order_by(Contract.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    return lead, contract, None


def parse_date_cell(value: str) -> date | None:
    """解析日期单元格：支持 YYYY-MM-DD / YYYY/M/D / YYYY.M.D / YYYY年M月D日。"""
    text = (value or "").strip()
    if not text:
        return None
    normalized = re.sub(r"[年月]", "-", text).replace("日", "").replace("/", "-").replace(".", "-")
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_int_cell(value: str) -> int | None:
    """解析整数单元格（允许 1,200 这类千分位）。"""
    text = (value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None
