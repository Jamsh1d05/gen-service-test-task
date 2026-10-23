import asyncio

from tests.conftest import expire_current_attempt, fund_user, generate, provider_webhook


async def _start(client, container, model: str, tokens: int) -> str:
    await fund_user(container, 1, tokens)
    r = await generate(client, 1, model)
    await container.dispatcher.drain()
    return r.json()["task_id"]


async def _timeout_current(container, task_id: str) -> int:
    await expire_current_attempt(container, task_id)
    handled = await container.watchdog.tick()
    await container.dispatcher.drain()
    return handled


async def _task(client, task_id: str) -> dict:
    return (await client.get(f"/tasks/{task_id}")).json()


async def test_watchdog_moves_silent_attempt_to_next_leg(client, container, gateway):
    task_id = await _start(client, container, "video-long", 30)

    assert await _timeout_current(container, task_id) == 1

    task = await _task(client, task_id)
    assert task["status"] == "processing"
    assert [(a["provider"], a["status"], a["error_type"]) for a in task["attempts"]] == [
        ("beta", "timed_out", "timeout"),
        ("alpha", "accepted", None),
    ]


async def test_watchdog_in_4_workers_does_not_double_work(client, container):
    task_id = await _start(client, container, "image-fast", 10)
    await expire_current_attempt(container, task_id)

    handled = await asyncio.gather(*(container.watchdog.tick() for _ in range(4)))
    await container.dispatcher.drain()

    assert sum(handled) == 1
    assert len((await _task(client, task_id))["attempts"]) == 2


async def test_late_completed_while_task_still_running_is_accepted(client, container, gateway):
    """Rule 6, task not terminal yet: take the late result, cancel the newer attempt, no refund."""
    task_id = await _start(client, container, "video-long", 30)
    _, beta_id = gateway.last("beta")
    await _timeout_current(container, task_id)
    _, alpha_id = gateway.last("alpha")

    r = await provider_webhook(client, "beta", beta_id, status="completed", result_url="https://f/late")
    assert r.json()["result"] == "late_accepted"

    task = await _task(client, task_id)
    assert task["status"] == "completed"
    assert task["result_url"] == "https://f/late"
    assert [(a["provider"], a["status"]) for a in task["attempts"]] == [
        ("beta", "completed"),
        ("alpha", "cancelled"),
    ]
    assert task["attempts"][0]["late_result_at"] is not None

    # The cancelled attempt's own result changes nothing either.
    r = await provider_webhook(client, "alpha", alpha_id, status="completed", result_url="https://f/other")
    assert r.json()["result"] == "ignored"
    balance = (await client.get("/users/1/balance")).json()
    assert balance["balance"] == 5
    assert [e["kind"] for e in balance["ledger"]] == ["charge", "topup"]


async def test_late_completed_after_refund_is_discarded(client, container, gateway):
    """Rule 6, task already failed and refunded: the result is recorded, the task stays failed."""
    task_id = await _start(client, container, "image-fast", 10)
    _, alpha_id = gateway.last("alpha")
    await _timeout_current(container, task_id)  # alpha -> beta
    await _timeout_current(container, task_id)  # beta -> no legs left -> refund
    assert (await _task(client, task_id))["status"] == "failed"

    responses = await asyncio.gather(
        *(
            provider_webhook(client, "alpha", alpha_id, status="completed", result_url="https://f/late")
            for _ in range(3)
        )
    )
    assert {r.json()["result"] for r in responses} == {"late_discarded"}

    task = await _task(client, task_id)
    assert task["status"] == "failed"
    assert task["result_url"] is None
    assert task["attempts"][0]["late_result_at"] is not None
    balance = (await client.get("/users/1/balance")).json()
    assert balance["balance"] == 10
    assert [e["kind"] for e in balance["ledger"]] == ["refund", "charge", "topup"]


async def test_watchdog_skips_attempt_resolved_meanwhile(client, container, gateway):
    task_id = await _start(client, container, "image-fast", 10)
    _, alpha_id = gateway.last("alpha")
    await expire_current_attempt(container, task_id)
    await provider_webhook(client, "alpha", alpha_id, status="completed", result_url="https://f/ok")

    assert await container.watchdog.tick() == 0
    assert (await _task(client, task_id))["status"] == "completed"
