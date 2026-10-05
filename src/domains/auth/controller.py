from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from core.dependencies import bearer_scheme, get_auth_service
from domains.auth.dto import (
    CompleteRegistrationRequest,
    EmailRequest,
    LoginResponse,
    MessageResponse,
    RegistrationResponse,
    VerifyOtpRequest,
)
from domains.auth.errors import (
    AccountExists,
    AccountNotFound,
    InvalidOtp,
    InvalidProfileFields,
    RegistrationNotValidated,
)
from domains.auth.service import AuthService
from domains.organizations.dto import OrganizationResponse
from domains.organizations.model import OrganizationRole
from domains.users.dto import UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register/start", response_model=MessageResponse)
async def start_registration(
    payload: EmailRequest,
    service: AuthService = Depends(get_auth_service),
) -> MessageResponse:
    try:
        await service.start_registration(str(payload.email))
    except AccountExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MessageResponse(message="Registration OTP sent")


@router.post("/register/verify", response_model=MessageResponse)
async def verify_registration(
    payload: VerifyOtpRequest,
    service: AuthService = Depends(get_auth_service),
) -> MessageResponse:
    try:
        await service.verify_registration(str(payload.email), payload.code)
    except InvalidOtp as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MessageResponse(message="Registration OTP validated")


@router.post("/register/complete", response_model=RegistrationResponse, status_code=201)
async def complete_registration(
    payload: CompleteRegistrationRequest,
    service: AuthService = Depends(get_auth_service),
) -> RegistrationResponse:
    try:
        registration = await service.complete_registration(
            str(payload.email),
            payload.first_name,
            payload.last_name,
            payload.organization_name,
        )
    except RegistrationNotValidated as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidProfileFields as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AccountExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    organization = registration.organization
    return RegistrationResponse(
        user=UserResponse.model_validate(registration.user),
        organization=OrganizationResponse(
            id=organization.id,
            name=organization.name,
            role=OrganizationRole.ADMIN,
            created_at=organization.created_at,
        ),
    )


@router.post("/login/start", response_model=MessageResponse)
async def start_login(
    payload: EmailRequest,
    service: AuthService = Depends(get_auth_service),
) -> MessageResponse:
    try:
        await service.start_login(str(payload.email))
    except AccountNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MessageResponse(message="Login OTP sent")


@router.post("/login/verify", response_model=LoginResponse)
async def verify_login(
    payload: VerifyOtpRequest,
    service: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    try:
        access_token = await service.verify_login(str(payload.email), payload.code)
    except InvalidOtp as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AccountNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return LoginResponse(access_token=access_token)
