from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiResponse[T](BaseModel):
    success: bool
    message: str
    data: T | None
    code: str


def success[T](data: T, message: str, status_code: int = 200) -> ApiResponse[T]:
    return ApiResponse(success=True, message=message, data=data, code=HTTPStatus(status_code).name)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "message": message,
                "data": None,
                "code": HTTPStatus(exc.status_code).name,
            },
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "message": "Invalid request data",
                "data": None,
                "code": "UNPROCESSABLE_ENTITY",
            },
        )

    @app.exception_handler(Exception)
    async def server_error(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Internal server error",
                "data": None,
                "code": "INTERNAL_SERVER_ERROR",
            },
        )
