import asyncio
import hashlib
import hmac
import json
import logging
import random
import uuid
from collections.abc import Coroutine
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Response, status
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("fake_provider")

Rate = Field(0.0, ge=0, le=1)
ERROR_TYPES = ("technical", "moderation", "payload")


class Behavior(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FAKE_")

    min_delay: float = 2.0
    max_delay: float = 20.0
    failure_rate: float = Rate
    failure_type: Literal["technical", "moderation", "payload", "random"] = "technical"
    sync_failure_rate: float = Rate
    sync_failure_mode: Literal["503", "timeout"] = "503"
    sync_timeout_seconds: float = 30.0
    duplicate_rate: float = Rate
    drop_rate: float = Rate
    late_rate: float = Rate
    late_delay: float = 90.0


class Settings(BaseSettings):
    provider_name: str = "alpha"
    webhook_secret: str = "dev-secret"


class TaskIn(BaseModel):
    reference_id: str
    model: str
    prompt: str
    ref_images: list[str] = []
    callback_url: str


class State:
    def __init__(self) -> None:
        self.settings = Settings()
        self.behavior = Behavior()
        self.client = httpx.AsyncClient(timeout=10)
        self.background: set[asyncio.Task[None]] = set()

    def spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)


state = State()
app = FastAPI(title=f"fake provider {state.settings.provider_name}")


@app.get("/_behavior")
async def get_behavior() -> Behavior:
    return state.behavior


@app.put("/_behavior")
async def put_behavior(overrides: dict[str, Any]) -> Behavior:
    state.behavior = Behavior.model_validate({**state.behavior.model_dump(), **overrides})
    return state.behavior


@app.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
async def create_task(task: TaskIn, response: Response) -> dict[str, str]:
    behavior = state.behavior
    external_id = f"{state.settings.provider_name}-{uuid.uuid4().hex[:12]}"

    sync_failure = _roll(behavior.sync_failure_rate)

    if sync_failure and behavior.sync_failure_mode == "503":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"error": "unavailable"}

    state.spawn(_deliver(task, external_id, behavior))
    if sync_failure:
        # The task is processed anyway: the caller cannot tell whether it was accepted.
        await asyncio.sleep(behavior.sync_timeout_seconds)
    return {"external_id": external_id}


async def _deliver(task: TaskIn, external_id: str, behavior: Behavior) -> None:
    if _roll(behavior.drop_rate):
        log.info("dropping webhook for %s", external_id)
        return

    late = _roll(behavior.late_rate)
    await asyncio.sleep(
        behavior.late_delay if late else random.uniform(behavior.min_delay, behavior.max_delay)
    )

    body = json.dumps(_result(task, external_id, behavior, late)).encode()
    copies = 2 if _roll(behavior.duplicate_rate) else 1
    await asyncio.gather(*(_post(task.callback_url, body) for _ in range(copies)))


def _result(task: TaskIn, external_id: str, behavior: Behavior, late: bool) -> dict[str, str]:
    result = {"external_id": external_id, "reference_id": task.reference_id}
    if not late and _roll(behavior.failure_rate):
        error = random.choice(ERROR_TYPES) if behavior.failure_type == "random" else behavior.failure_type
        return result | {"status": "failed", "error_type": error}
    return result | {"status": "completed", "result_url": f"https://files.example/{external_id}.bin"}


async def _post(url: str, body: bytes) -> None:
    signature = hmac.new(state.settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    try:
        response = await state.client.post(
            url, content=body, headers={"X-Signature": signature, "Content-Type": "application/json"}
        )
        log.info("webhook %s -> %s", url, response.status_code)
    except httpx.HTTPError as exc:
        log.warning("webhook %s failed: %s", url, exc)


def _roll(rate: float) -> bool:
    return random.random() < rate
