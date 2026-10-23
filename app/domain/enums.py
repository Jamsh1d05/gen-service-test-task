from enum import StrEnum


class TaskStatus(StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class AttemptStatus(StrEnum):
    SENDING = "sending"  # sent the request, waiting for providers answer
    ACCEPTED = "accepted"  # provider answered 202, waiting for webhook
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"  # stopped waiting; provider may still deliver
    CANCELLED = "cancelled"  # task finished through another attempt

    @classmethod
    def active(cls) -> tuple["AttemptStatus", ...]:
        return cls.SENDING, cls.ACCEPTED


class ErrorType(StrEnum):
    TECHNICAL = "technical"
    MODERATION = "moderation"
    PAYLOAD = "payload"
    # Assigned by us, never sent by providers.
    TIMEOUT = "timeout"


class LedgerKind(StrEnum):
    TOPUP = "topup"
    CHARGE = "charge"
    REFUND = "refund"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
