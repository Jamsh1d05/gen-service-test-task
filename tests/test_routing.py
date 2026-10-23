from dataclasses import dataclass

from app.domain.enums import ErrorType
from app.domain.routing import eligible_legs, next_leg_after_failure


@dataclass
class L:
    name: str
    priority: int
    max_ref_images: int
    skip_on_moderation: bool = False


IMAGE_FAST = [L("beta", 2, 10), L("alpha", 1, 4)]
VIDEO_LONG = [L("beta", 1, 2), L("alpha", 2, 1, skip_on_moderation=True)]
CHAIN = [L("a", 1, 5), L("b", 2, 5, skip_on_moderation=True), L("c", 3, 5)]


def names(legs) -> list[str]:
    return [leg.name for leg in legs]


def test_eligible_legs_are_filtered_by_ref_images_and_sorted():
    assert names(eligible_legs(IMAGE_FAST, 0)) == ["alpha", "beta"]
    assert names(eligible_legs(IMAGE_FAST, 5)) == ["beta"]
    assert eligible_legs(IMAGE_FAST, 11) == []


def test_technical_goes_to_next_leg():
    assert next_leg_after_failure(IMAGE_FAST, 0, 1, ErrorType.TECHNICAL).name == "beta"


def test_timeout_behaves_like_technical():
    assert next_leg_after_failure(VIDEO_LONG, 0, 1, ErrorType.TIMEOUT).name == "alpha"


def test_moderation_skips_flagged_legs():
    assert next_leg_after_failure(VIDEO_LONG, 0, 1, ErrorType.MODERATION) is None
    assert next_leg_after_failure(CHAIN, 0, 1, ErrorType.MODERATION).name == "c"
    assert next_leg_after_failure(CHAIN, 0, 1, ErrorType.TECHNICAL).name == "b"


def test_payload_stops_immediately():
    assert next_leg_after_failure(IMAGE_FAST, 0, 1, ErrorType.PAYLOAD) is None


def test_next_leg_must_also_fit_ref_images():
    # video-long with 2 refs: alpha (max 1) does not fit, so beta's failure is final.
    assert next_leg_after_failure(VIDEO_LONG, 2, 1, ErrorType.TECHNICAL) is None


def test_last_leg_failure_gives_up():
    assert next_leg_after_failure(IMAGE_FAST, 0, 2, ErrorType.TECHNICAL) is None
