from dataclasses import dataclass

from domains.organizations.model import Organization
from domains.users.model import User


@dataclass(frozen=True, slots=True)
class Registration:
    user: User
    organization: Organization


@dataclass(frozen=True, slots=True)
class Authentication:
    access_token: str
    user: User


@dataclass(frozen=True, slots=True)
class RegistrationSession:
    access_token: str
    registration: Registration
