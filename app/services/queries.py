import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import LedgerEntry, Task, User
from app.services.errors import NotFound


@dataclass(frozen=True, slots=True)
class Balance:
    user: User
    ledger: list[LedgerEntry]


class Queries:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def task(self, task_id: uuid.UUID) -> Task:
        async with self._sessionmaker() as session:
            task = await session.get(Task, task_id)
        if task is None:
            raise NotFound(f"task {task_id}")
        return task

    async def balance(self, user_id: int, limit: int) -> Balance:
        async with self._sessionmaker() as session:
            user = await session.get(User, user_id)
            if user is None:
                raise NotFound(f"user {user_id}")
            entries = await session.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.user_id == user_id)
                .order_by(LedgerEntry.id.desc())
                .limit(limit)
            )
            return Balance(user, list(entries))
