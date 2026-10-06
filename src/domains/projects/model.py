from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Project:
    id: UUID
    organization_id: UUID
    name: str
    public_id: str
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectBackup:
    id: UUID
    archive_path: str
    created_at: datetime
