import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import ErrorType, PaymentStatus


class GenerateRequest(BaseModel):
    user_id: int
    model_slug: str
    prompt: str = Field(min_length=1)
    ref_images: list[str] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    task_id: uuid.UUID


class ProviderWebhook(BaseModel):
    external_id: str
    reference_id: str | None = None
    status: Literal["completed", "failed"]
    result_url: str | None = None
    error_type: Literal[ErrorType.TECHNICAL, ErrorType.MODERATION, ErrorType.PAYLOAD] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status == "completed" and not self.result_url:
            raise ValueError("completed webhook requires result_url")
        if self.status == "failed" and self.error_type is None:
            raise ValueError("failed webhook requires error_type")
        return self


class PaymentWebhook(BaseModel):
    payment_id: str = Field(min_length=1)
    user_id: int
    tokens: int = Field(gt=0)
    status: PaymentStatus


class WebhookAck(BaseModel):
    result: str


class AttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    status: str
    error_type: str | None
    external_id: str | None
    started_at: datetime
    finished_at: datetime | None
    late_result_at: datetime | None


class CurrentLeg(BaseModel):
    attempt_id: uuid.UUID
    provider: str
    priority: int


class TaskOut(BaseModel):
    id: uuid.UUID
    status: str
    model_slug: str
    result_url: str | None
    error_type: str | None
    current_leg: CurrentLeg | None
    attempts: list[AttemptOut]
    created_at: datetime
    updated_at: datetime


class LedgerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    amount: int
    kind: str
    task_id: uuid.UUID | None
    payment_id: str | None
    created_at: datetime


class BalanceOut(BaseModel):
    user_id: int
    balance: int
    ledger: list[LedgerOut]
