from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from domains.agent.model import AgentChange, AgentDraft, AgentEvent, AgentRun


class CreateAgentRunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    skill_id: str | None = Field(default=None, max_length=80)


class PublishAgentDraftRequest(BaseModel):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class AgentRunResponse(BaseModel):
    id: UUID
    project_id: UUID
    status: str
    provider_id: str
    model_id: str
    skill_id: str | None
    skill_version: str | None
    error_message: str | None
    draft_revision: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_run(cls, run: AgentRun) -> "AgentRunResponse":
        return cls(**{name: getattr(run, name) for name in cls.model_fields})


class AgentEventResponse(BaseModel):
    sequence: int
    kind: str
    content: str
    created_at: datetime

    @classmethod
    def from_event(cls, event: AgentEvent) -> "AgentEventResponse":
        return cls(**{name: getattr(event, name) for name in cls.model_fields})


class AgentChangeResponse(BaseModel):
    id: UUID
    path: str
    change_type: str
    has_diff: bool

    @classmethod
    def from_change(cls, change: AgentChange) -> "AgentChangeResponse":
        return cls(
            id=change.id,
            path=change.path,
            change_type=change.change_type,
            has_diff=change.diff_text is not None,
        )


class AgentChangeDetailResponse(AgentChangeResponse):
    diff_text: str | None

    @classmethod
    def from_change(cls, change: AgentChange) -> "AgentChangeDetailResponse":
        return cls(
            id=change.id,
            path=change.path,
            change_type=change.change_type,
            has_diff=change.diff_text is not None,
            diff_text=change.diff_text,
        )


class AgentDraftResponse(BaseModel):
    revision: str
    source_run_id: UUID
    published: bool
    created_at: datetime
    preview_url: str

    @classmethod
    def from_draft(cls, draft: AgentDraft, preview_url: str) -> "AgentDraftResponse":
        return cls(
            revision=draft.revision,
            source_run_id=draft.source_run_id,
            published=draft.published_revision == draft.revision,
            created_at=draft.created_at,
            preview_url=preview_url,
        )
