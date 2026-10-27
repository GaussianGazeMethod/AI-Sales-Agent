"""助手会话接口：按已绑定skill执行权限内业务，返回实际执行回执。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_lead_write
from app.database import get_db
from app.models.user import User
from app.services import assistant_service

router = APIRouter(prefix="/ai/conversations", tags=["AI 助手会话"])


class ConversationCreate(BaseModel):
    lead_id: str | None = None


class MessageCreate(BaseModel):
    request_key: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=12000)
    lead_id: str | None = None


@router.post("")
async def create(
    payload: ConversationCreate,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    return await assistant_service.create_conversation(db, user=user, lead_id=payload.lead_id)


@router.get("/{conversation_id}")
async def read(
    conversation_id: str,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    return await assistant_service.read_conversation(db, user=user, conversation_id=conversation_id)


@router.post("/{conversation_id}/messages")
async def message(
    conversation_id: str,
    payload: MessageCreate,
    user: User = Depends(require_lead_write),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    return await assistant_service.send_message(
        db,
        user=user,
        conversation_id=conversation_id,
        **payload.model_dump(),
    )
