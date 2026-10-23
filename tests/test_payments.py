import asyncio

from sqlalchemy import func, select

from app.models import LedgerEntry
from tests.conftest import signed_post

PAYMENT = {"payment_id": "pay-1", "user_id": 42, "tokens": 100, "status": "succeeded"}


async def test_20_concurrent_identical_payments_credit_once(client, container):
    responses = await asyncio.gather(*(signed_post(client, "/webhooks/payments", PAYMENT) for _ in range(20)))

    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    results = sorted(r.json()["result"] for r in responses)
    assert results == ["already_credited"] * 19 + ["credited"]

    balance = (await client.get("/users/42/balance")).json()
    assert balance["balance"] == 100
    assert [(e["kind"], e["amount"]) for e in balance["ledger"]] == [("topup", 100)]

    async with container.sessionmaker() as s:
        count = await s.scalar(
            select(func.count()).select_from(LedgerEntry).where(LedgerEntry.payment_id == "pay-1")
        )
    assert count == 1


async def test_pending_then_succeeded_credits_once(client):
    pending = {**PAYMENT, "status": "pending"}
    assert (await signed_post(client, "/webhooks/payments", pending)).json()["result"] == "recorded"
    assert (await signed_post(client, "/webhooks/payments", PAYMENT)).json()["result"] == "credited"
    assert (await signed_post(client, "/webhooks/payments", pending)).json()["result"] == "already_credited"
    assert (await client.get("/users/42/balance")).json()["balance"] == 100


async def test_redelivery_with_different_amount_is_rejected(client):
    await signed_post(client, "/webhooks/payments", PAYMENT)
    r = await signed_post(client, "/webhooks/payments", {**PAYMENT, "tokens": 1000})
    assert r.status_code == 409
    assert (await client.get("/users/42/balance")).json()["balance"] == 100


async def test_bad_signature_is_rejected(client):
    r = await client.post("/webhooks/payments", json=PAYMENT, headers={"X-Signature": "nope"})
    assert r.status_code == 401
    assert (await client.get("/users/42/balance")).status_code == 404
