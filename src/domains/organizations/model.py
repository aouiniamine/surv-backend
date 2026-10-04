from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class OrganizationRole(StrEnum):
    ADMIN = "ADMIN"
    DEVELOPER = "DEVELOPER"
    QA = "QA"


@dataclass(frozen=True, slots=True)
class Organization:
    id: UUID
    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class UserOrganization:
    id: UUID
    name: str
    role: OrganizationRole
    created_at: datetime
