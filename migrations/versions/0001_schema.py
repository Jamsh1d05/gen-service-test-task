"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-10-23
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_STATEMENTS = [
    """
    CREATE TABLE users (
        id          BIGINT PRIMARY KEY,
        balance     BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE gen_models (
        id     SERIAL PRIMARY KEY,
        slug   TEXT NOT NULL UNIQUE,
        price  BIGINT NOT NULL CHECK (price > 0)
    )
    """,
    """
    CREATE TABLE legs (
        id                  SERIAL PRIMARY KEY,
        model_id            INT NOT NULL REFERENCES gen_models(id) ON DELETE CASCADE,
        provider            TEXT NOT NULL,
        priority            INT NOT NULL,
        max_ref_images      INT NOT NULL CHECK (max_ref_images >= 0),
        skip_on_moderation  BOOLEAN NOT NULL DEFAULT false,
        UNIQUE (model_id, priority)
    )
    """,
    """
    CREATE TABLE tasks (
        id                  UUID PRIMARY KEY,
        user_id             BIGINT NOT NULL REFERENCES users(id),
        model_id            INT NOT NULL REFERENCES gen_models(id),
        prompt              TEXT NOT NULL,
        ref_images          JSONB NOT NULL DEFAULT '[]',
        price               BIGINT NOT NULL,
        status              TEXT NOT NULL
                            CHECK (status IN ('processing', 'completed', 'failed')),
        current_attempt_id  UUID,
        result_url          TEXT,
        error_type          TEXT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    -- 429 check counts active tasks per user.
    CREATE INDEX ix_tasks_user_active ON tasks (user_id) WHERE status = 'processing'
    """,
    """
    CREATE TABLE attempts (
        id               UUID PRIMARY KEY,
        task_id          UUID NOT NULL REFERENCES tasks(id),
        leg_id           INT NOT NULL REFERENCES legs(id),
        provider         TEXT NOT NULL,
        status           TEXT NOT NULL CHECK (status IN
                         ('sending', 'accepted', 'completed', 'failed', 'timed_out', 'cancelled')),
        external_id      TEXT,
        error_type       TEXT,
        -- clock_timestamp: attempts created in one transaction still get ordered times.
        started_at       TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        deadline_at      TIMESTAMPTZ NOT NULL,
        finished_at      TIMESTAMPTZ,
        late_result_url  TEXT,
        late_result_at   TIMESTAMPTZ
    )
    """,
    """
    CREATE INDEX ix_attempts_task ON attempts (task_id, started_at)
    """,
    """
    CREATE UNIQUE INDEX ux_attempts_external ON attempts (provider, external_id)
        WHERE external_id IS NOT NULL
    """,
    """
    -- A task never has two attempts in flight at once.
    CREATE UNIQUE INDEX ux_attempts_one_active ON attempts (task_id)
        WHERE status IN ('sending', 'accepted')
    """,
    """
    -- Watchdog scan.
    CREATE INDEX ix_attempts_deadline ON attempts (deadline_at)
        WHERE status IN ('sending', 'accepted')
    """,
    """
    ALTER TABLE tasks ADD CONSTRAINT fk_tasks_current_attempt
        FOREIGN KEY (current_attempt_id) REFERENCES attempts(id) DEFERRABLE INITIALLY DEFERRED
    """,
    """
    CREATE TABLE payments (
        payment_id   TEXT PRIMARY KEY,
        user_id      BIGINT NOT NULL REFERENCES users(id),
        tokens       BIGINT NOT NULL CHECK (tokens > 0),
        status       TEXT NOT NULL,
        credited_at  TIMESTAMPTZ,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE ledger (
        id          BIGSERIAL PRIMARY KEY,
        user_id     BIGINT NOT NULL REFERENCES users(id),
        amount      BIGINT NOT NULL CHECK (amount <> 0),
        kind        TEXT NOT NULL CHECK (kind IN ('topup', 'charge', 'refund')),
        task_id     UUID REFERENCES tasks(id),
        payment_id  TEXT REFERENCES payments(payment_id),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (
            (kind = 'topup'  AND payment_id IS NOT NULL AND task_id IS NULL AND amount > 0) OR
            (kind = 'charge' AND task_id IS NOT NULL AND payment_id IS NULL AND amount < 0) OR
            (kind = 'refund' AND task_id IS NOT NULL AND payment_id IS NULL AND amount > 0)
        )
    )
    """,
    """
    CREATE INDEX ix_ledger_user ON ledger (user_id, id DESC)
    """,
    """
    CREATE UNIQUE INDEX ux_ledger_topup ON ledger (payment_id) WHERE kind = 'topup'
    """,
    """
    CREATE UNIQUE INDEX ux_ledger_task_kind ON ledger (task_id, kind)
        WHERE kind IN ('charge', 'refund')
    """,
    """
    CREATE FUNCTION ledger_apply_to_balance() RETURNS trigger AS $$
    BEGIN
        UPDATE users SET balance = balance + NEW.amount WHERE id = NEW.user_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'ledger entry for unknown user %', NEW.user_id;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_ledger_balance AFTER INSERT ON ledger
        FOR EACH ROW EXECUTE FUNCTION ledger_apply_to_balance()
    """,
    """
    CREATE FUNCTION ledger_forbid_change() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'ledger is append-only';
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_ledger_immutable BEFORE UPDATE OR DELETE ON ledger
        FOR EACH ROW EXECUTE FUNCTION ledger_forbid_change()
    """,
]


def upgrade() -> None:
    # asyncpg cannot execute multi-statement strings.
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "DROP TABLE ledger",
        "DROP FUNCTION ledger_forbid_change",
        "DROP FUNCTION ledger_apply_to_balance",
        "DROP TABLE payments",
        "ALTER TABLE tasks DROP CONSTRAINT fk_tasks_current_attempt",
        "DROP TABLE attempts",
        "DROP TABLE tasks",
        "DROP TABLE legs",
        "DROP TABLE gen_models",
        "DROP TABLE users",
    ):
        op.execute(statement)
