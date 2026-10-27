"""Authorized SQL truth adapter for Agently's native bounded evidence reader."""

from agently.types.data import (
    ContextSourceDescriptor,
    ContextSourceDescriptorPage,
    ContextSourceRead,
)
from sqlalchemy import select

from app.core.errors import ConflictError, PermissionDeniedError
from app.models.activity import FollowUp
from app.services import lead_service


class ProjectEvidenceSource:
    source_kind = "project_followups"

    def __init__(self, *, db, user, lead_id, revision, descriptors):
        self.db, self.user, self.lead_id = db, user, lead_id
        self.revision = revision
        self.source_id = "project-followups:" + lead_id
        self._descriptors = descriptors
        self._refs = {d["key"]: d for d in descriptors}

    @property
    def source_revision(self):
        return str(self.revision)

    async def async_enumerate_descriptors(self, *, profile, cursor, limit):
        start = int(cursor or 0)
        selected = self._descriptors[start : start + limit]
        return ContextSourceDescriptorPage(
            source_id=self.source_id,
            source_revision=self.source_revision,
            descriptors=tuple(
                ContextSourceDescriptor(
                    descriptor_key=d["key"],
                    source_id=self.source_id,
                    source_revision=self.source_revision,
                    source_ref=d["key"],
                    role="information",
                    title="项目跟进 " + d["occurred_at"],
                    summary="可按需读取的原始跟进",
                    estimated_chars=d["chars"],
                )
                for d in selected
            ),
            next_cursor=str(start + limit) if start + limit < len(self._descriptors) else None,
        )

    async def async_read_exact(self, source_ref, *, max_chars, representation=None, range_start=0):
        from app.services import dossier_service

        descriptor = self._refs.get(source_ref)
        if not descriptor:
            raise PermissionDeniedError("未授权的项目证据引用")
        lead = await lead_service.get_lead(self.db, self.lead_id)
        await lead_service.assert_can_view_lead(self.db, self.user, lead)
        dossier = await dossier_service.get(self.db, self.lead_id)
        current = dossier.revision if dossier else None
        if current != self.revision:
            raise ConflictError("证据快照已变化，不能读取新版本替代本次依据")
        row = (
            await self.db.execute(
                select(FollowUp).where(
                    FollowUp.id == descriptor["id"],
                    FollowUp.lead_id == self.lead_id,
                )
            )
        ).scalar_one()
        text = row.content
        await self.db.commit()  # No DB transaction remains while the model runs.
        end = range_start + max_chars
        return ContextSourceRead(
            source_id=self.source_id,
            source_revision=self.source_revision,
            source_ref=source_ref,
            content=text[range_start:end],
            completeness="complete" if end >= len(text) else "truncated",
            next_range_start=end if end < len(text) else None,
            metadata={"occurred_at": descriptor["occurred_at"]},
        )
