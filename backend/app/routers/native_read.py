from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..native_read import ERRORS, NativeReadError, read_directory

router = APIRouter(prefix="/chat", tags=["chat"])


class ReadIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: str = Field(min_length=1, max_length=2000)


@router.post("/directory")
async def directory(body: ReadIn):
    try:
        return (await read_directory(body.message)).model_dump()
    except NativeReadError as exc:
        status = 400 if exc.code == "unsupported_request" else 504 if exc.code == "timeout" else 503
        return JSONResponse({"error": {"code": exc.code, "message": ERRORS[exc.code]}}, status_code=status)
