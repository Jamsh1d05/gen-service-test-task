from collections.abc import Iterable
from typing import Protocol, TypeVar

from app.domain.enums import ErrorType


class LegLike(Protocol):
    priority: int
    max_ref_images: int
    skip_on_moderation: bool


L = TypeVar("L", bound=LegLike)


def eligible_legs(legs: Iterable[L], ref_images_count: int) -> list[L]:
    return sorted(
        (leg for leg in legs if leg.max_ref_images >= ref_images_count),
        key=lambda leg: leg.priority,
    )


def next_leg_after_failure(
    legs: Iterable[L],
    ref_images_count: int,
    failed_priority: int,
    error: ErrorType,
) -> L | None:
    """Next leg to try, or None if the task must fail. Timeouts count as technical."""
    if error is ErrorType.PAYLOAD:
        return None
    for leg in eligible_legs(legs, ref_images_count):
        if leg.priority <= failed_priority:
            continue
        if error is ErrorType.MODERATION and leg.skip_on_moderation:
            continue
        return leg
    return None
