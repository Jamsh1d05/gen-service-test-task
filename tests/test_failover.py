import asyncio

from app.providers.gateway import ProviderRejected, ProviderTimeout
from tests.conftest import fund_user, generate, provider_webhook


async def _start(client, container, model: str = "image-fast", tokens: int = 10) -> str:
    await fund_user(container, 1, tokens)
    r = await generate(client, 1, model)
    assert r.status_code == 202
    await container.dispatcher.drain()
    return r.json()["task_id"]


async def _task(client, task_id: str) -> dict:
    return (await client.get(f"/tasks/{task_id}")).json()


async def test_first_leg_fails_task_completes_on_second(client, container, gateway):
    task_id = await _start(client, container)

    _, alpha_id = gateway.last("alpha")
    r = await provider_webhook(client, "alpha", alpha_id, status="failed", error_type="technical")
    assert r.json()["result"] == "retrying"
    await container.dispatcher.drain()

    _, beta_id = gateway.last("beta")
    r = await provider_webhook(client, "beta", beta_id, status="completed", result_url="https://f/1")
    assert r.json()["result"] == "completed"

    task = await _task(client, task_id)
    assert task["status"] == "completed"
    assert task["result_url"] == "https://f/1"
    assert [(a["provider"], a["status"], a["error_type"]) for a in task["attempts"]] == [
        ("alpha", "failed", "technical"),
        ("beta", "completed", None),
    ]
    assert (await client.get("/users/1/balance")).json()["balance"] == 8


async def test_10_concurrent_failed_webhooks_on_last_leg_refund_once(client, container, gateway):
    task_id = await _start(client, container)
    _, alpha_id = gateway.last("alpha")
    await provider_webhook(client, "alpha", alpha_id, status="failed", error_type="technical")
    await container.dispatcher.drain()
    _, beta_id = gateway.last("beta")

    responses = await asyncio.gather(
        *(
            provider_webhook(client, "beta", beta_id, status="failed", error_type="technical")
            for _ in range(10)
        )
    )

    assert all(r.status_code == 200 for r in responses)
    assert sorted(r.json()["result"] for r in responses) == ["failed"] + ["ignored"] * 9
    task = await _task(client, task_id)
    assert task["status"] == "failed"
    balance = (await client.get("/users/1/balance")).json()
    assert balance["balance"] == 10
    assert [e["kind"] for e in balance["ledger"]].count("refund") == 1


async def test_sync_503_moves_to_next_leg(client, container, gateway):
    gateway.errors["alpha"].append(ProviderRejected("HTTP 503"))
    task_id = await _start(client, container)

    task = await _task(client, task_id)
    assert task["current_leg"]["provider"] == "beta"
    assert [(a["provider"], a["status"]) for a in task["attempts"]] == [
        ("alpha", "failed"),
        ("beta", "accepted"),
    ]


async def test_sync_failure_on_every_leg_refunds(client, container, gateway):
    gateway.errors["alpha"].append(ProviderRejected("HTTP 503"))
    gateway.errors["beta"].append(ProviderTimeout("read timeout"))
    task_id = await _start(client, container)

    task = await _task(client, task_id)
    assert task["status"] == "failed"
    assert [(a["provider"], a["status"]) for a in task["attempts"]] == [
        ("alpha", "failed"),
        ("beta", "timed_out"),
    ]
    assert (await client.get("/users/1/balance")).json()["balance"] == 10


async def test_webhook_before_202_is_matched_by_reference_id(client, container, gateway):
    """Provider calls back before we stored external_id: matched via our attempt id."""
    task_id = await _start(client, container)
    request, _ = gateway.last("alpha")
    r = await provider_webhook(
        client,
        "alpha",
        "unknown-ext",
        reference_id=request.reference_id,
        status="completed",
        result_url="https://f/2",
    )
    assert r.json()["result"] == "completed"
    assert (await _task(client, task_id))["status"] == "completed"


async def test_moderation_skips_flagged_leg_and_refunds(client, container, gateway):
    task_id = await _start(client, container, "video-long", tokens=30)
    _, beta_id = gateway.last("beta")

    r = await provider_webhook(client, "beta", beta_id, status="failed", error_type="moderation")

    assert r.json()["result"] == "failed"
    task = await _task(client, task_id)
    assert task["status"] == "failed"
    assert len(task["attempts"]) == 1  # alpha has skip_on_moderation
    assert (await client.get("/users/1/balance")).json()["balance"] == 30


async def test_payload_error_fails_without_trying_next_leg(client, container, gateway):
    task_id = await _start(client, container)
    _, alpha_id = gateway.last("alpha")

    r = await provider_webhook(client, "alpha", alpha_id, status="failed", error_type="payload")

    assert r.json()["result"] == "failed"
    task = await _task(client, task_id)
    assert task["error_type"] == "payload"
    assert len(task["attempts"]) == 1
    assert (await client.get("/users/1/balance")).json()["balance"] == 10


async def test_duplicate_completed_is_ignored(client, container, gateway):
    await _start(client, container)
    _, alpha_id = gateway.last("alpha")
    responses = await asyncio.gather(
        *(
            provider_webhook(client, "alpha", alpha_id, status="completed", result_url="https://f/3")
            for _ in range(5)
        )
    )
    assert sorted(r.json()["result"] for r in responses) == ["completed"] + ["ignored"] * 4


async def test_unknown_attempt_is_404(client):
    r = await provider_webhook(client, "alpha", "nope", status="completed", result_url="https://f")
    assert r.status_code == 404
