"""模型包 — 集中导入所有模型，确保它们注册到 Base.metadata。

 新增模型后必须在此登记，否则 `create_all` 不会建表。
"""

from app.models.activity import Assignment, FollowUp, MarketTarget, Reminder
from app.models.ai import AiDiagnosis, AiDraft
from app.models.assistant import AssistantConversation, AssistantTurn
from app.models.base import Base
from app.models.customer import Customer
from app.models.delivery import (
    AcceptanceRecord,
    Contract,
    DeliveryItem,
    DeliveryPhaseStandard,
    DeliveryStage,
    HandoverSheet,
    RenewalAssessment,
    StandardServiceItem,
)
from app.models.dossier import AssistantAction, ProjectDossier, ProjectFactEvent
from app.models.lead import Contact, Lead
from app.models.operation import (
    Complaint,
    FaqItem,
    OperationMetric,
    OperationRecord,
    ProductIssue,
)
from app.models.report import (
    PaymentRecord,
    ProjectReview,
    RuleConfig,
    WeeklyReport,
)
from app.models.stage import (
    AmountChange,
    RiskAlert,
    StageCompletion,
    StageSalesConfirmation,
    StageStandard,
    StageTransition,
    StandardJudgement,
)
from app.models.system import Holiday, ImMessage, ImportBatch, Notification, OperationLog
from app.models.user import Department, User

__all__ = [
    "AssistantAction",
    "ProjectDossier",
    "ProjectFactEvent",
    "AssistantConversation",
    "AssistantTurn",
    "AcceptanceRecord",
    "Complaint",
    "Contract",
    "DeliveryItem",
    "DeliveryPhaseStandard",
    "DeliveryStage",
    "FaqItem",
    "HandoverSheet",
    "OperationMetric",
    "OperationRecord",
    "ProductIssue",
    "RenewalAssessment",
    "StandardServiceItem",
    "AmountChange",
    "AiDiagnosis",
    "AiDraft",
    "Assignment",
    "Base",
    "Contact",
    "Customer",
    "Department",
    "FollowUp",
    "Holiday",
    "ImportBatch",
    "ImMessage",
    "Lead",
    "MarketTarget",
    "Notification",
    "OperationLog",
    "PaymentRecord",
    "ProjectReview",
    "Reminder",
    "RiskAlert",
    "RuleConfig",
    "StageCompletion",
    "StageSalesConfirmation",
    "StageStandard",
    "StageTransition",
    "StandardJudgement",
    "User",
    "WeeklyReport",
]
