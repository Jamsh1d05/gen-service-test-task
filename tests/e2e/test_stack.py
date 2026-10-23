"""docker compose up -d && pytest -m e2e"""

import asyncio
import json
import os
import random
import time

import httpx
import pytest

from app import security

pytestmark = pytest.mark.e2e

APP = os.getenv("E2E_APP_URL", "http://localhost:8000")
PROVIDERS = {
    "alpha": os.getenv("E2E_ALPHA_URL", "http://localhost:8001"),
    "beta": os.getenv("E2E_BETA_URL", "http://localhost:8002"),
}
SECRET = os.getenv("WEBHOOK_SECRET", "dev-secret")
FAST = {
    "min_delay": 0.3,
    "max_delay": 1.0,
    "failure_rate": 0,
    "failure_type": "technical",
    "sync_failure_rate": 0,
    "duplicate_rate": 0,
    "drop_rate": 0,
    "late_rate": 0,
}


@pytest.fixture
async def http():
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.get(f"{APP}/docs")
        except httpx.HTTPError:
            pytest.skip(f"stack not reachable at {APP}")
        await _behave(client, alpha={}, beta={})
        yield client
        await _behave(client, alpha={}, beta={})


async def _behave(client: httpx.AsyncClient, **per_provider: dict) -> None:
    for name, overrides in per_provider.items():
        (await client.put(f"{PROVIDERS[name]}/_behavior", json={**FAST, **overrides})).raise_for_status()


async def _pay(client, payment_id: str, user_id: int, tokens: int) -> httpx.Response:
    body = json.dumps(
        {"payment_id": payment_id, "user_id": user_id, "tokens": tokens, "status": "succeeded"}
    ).encode()
    return await client.post(
        f"{APP}/webhooks/payments",
        content=body,
        headers={"X-Signature": security.sign(body, SECRET), "Content-Type": "application/json"},
    )


async def _new_user(client, tokens: int) -> int:
    user_id = random.randint(10**9, 2 * 10**9)
    (await _pay(client, f"e2e-{user_id}", user_id, tokens)).raise_for_status()
    return user_id


async def _wait_terminal(client, task_id: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = (await client.get(f"{APP}/tasks/{task_id}")).json()
        if task["status"] != "processing":
            return task
        await asyncio.sleep(0.3)
    raise AssertionError(f"task {task_id} still processing: {task}")


async def _generate(client, user_id: int, model: str = "image-fast") -> str:
    r = await client.post(f"{APP}/generate", json={"user_id": user_id, "model_slug": model, "prompt": "e2e"})
    assert r.status_code == 202, r.text
    return r.json()["task_id"]


async def test_concurrent_payments_across_workers_credit_once(http):
    user_id = random.randint(10**9, 2 * 10**9)
    responses = await asyncio.gather(*(_pay(http, f"e2e-pay-{user_id}", user_id, 50) for _ in range(20)))
    assert sorted(r.json()["result"] for r in responses) == ["already_credited"] * 19 + ["credited"]
    assert (await http.get(f"{APP}/users/{user_id}/balance")).json()["balance"] == 50


async def test_happy_path(http):
    user_id = await _new_user(http, 10)
    task = await _wait_terminal(http, await _generate(http, user_id))
    assert task["status"] == "completed"
    assert task["result_url"].startswith("https://")
    assert (await http.get(f"{APP}/users/{user_id}/balance")).json()["balance"] == 8


async def test_failover_to_second_leg(http):
    await _behave(http, alpha={"failure_rate": 1})
    user_id = await _new_user(http, 10)
    task = await _wait_terminal(http, await _generate(http, user_id))
    assert task["status"] == "completed"
    assert [(a["provider"], a["status"]) for a in task["attempts"]] == [
        ("alpha", "failed"),
        ("beta", "completed"),
    ]


async def test_all_legs_fail_with_duplicate_webhooks_refund_once(http):
    await _behave(http, alpha={"sync_failure_rate": 1}, beta={"failure_rate": 1, "duplicate_rate": 1})
    user_id = await _new_user(http, 10)
    task = await _wait_terminal(http, await _generate(http, user_id))
    assert task["status"] == "failed"
    await asyncio.sleep(1)  # let the duplicate webhook land
    ledger = (await http.get(f"{APP}/users/{user_id}/balance")).json()
    assert ledger["balance"] == 10
    assert [e["kind"] for e in ledger["ledger"]].count("refund") == 1
