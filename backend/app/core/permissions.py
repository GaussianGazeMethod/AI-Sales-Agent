"""角色能力矩阵（6.2 角色与权限页的唯一来源）。

**设计原则：矩阵必须反映真实拦截逻辑**，不允许"文档一套、代码一套"。

- 凡 `app/core/deps.py` 或各 API 里已有角色集合常量的能力，这里**直接引用常量**，不复制取值；
- 能力项与 `app/api/*.py` 里的 `Depends(require_admin)` / `role in {...}` 一一对应；
- 改权限时先改拦截逻辑（或这里的常量引用），再由 `/roles` 接口把结果暴露给页面。
  如果矩阵与代码不一致，属于 bug：以代码为准并立刻更新本模块。
"""

from __future__ import annotations

from typing import Any

from app.core.deps import (
    LEAD_ASSIGN_ROLES,
    LEAD_TARGET_ROLES,
    LEAD_VIEW_ROLES,
    LEAD_WRITE_ROLES,
    ROLE_DEFAULT_SCOPE,
)
from app.models.enums import UserRole

#: 能力清单：code / 名称 / 允许角色 / 归属说明（说明对应哪里的拦截）
CAPABILITIES: list[dict[str, Any]] = [
    # ── 闭环一：线索 ──
    {
        "code": "lead.view",
        "name": "查看线索",
        "roles": set(LEAD_VIEW_ROLES),
        "note": "线索列表 / 详情；数据范围见「数据范围」列",
    },
    {
        "code": "lead.write",
        "name": "新建与编辑线索、登记跟进",
        "roles": set(LEAD_WRITE_ROLES),
        "note": "跟进记录必须填「是否有效 + 下一步动作」",
    },
    {
        "code": "lead.assign",
        "name": "指派 / 改派 / 回收线索",
        "roles": set(LEAD_ASSIGN_ROLES),
        "note": "",
    },
    {
        "code": "lead.import",
        "name": "批量导入 / 导出线索",
        "roles": set(LEAD_ASSIGN_ROLES),
        "note": "导入需带批次号与失败明细",
    },
    {
        "code": "target.manage",
        "name": "设定市场目标",
        "roles": set(LEAD_TARGET_ROLES),
        "note": "1.8 按人 × 月设目标",
    },
    # ── 闭环二：商机 ──
    {
        "code": "opportunity.view",
        "name": "查看商机与环节进度",
        "roles": set(LEAD_VIEW_ROLES),
        "note": "2.1 / 2.2",
    },
    {
        "code": "opportunity.write",
        "name": "推进环节 / 勾选标准 / 改金额 / 登记风险",
        "roles": set(LEAD_WRITE_ROLES),
        "note": "硬门槛未过不允许流转；勾选必须附证据",
    },
    {
        "code": "opportunity.lose",
        "name": "判定丢单 / 撤销丢单",
        "roles": set(LEAD_WRITE_ROLES),
        "note": "判丢单必须多选 R1–R6",
    },
    {
        "code": "payment.write",
        "name": "登记回款",
        "roles": {
            UserRole.ADMIN,
            UserRole.MARKET_MANAGER,
            UserRole.MARKET_STAFF,
            UserRole.BIZ_SUPPORT,
        },
        "note": "2.6；回款记录表是「已回款金额」的唯一事实来源",
    },
    {
        "code": "review.write",
        "name": "提交项目评审卡",
        "roles": {UserRole.ADMIN, UserRole.MARKET_MANAGER},
        "note": "2.7；判定能力/态度问题会同步生成预警",
    },
    # ── 报表与看板 ──
    {
        "code": "report.self",
        "name": "查看本人业绩与周报",
        "roles": set(LEAD_VIEW_ROLES),
        "note": "4.4 / 7.2",
    },
    {
        "code": "report.team",
        "name": "查看团队报表 / 周会视图",
        "roles": {UserRole.ADMIN, UserRole.MARKET_MANAGER},
        "note": "4.5 / 4.7",
    },
    {
        "code": "dashboard.executive",
        "name": "查看经营大盘",
        "roles": {UserRole.ADMIN, UserRole.EXECUTIVE},
        "note": "4.1；跨三闭环汇总",
    },
    # ── 闭环三：交付与客户成功 ──
    {
        "code": "delivery.view",
        "name": "查看客户交付",
        "roles": {
            UserRole.ADMIN,
            UserRole.DELIVERY_STAFF,
            UserRole.DELIVERY_MANAGER,
            UserRole.EXECUTIVE,
            UserRole.RND,
        },
        "note": "3.1~3.5；运营交付专员按客户归属只看自己的",
    },
    {
        "code": "delivery.write",
        "name": "维护交付数据（合同 / 交付物 / 阶段材料）",
        "roles": {UserRole.ADMIN, UserRole.DELIVERY_STAFF, UserRole.DELIVERY_MANAGER},
        "note": "勾选阶段产出材料必须附证据",
    },
    {
        "code": "delivery.manage",
        "name": "确认运营交付专员 / 配置阶段材料",
        "roles": {UserRole.ADMIN, UserRole.DELIVERY_MANAGER},
        "note": "6.6",
    },
    {
        "code": "issue.view",
        "name": "查看产品问题与承诺台账",
        "roles": {
            UserRole.ADMIN,
            UserRole.DELIVERY_STAFF,
            UserRole.DELIVERY_MANAGER,
            UserRole.EXECUTIVE,
            UserRole.RND,
        },
        "note": "3.7；产研只看派给自己的",
    },
    {
        "code": "issue.assign",
        "name": "台账派单",
        "roles": {UserRole.ADMIN, UserRole.DELIVERY_STAFF, UserRole.DELIVERY_MANAGER},
        "note": "",
    },
    {
        "code": "issue.resolve",
        "name": "处理并回复台账（含产研）",
        "roles": {
            UserRole.ADMIN,
            UserRole.DELIVERY_STAFF,
            UserRole.DELIVERY_MANAGER,
            UserRole.RND,
            UserRole.MARKET_STAFF,
            UserRole.MARKET_MANAGER,
        },
        "note": "回复客户是 C19 的关键一步",
    },
    {
        "code": "complaint.write",
        "name": "登记与处理投诉",
        "roles": {UserRole.ADMIN, UserRole.DELIVERY_STAFF, UserRole.DELIVERY_MANAGER},
        "note": "3.8；产品问题/承诺没兑现会自动进台账",
    },
    {
        "code": "complaint.escalate",
        "name": "投诉升级",
        "roles": {UserRole.ADMIN, UserRole.DELIVERY_MANAGER},
        "note": "拉市场 + 产研专项会",
    },
    {
        "code": "acceptance.write",
        "name": "登记与推进验收",
        "roles": {UserRole.ADMIN, UserRole.DELIVERY_STAFF, UserRole.DELIVERY_MANAGER},
        "note": "3.11",
    },
    {
        "code": "renewal.write",
        "name": "续费评估与结论",
        "roles": {UserRole.ADMIN, UserRole.DELIVERY_STAFF, UserRole.DELIVERY_MANAGER},
        "note": "3.12",
    },
    # ── AI 与系统设置 ──
    {
        "code": "ai.use",
        "name": "使用 AI 录入与诊断",
        "roles": set(LEAD_WRITE_ROLES),
        "note": "AI 只出草稿，必须本人确认后才落库",
    },
    {
        "code": "admin.user",
        "name": "用户管理（含重置密码）",
        "roles": {UserRole.ADMIN},
        "note": "6.1",
    },
    {"code": "admin.dict", "name": "字典与节假日维护", "roles": {UserRole.ADMIN}, "note": "6.4"},
    {
        "code": "admin.standard",
        "name": "环节标准卡配置（写）",
        #  与 app/api/opportunities.py 的 require_admin 一致：市场部管理可读不可写
        "roles": {UserRole.ADMIN},
        "note": "6.5；读取对所有登录用户开放，写操作仅管理员",
    },
    {
        "code": "admin.rule",
        "name": "预警规则配置",
        "roles": {UserRole.ADMIN, UserRole.MARKET_MANAGER},
        "note": "6.7",
    },
    {
        "code": "admin.template",
        "name": "通知模板配置",
        "roles": {UserRole.ADMIN},
        "note": "6.8",
    },
    {
        "code": "admin.adapter",
        "name": "适配配置（模型 / IM）",
        "roles": {UserRole.ADMIN},
        "note": "6.10",
    },
    {"code": "admin.log", "name": "查看操作日志", "roles": {UserRole.ADMIN}, "note": "6.9"},
]


