from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from core.dependencies import bearer_scheme, get_auth_service
from core.responses import ApiResponse, success
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
    Unauthorized,
)
from domains.auth.service import AuthService
from domains.organizations.dto import OrganizationResponse
from domains.organizations.model import OrganizationRole
from domains.users.dto import UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register/start", response_model=ApiResponse[MessageResponse])
async def start_registration(
    payload: EmailRequest,
    service: AuthService = Depends(get_auth_service),
) -> ApiResponse[MessageResponse]:
    try:
        await service.start_registration(str(payload.email))
    except AccountExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return success(MessageResponse(message="Registration OTP sent"), "Registration OTP sent")


@router.post("/register/verify", response_model=ApiResponse[MessageResponse])
async def verify_registration(
    payload: VerifyOtpRequest,
    service: AuthService = Depends(get_auth_service),
) -> ApiResponse[MessageResponse]:
    try:
        await service.verify_registration(str(payload.email), payload.code)
    except InvalidOtp as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success(
        MessageResponse(message="Registration OTP validated"), "Registration OTP validated"
    )


@router.post(
    "/register/complete", response_model=ApiResponse[RegistrationResponse], status_code=201
)
async def complete_registration(
    payload: CompleteRegistrationRequest,
    service: AuthService = Depends(get_auth_service),
) -> ApiResponse[RegistrationResponse]:
    try:
        session = await service.complete_registration(
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
    organization = session.registration.organization
    data = RegistrationResponse(
        access_token=session.access_token,
        user=UserResponse.model_validate(session.registration.user),
        organization=OrganizationResponse(
            id=organization.id,
            name=organization.name,
            role=OrganizationRole.ADMIN,
            created_at=organization.created_at,
        ),
    )
    return success(data, "Registration completed", 201)


@router.post("/login/start", response_model=ApiResponse[MessageResponse])
async def start_login(
    payload: EmailRequest,
    service: AuthService = Depends(get_auth_service),
) -> ApiResponse[MessageResponse]:
    try:
        await service.start_login(str(payload.email))
    except AccountNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(MessageResponse(message="Login OTP sent"), "Login OTP sent")


@router.post("/login/verify", response_model=ApiResponse[LoginResponse])
async def verify_login(
    payload: VerifyOtpRequest,
    service: AuthService = Depends(get_auth_service),
) -> ApiResponse[LoginResponse]:
    try:
        authentication = await service.verify_login(str(payload.email), payload.code)
    except InvalidOtp as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AccountNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    data = LoginResponse(
        access_token=authentication.access_token,
        user=UserResponse.model_validate(authentication.user),
    )
    return success(data, "Login successful")


@router.post("/refresh", response_model=ApiResponse[LoginResponse])
async def refresh(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    service: AuthService = Depends(get_auth_service),
) -> ApiResponse[LoginResponse]:
    try:
        authentication = await service.refresh(credentials.credentials if credentials else None)
    except Unauthorized as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    data = LoginResponse(
        access_token=authentication.access_token,
        user=UserResponse.model_validate(authentication.user),
    )
    return success(data, "Token refreshed")
