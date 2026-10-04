from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from domains.organizations.model import OrganizationRole


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    role: OrganizationRole
    created_at: datetime
