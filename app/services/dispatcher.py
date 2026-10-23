import asyncio
import logging
import uuid

from app.domain.enums import AttemptStatus, ErrorType
from app.providers.gateway import ProviderGateway, ProviderRejected, ProviderTimeout, SubmitRequest
from app.services.orchestrator import Orchestrator, Outcome

log = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, orchestrator: Orchestrator, gateway: ProviderGateway, callback_base_url: str) -> None:
        self._orchestrator = orchestrator
        self._gateway = gateway
        self._callback_base_url = callback_base_url.rstrip("/")
        self._background: set[asyncio.Task[None]] = set()

    def dispatch_in_background(self, attempt_id: uuid.UUID) -> None:
        task = asyncio.create_task(self.dispatch(attempt_id))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def dispatch(self, attempt_id: uuid.UUID) -> None:
        # Runs detached; an attempt stuck in 'sending' is picked up by the watchdog.
        try:
            await self._dispatch_chain(attempt_id)
        except Exception:
            log.exception("dispatch of attempt %s failed", attempt_id)

    async def _dispatch_chain(self, attempt_id: uuid.UUID | None) -> None:
        while attempt_id is not None:
            info = await self._orchestrator.dispatch_info(attempt_id)
            if info is None or info.status != AttemptStatus.SENDING:
                return
            request = SubmitRequest(
                reference_id=str(info.attempt_id),
                model=info.model_slug,
                prompt=info.prompt,
                ref_images=info.ref_images,
                callback_url=f"{self._callback_base_url}/webhooks/providers/{info.provider}",
            )
            try:
                external_id = await self._gateway.submit(info.provider, request)
            except ProviderTimeout:
                # The provider may still deliver, so a late result goes through rule 6.
                log.warning("submit of %s to %s timed out", attempt_id, info.provider)
                outcome = Outcome.timed_out()
            except ProviderRejected as exc:
                log.warning("submit of %s to %s rejected: %s", attempt_id, info.provider, exc)
                outcome = Outcome.failed(ErrorType.TECHNICAL)
            else:
                await self._orchestrator.mark_accepted(attempt_id, external_id)
                return
            resolution = await self._orchestrator.resolve(attempt_id, outcome)
            attempt_id = resolution.next_attempt_id

    async def drain(self) -> None:
        while self._background:
            await asyncio.gather(*self._background, return_exceptions=True)

    async def aclose(self, grace: float = 5.0) -> None:
        try:
            await asyncio.wait_for(self.drain(), grace)
        except TimeoutError:
            for task in list(self._background):
                task.cancel()
            await asyncio.gather(*self._background, return_exceptions=True)
