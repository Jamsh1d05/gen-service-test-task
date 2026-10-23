from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import LedgerKind, PaymentStatus
from app.models import LedgerEntry, Payment, User
from app.services.errors import PaymentConflict


class PaymentResult(StrEnum):
    CREDITED = "credited"
    ALREADY_CREDITED = "already_credited"
    RECORDED = "recorded" 


class PaymentService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def handle(
        self, payment_id: str, user_id: int, tokens: int, status: PaymentStatus
    ) -> PaymentResult:
        async with self._sessionmaker() as session, session.begin():
            # Users are external; the account is created on first payment.
            await session.execute(insert(User).values(id=user_id).on_conflict_do_nothing())
            await session.execute(
                insert(Payment)
                .values(payment_id=payment_id, user_id=user_id, tokens=tokens, status=status)
                .on_conflict_do_nothing(index_elements=[Payment.payment_id])
            )
            payment = await session.scalar(
                select(Payment).where(Payment.payment_id == payment_id).with_for_update()
            )
            assert payment is not None
            if payment.user_id != user_id or payment.tokens != tokens:
                raise PaymentConflict(f"payment {payment_id} redelivered with different user/amount")

            if payment.credited_at is not None:
                return PaymentResult.ALREADY_CREDITED

            payment.status = status
            payment.updated_at = func.now()
            if status != PaymentStatus.SUCCEEDED:
                return PaymentResult.RECORDED

            payment.credited_at = func.now()
            session.add(
                LedgerEntry(user_id=user_id, amount=tokens, kind=LedgerKind.TOPUP, payment_id=payment_id)
            )
            return PaymentResult.CREDITED
