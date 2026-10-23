from dataclasses import asdict, dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True, slots=True)
class SubmitRequest:
    # Our attempt id, echoed back in the webhook in case external_id is unknown to us.
    reference_id: str
    model: str
    prompt: str
    ref_images: list[str]
    callback_url: str


class ProviderError(Exception):
    pass


class ProviderRejected(ProviderError):
    """The provider definitely did not accept the task."""


class ProviderTimeout(ProviderError):
    """The provider may or may not have accepted the task."""


class ProviderGateway(Protocol):
    async def submit(self, provider: str, request: SubmitRequest) -> str: ...

    async def aclose(self) -> None: ...


class HttpProviderGateway:
    def __init__(self, base_urls: dict[str, str], timeout: float) -> None:
        self._base_urls = base_urls
        self._client = httpx.AsyncClient(timeout=timeout)

    async def submit(self, provider: str, request: SubmitRequest) -> str:
        base_url = self._base_urls.get(provider)
        if base_url is None:
            raise ProviderRejected(f"unknown provider {provider!r}")
        try:
            response = await self._client.post(f"{base_url}/tasks", json=asdict(request))
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderRejected(str(exc)) from exc
        if response.status_code != 202:
            raise ProviderRejected(f"HTTP {response.status_code}")
        return str(response.json()["external_id"])

    async def aclose(self) -> None:
        await self._client.aclose()
