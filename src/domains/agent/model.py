from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from domains.organizations.model import OrganizationRole


@dataclass(frozen=True, slots=True)
class AgentProject:
    id: UUID
    public_id: str
    status: str
    role: OrganizationRole


@dataclass(frozen=True, slots=True)
class AgentRun:
    id: UUID
    project_id: UUID
    created_by: UUID
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


@dataclass(frozen=True, slots=True)
class AgentEvent:
    sequence: int
    kind: str
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AgentChange:
    id: UUID
    path: str
    change_type: str
    diff_text: str | None


@dataclass(frozen=True, slots=True)
class AgentDraft:
    revision: str
    source_run_id: UUID
    published_revision: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    id: UUID
    project_id: UUID
    created_by: UUID
    prompt: str
    provider_id: str
    model_id: str
    skill_id: str | None
    skill_version: str | None
