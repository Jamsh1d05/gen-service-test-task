import asyncio
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import AttemptStatus
from app.models import Attempt
from app.services.dispatcher import Dispatcher
from app.services.orchestrator import Action, Orchestrator, Outcome

log = logging.getLogger(__name__)


class Watchdog:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        orchestrator: Orchestrator,
        dispatcher: Dispatcher,
        interval: float,
        batch_size: int,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._orchestrator = orchestrator
        self._dispatcher = dispatcher
        self._interval = interval
        self._batch_size = batch_size

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("watchdog tick failed")
            await asyncio.sleep(self._interval)

    async def tick(self) -> int:
        async with self._sessionmaker() as session:
            expired = (
                await session.scalars(
                    select(Attempt.id)
                    .where(
                        Attempt.status.in_(AttemptStatus.active()),
                        Attempt.deadline_at < func.clock_timestamp(),
                    )
                    .order_by(Attempt.deadline_at)
                    .limit(self._batch_size)
                )
            ).all()

        handled = 0
        for attempt_id in expired:
            resolution = await self._orchestrator.resolve(
                attempt_id, Outcome.timed_out(), only_if_expired=True
            )
            if resolution.action is Action.SKIPPED:
                continue
            handled += 1
            log.info("attempt %s timed out -> %s", attempt_id, resolution.action)
            if resolution.next_attempt_id is not None:
                self._dispatcher.dispatch_in_background(resolution.next_attempt_id)
        return handled
