from pydantic import BaseModel, EmailStr, Field

from domains.organizations.dto import OrganizationResponse
from domains.users.dto import UserResponse


class EmailRequest(BaseModel):
    email: EmailStr = Field(max_length=254)


class VerifyOtpRequest(EmailRequest):
    code: str = Field(pattern=r"^[0-9]{6}$")


class CompleteRegistrationRequest(EmailRequest):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    organization_name: str = Field(min_length=1, max_length=150)


class MessageResponse(BaseModel):
    message: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class RegistrationResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    organization: OrganizationResponse
