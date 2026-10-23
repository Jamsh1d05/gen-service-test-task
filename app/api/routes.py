import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import ContainerDep, SignedBody, parse_body
from app.api.schemas import (
    AttemptOut,
    BalanceOut,
    CurrentLeg,
    GenerateRequest,
    GenerateResponse,
    LedgerOut,
    PaymentWebhook,
    ProviderWebhook,
    TaskOut,
    WebhookAck,
)
from app.domain.enums import ErrorType
from app.services.orchestrator import Outcome

router = APIRouter()


@router.post("/generate", response_model=GenerateResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate(body: GenerateRequest, container: ContainerDep) -> GenerateResponse:
    created = await container.generation.create_task(
        body.user_id, body.model_slug, body.prompt, body.ref_images
    )
    container.dispatcher.dispatch_in_background(created.first_attempt_id)
    return GenerateResponse(task_id=created.task_id)


@router.post("/webhooks/providers/{name}", response_model=WebhookAck)
async def provider_webhook(name: str, raw: SignedBody, container: ContainerDep) -> WebhookAck:
    event = parse_body(ProviderWebhook, raw)
    attempt_id = await container.orchestrator.find_attempt_id(name, event.external_id, event.reference_id)
    if event.status == "completed":
        assert event.result_url is not None
        outcome = Outcome.completed(event.result_url, event.external_id)
    else:
        outcome = Outcome.failed(ErrorType(event.error_type), event.external_id)
    resolution = await container.orchestrator.resolve(attempt_id, outcome)
    if resolution.next_attempt_id is not None:
        container.dispatcher.dispatch_in_background(resolution.next_attempt_id)
    return WebhookAck(result=resolution.action)


@router.post("/webhooks/payments", response_model=WebhookAck)
async def payment_webhook(raw: SignedBody, container: ContainerDep) -> WebhookAck:
    event = parse_body(PaymentWebhook, raw)
    result = await container.payments.handle(event.payment_id, event.user_id, event.tokens, event.status)
    return WebhookAck(result=result)


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def get_task(task_id: uuid.UUID, container: ContainerDep) -> TaskOut:
    task = await container.queries.task(task_id)
    current = next((a for a in task.attempts if a.id == task.current_attempt_id), None)
    return TaskOut(
        id=task.id,
        status=task.status,
        model_slug=task.model.slug,
        result_url=task.result_url,
        error_type=task.error_type,
        current_leg=CurrentLeg(
            attempt_id=current.id, provider=current.provider, priority=current.leg.priority
        )
        if current
        else None,
        attempts=[AttemptOut.model_validate(a) for a in task.attempts],
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


@router.get("/users/{user_id}/balance", response_model=BalanceOut)
async def get_balance(
    user_id: int, container: ContainerDep, limit: int = Query(20, ge=1, le=200)
) -> BalanceOut:
    balance = await container.queries.balance(user_id, limit)
    return BalanceOut(
        user_id=balance.user.id,
        balance=balance.user.balance,
        ledger=[LedgerOut.model_validate(e) for e in balance.ledger],
    )
