from pydantic import BaseModel, Field


class PrintResultRequest(BaseModel):
    success: bool
    error: str | None = Field(default=None, max_length=2000)
