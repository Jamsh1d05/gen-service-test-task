"""Seed the starting model catalog.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO gen_models (slug, price) VALUES ('image-fast', 2), ('video-long', 25)
        """
    )
    op.execute(
        """
        INSERT INTO legs (model_id, provider, priority, max_ref_images, skip_on_moderation)
        SELECT m.id, l.provider, l.priority, l.max_ref_images, l.skip_on_moderation
        FROM (VALUES
            ('image-fast', 'alpha', 1, 4,  false),
            ('image-fast', 'beta',  2, 10, false),
            ('video-long', 'beta',  1, 2,  false),
            ('video-long', 'alpha', 2, 1,  true)
        ) AS l(slug, provider, priority, max_ref_images, skip_on_moderation)
        JOIN gen_models m ON m.slug = l.slug
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM legs WHERE model_id IN
            (SELECT id FROM gen_models WHERE slug IN ('image-fast', 'video-long'))
        """
    )
    op.execute(
        """
        DELETE FROM gen_models WHERE slug IN ('image-fast', 'video-long')
        """
    )
