from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from domains.projects.model import Project


class CreateProjectRequest(BaseModel):
    organization_id: UUID
    name: str = Field(min_length=1, max_length=120)


class ProjectResponse(BaseModel):
    id: UUID
    organization_id: UUID
    name: str
    public_id: str
    status: str
    app_url: str
    created_at: datetime

    @classmethod
    def from_project(cls, project: Project, app_url: str) -> "ProjectResponse":
        return cls(
            id=project.id,
            organization_id=project.organization_id,
            name=project.name,
            public_id=project.public_id,
            status=project.status,
            app_url=app_url,
            created_at=project.created_at,
        )


class ProjectBackupResponse(BaseModel):
    id: UUID
    created_at: datetime
    preview_url: str