def capability_codes_for(user_role: str) -> list[str]:
    """某角色拥有的能力 code 列表（用于登录后前端菜单兜底，不是授权依据）。"""
    return [item["code"] for item in CAPABILITIES if user_role in item["roles"]]


def role_matrix() -> list[dict[str, Any]]:
    """返回能力矩阵：每项能力 + 每个角色是否允许（布尔）。"""
    roles = [role.value for role in UserRole]
    rows: list[dict[str, Any]] = []
    for item in CAPABILITIES:
        allowed = {role.value for role in item["roles"]}
        rows.append(
            {
                "code": item["code"],
                "name": item["name"],
                "note": item["note"],
                "roles": sorted(allowed),
                "matrix": {role: role in allowed for role in roles},
            }
        )
    return rows


def role_summary() -> list[dict[str, Any]]:
    """角色清单：中文名 + 默认数据范围 + 能力数。"""
    from app.core import dicts

    rows: list[dict[str, Any]] = []
    for role in UserRole:
        scope = ROLE_DEFAULT_SCOPE.get(role, "")
        rows.append(
            {
                "role": role.value,
                "label": dicts.label_of(dicts.ROLE_LABELS, role.value),
                "default_scope": scope,
                "default_scope_label": dicts.label_of(dicts.DATA_SCOPE_LABELS, scope),
                "capability_count": len(capability_codes_for(role.value)),
            }
        )
    return rows
