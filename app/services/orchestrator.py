import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import AttemptStatus, ErrorType, LedgerKind, TaskStatus
from app.domain.routing import next_leg_after_failure
from app.models import Attempt, LedgerEntry, Leg, Task
from app.services.errors import NotFound

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Outcome:
    status: AttemptStatus
    error_type: ErrorType | None = None
    result_url: str | None = None
    external_id: str | None = None

    @classmethod
    def completed(cls, result_url: str, external_id: str | None = None) -> "Outcome":
        return cls(AttemptStatus.COMPLETED, result_url=result_url, external_id=external_id)

    @classmethod
    def failed(cls, error_type: ErrorType, external_id: str | None = None) -> "Outcome":
        return cls(AttemptStatus.FAILED, error_type=error_type, external_id=external_id)

    @classmethod
    def timed_out(cls) -> "Outcome":
        return cls(AttemptStatus.TIMED_OUT, error_type=ErrorType.TIMEOUT)


class Action(StrEnum):
    COMPLETED = "completed"
    RETRYING = "retrying"
    FAILED = "failed"
    LATE_ACCEPTED = "late_accepted"
    LATE_DISCARDED = "late_discarded"
    IGNORED = "ignored"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Resolution:
    action: Action
    next_attempt_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class DispatchInfo:
    attempt_id: uuid.UUID
    status: str
    provider: str
    model_slug: str
    prompt: str
    ref_images: list[str]


class Orchestrator:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], provider_timeout: float) -> None:
        self._sessionmaker = sessionmaker
        self._provider_timeout = timedelta(seconds=provider_timeout)

    def new_attempt(self, task: Task, leg: Leg) -> Attempt:
        attempt = Attempt(
            id=uuid.uuid4(),
            task_id=task.id,
            leg_id=leg.id,
            provider=leg.provider,
            status=AttemptStatus.SENDING,
            deadline_at=func.clock_timestamp() + self._provider_timeout,
        )
        task.current_attempt_id = attempt.id
        return attempt

    async def find_attempt_id(self, provider: str, external_id: str, reference_id: str | None) -> uuid.UUID:
        match = Attempt.external_id == external_id
        if reference_id := _parse_uuid(reference_id):
            match = or_(match, Attempt.id == reference_id)

        async with self._sessionmaker() as session:
            attempt_id = await session.scalar(
                select(Attempt.id).where(Attempt.provider == provider, match).limit(1)
            )
        if attempt_id is None:
            raise NotFound(f"no attempt for {provider}/{external_id}")
        return attempt_id

    async def dispatch_info(self, attempt_id: uuid.UUID) -> DispatchInfo | None:
        async with self._sessionmaker() as session:
            attempt = await session.get(Attempt, attempt_id)
            if attempt is None:
                return None
            task = await session.get_one(Task, attempt.task_id)
            return DispatchInfo(
                attempt_id=attempt.id,
                status=attempt.status,
                provider=attempt.provider,
                model_slug=task.model.slug,
                prompt=task.prompt,
                ref_images=list(task.ref_images),
            )

    async def mark_accepted(self, attempt_id: uuid.UUID, external_id: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                update(Attempt)
                .where(Attempt.id == attempt_id, Attempt.status == AttemptStatus.SENDING)
                .values(status=AttemptStatus.ACCEPTED, external_id=external_id)
            )

    async def resolve(
        self, attempt_id: uuid.UUID, outcome: Outcome, *, only_if_expired: bool = False
    ) -> Resolution:
        async with self._sessionmaker() as session, session.begin():
            task = await self._lock_task(session, attempt_id, skip_locked=only_if_expired)
            if task is None:
                return Resolution(Action.SKIPPED)
            if only_if_expired and not await self._is_expired(session, attempt_id):
                return Resolution(Action.SKIPPED)

            attempt = next(a for a in task.attempts if a.id == attempt_id)
            if outcome.external_id and attempt.external_id is None:
                attempt.external_id = outcome.external_id

            if attempt.status in AttemptStatus.active():
                return await self._resolve_active(session, task, attempt, outcome)
            if attempt.status == AttemptStatus.TIMED_OUT and outcome.status == AttemptStatus.COMPLETED:
                return self._resolve_late_completion(task, attempt, outcome)

            log.info("ignoring %s for attempt %s in status %s", outcome.status, attempt.id, attempt.status)
            return Resolution(Action.IGNORED)

    async def _lock_task(
        self, session: AsyncSession, attempt_id: uuid.UUID, skip_locked: bool
    ) -> Task | None:
        task_id = await session.scalar(select(Attempt.task_id).where(Attempt.id == attempt_id))
        if task_id is None:
            raise NotFound(f"attempt {attempt_id}")
        return await session.scalar(
            select(Task).where(Task.id == task_id).with_for_update(of=Task, skip_locked=skip_locked)
        )

    async def _is_expired(self, session: AsyncSession, attempt_id: uuid.UUID) -> bool:
        found = await session.scalar(
            select(Attempt.id).where(
                Attempt.id == attempt_id,
                Attempt.status.in_(AttemptStatus.active()),
                Attempt.deadline_at < func.clock_timestamp(),
            )
        )
        return found is not None

    async def _resolve_active(
        self, session: AsyncSession, task: Task, attempt: Attempt, outcome: Outcome
    ) -> Resolution:
        attempt.status = outcome.status
        attempt.error_type = outcome.error_type
        attempt.finished_at = func.clock_timestamp()

        if outcome.status == AttemptStatus.COMPLETED:
            _complete(task, outcome.result_url)
            return Resolution(Action.COMPLETED)

        assert outcome.error_type is not None
        leg = next_leg_after_failure(
            task.model.legs, len(task.ref_images), attempt.leg.priority, outcome.error_type
        )
        if leg is None:
            _fail_and_refund(session, task, outcome.error_type)
            return Resolution(Action.FAILED)

        # The old attempt must leave the "one active attempt" unique index before the next is inserted.
        await session.flush()
        next_attempt = self.new_attempt(task, leg)
        session.add(next_attempt)
        task.updated_at = func.now()
        return Resolution(Action.RETRYING, next_attempt.id)

    @staticmethod
    def _resolve_late_completion(task: Task, attempt: Attempt, outcome: Outcome) -> Resolution:
        """Rule 6: never both a result and a refund (see README)."""
        attempt.late_result_url = outcome.result_url
        attempt.late_result_at = func.clock_timestamp()

        if task.status != TaskStatus.PROCESSING:
            log.warning("late completion for finished task %s discarded", task.id)
            return Resolution(Action.LATE_DISCARDED)

        current = next((a for a in task.attempts if a.id == task.current_attempt_id), None)
        if current is not None and current.status in AttemptStatus.active():
            current.status = AttemptStatus.CANCELLED
            current.finished_at = func.clock_timestamp()

        attempt.status = AttemptStatus.COMPLETED
        task.current_attempt_id = attempt.id
        _complete(task, outcome.result_url)
        return Resolution(Action.LATE_ACCEPTED)


def _complete(task: Task, result_url: str | None) -> None:
    task.status = TaskStatus.COMPLETED
    task.result_url = result_url
    task.updated_at = func.now()


def _fail_and_refund(session: AsyncSession, task: Task, error_type: ErrorType) -> None:
    task.status = TaskStatus.FAILED
    task.error_type = error_type
    task.updated_at = func.now()
    session.add(LedgerEntry(user_id=task.user_id, amount=task.price, kind=LedgerKind.REFUND, task_id=task.id))


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None
