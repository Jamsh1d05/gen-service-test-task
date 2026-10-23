import asyncio
import json
import os
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app import security
from app.config import Settings
from app.container import Container
from app.domain.enums import PaymentStatus
from app.main import create_app
from app.providers.gateway import ProviderError, SubmitRequest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql+asyncpg://gen:gen@localhost:55432/gen_test")
SECRET = "test-secret"


@dataclass
class FakeGateway:
    submitted: list[tuple[str, SubmitRequest, str]] = field(default_factory=list)
    errors: dict[str, list[ProviderError]] = field(default_factory=lambda: defaultdict(list))

    async def submit(self, provider: str, request: SubmitRequest) -> str:
        if self.errors[provider]:
            raise self.errors[provider].pop(0)
        external_id = f"{provider}-{uuid.uuid4().hex[:8]}"
        self.submitted.append((provider, request, external_id))
        return external_id

    def last(self, provider: str) -> tuple[SubmitRequest, str]:
        request, external_id = next((r, e) for p, r, e in reversed(self.submitted) if p == provider)
        return request, external_id

    async def aclose(self) -> None:
        pass


async def _ensure_database() -> None:
    url = make_url(TEST_DATABASE_URL)
    conn = await asyncpg.connect(
        host=url.host, port=url.port, user=url.username, password=url.password, database="postgres"
    )
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", url.database)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
async def migrated_db() -> str:
    await _ensure_database()
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    # env.py calls asyncio.run itself, so it must not run on this event loop.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    return TEST_DATABASE_URL


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
async def app(migrated_db: str, gateway: FakeGateway):
    settings = Settings(
        database_url=migrated_db,
        webhook_secret=SECRET,
        watchdog_enabled=False,  # tests drive the watchdog explicitly via tick()
        provider_timeout=60,
        db_pool_size=30,
        db_max_overflow=10,
    )
    application = create_app(settings, gateway)
    async with application.router.lifespan_context(application):
        container: Container = application.state.container
        async with container.engine.begin() as conn:
            await conn.execute(text("TRUNCATE ledger, payments, attempts, tasks, users CASCADE"))
        yield application
        await container.dispatcher.drain()
        await _assert_balances_match_ledger(container)


async def _assert_balances_match_ledger(container: Container) -> None:
    # Rule 8, checked after every test.
    async with container.engine.connect() as conn:
        mismatches = (
            await conn.execute(
                text(
                    """
                    SELECT u.id, u.balance, COALESCE(SUM(l.amount), 0) AS journal
                    FROM users u LEFT JOIN ledger l ON l.user_id = u.id
                    GROUP BY u.id HAVING u.balance <> COALESCE(SUM(l.amount), 0)
                    """
                )
            )
        ).all()
    assert mismatches == [], f"balance != sum(ledger): {mismatches}"


@pytest.fixture
def container(app) -> Container:
    return app.state.container


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def signed_post(client: httpx.AsyncClient, url: str, payload: dict) -> httpx.Response:
    body = json.dumps(payload).encode()
    return await client.post(
        url,
        content=body,
        headers={"X-Signature": security.sign(body, SECRET), "Content-Type": "application/json"},
    )


async def fund_user(container: Container, user_id: int, tokens: int) -> None:
    await container.payments.handle(f"seed-{uuid.uuid4()}", user_id, tokens, PaymentStatus.SUCCEEDED)


async def generate(client: httpx.AsyncClient, user_id: int, model: str, refs: int = 0) -> httpx.Response:
    return await client.post(
        "/generate",
        json={
            "user_id": user_id,
            "model_slug": model,
            "prompt": "a cat",
            "ref_images": [f"https://img/{i}.png" for i in range(refs)],
        },
    )


async def provider_webhook(
    client: httpx.AsyncClient, provider: str, external_id: str, **fields
) -> httpx.Response:
    return await signed_post(
        client, f"/webhooks/providers/{provider}", {"external_id": external_id, **fields}
    )


async def expire_current_attempt(container: Container, task_id: str) -> None:
    async with container.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE attempts SET deadline_at = now() - interval '1 second' "
                "WHERE id = (SELECT current_attempt_id FROM tasks WHERE id = :id)"
            ),
            {"id": task_id},
        )
