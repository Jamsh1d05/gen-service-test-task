import asyncio

from tests.conftest import fund_user, generate


async def test_generate_charges_and_dispatches_to_first_leg(client, container, gateway):
    await fund_user(container, 1, 10)

    r = await generate(client, 1, "image-fast")
    assert r.status_code == 202
    task_id = r.json()["task_id"]
    await container.dispatcher.drain()

    task = (await client.get(f"/tasks/{task_id}")).json()
    assert task["status"] == "processing"
    assert task["current_leg"]["provider"] == "alpha"
    assert task["attempts"][0]["status"] == "accepted"
    assert gateway.last("alpha")[0].reference_id == task["attempts"][0]["id"]

    balance = (await client.get("/users/1/balance")).json()
    assert balance["balance"] == 8
    assert balance["ledger"][0]["kind"] == "charge"


async def test_ref_images_select_the_leg(client, container, gateway):
    await fund_user(container, 1, 10)
    r = await generate(client, 1, "image-fast", refs=7)  # alpha takes max 4
    await container.dispatcher.drain()
    task = (await client.get(f"/tasks/{r.json()['task_id']}")).json()
    assert task["current_leg"]["provider"] == "beta"


async def test_no_eligible_leg_is_400_without_charge(client, container):
    await fund_user(container, 1, 10)
    r = await generate(client, 1, "image-fast", refs=11)
    assert r.status_code == 400
    assert (await client.get("/users/1/balance")).json()["balance"] == 10


async def test_insufficient_funds_is_402(client, container):
    await fund_user(container, 1, 10)
    r = await generate(client, 1, "video-long")
    assert r.status_code == 402
    assert (await client.get("/users/1/balance")).json()["balance"] == 10


async def test_unknown_model_and_user_are_404(client, container):
    await fund_user(container, 1, 10)
    assert (await generate(client, 1, "nope")).status_code == 404
    assert (await generate(client, 999, "image-fast")).status_code == 404


async def test_active_task_limit_holds_under_concurrency(client, container):
    await fund_user(container, 1, 100)

    responses = await asyncio.gather(*(generate(client, 1, "image-fast") for _ in range(8)))

    codes = sorted(r.status_code for r in responses)
    assert codes == [202] * 3 + [429] * 5
    assert (await client.get("/users/1/balance")).json()["balance"] == 100 - 3 * 2
