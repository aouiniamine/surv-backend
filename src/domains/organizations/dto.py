from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from domains.organizations.model import OrganizationRole


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    role: OrganizationRole
    created_at: datetime


class CreateOrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
