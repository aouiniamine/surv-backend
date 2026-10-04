from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from core.dependencies import current_user_id, get_user_service
from domains.users.dto import UserResponse
from domains.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse)
async def get_me(
    user_id: UUID = Depends(current_user_id),
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    user = await service.get_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    return UserResponse.model_validate(user)
