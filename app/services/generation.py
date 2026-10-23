import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import LedgerKind, TaskStatus
from app.domain.routing import eligible_legs
from app.models import GenModel, LedgerEntry, Task, User
from app.services.errors import InsufficientFunds, NoEligibleLeg, NotFound, TooManyActiveTasks
from app.services.orchestrator import Orchestrator


@dataclass(frozen=True, slots=True)
class CreatedTask:
    task_id: uuid.UUID
    first_attempt_id: uuid.UUID


class GenerationService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        orchestrator: Orchestrator,
        max_active_tasks: int,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._orchestrator = orchestrator
        self._max_active_tasks = max_active_tasks

    async def create_task(
        self, user_id: int, model_slug: str, prompt: str, ref_images: list[str]
    ) -> CreatedTask:
        async with self._sessionmaker() as session, session.begin():
            model = await session.scalar(select(GenModel).where(GenModel.slug == model_slug))
            if model is None:
                raise NotFound(f"model {model_slug!r}")
            legs = eligible_legs(model.legs, len(ref_images))
            if not legs:
                raise NoEligibleLeg(f"no leg of {model_slug!r} accepts {len(ref_images)} ref images")

            user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
            if user is None:
                raise NotFound(f"user {user_id}")
            active = await session.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.user_id == user_id, Task.status == TaskStatus.PROCESSING)
            )
            if active >= self._max_active_tasks:
                raise TooManyActiveTasks(f"user {user_id} already has {active} active tasks")
            if user.balance < model.price:
                raise InsufficientFunds(f"balance {user.balance} < price {model.price}")

            task = Task(
                id=uuid.uuid4(),
                user_id=user_id,
                model_id=model.id,
                prompt=prompt,
                ref_images=ref_images,
                price=model.price,
                status=TaskStatus.PROCESSING,
            )
            session.add(task)
            await session.flush() 
            session.add(
                LedgerEntry(user_id=user_id, amount=-model.price, kind=LedgerKind.CHARGE, task_id=task.id)
            )
            attempt = self._orchestrator.new_attempt(task, legs[0])
            session.add(attempt)
        return CreatedTask(task.id, attempt.id)
