"""add campaign pod fields

Revision ID: e969a382d4b8
Revises: 4834564aec8f
Create Date: 2026-11-10 03:33:26.616137

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e969a382d4b8'
down_revision: Union[str, Sequence[str], None] = '4834564aec8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        "campaigns",
        sa.Column(
            "duration_seconds",
            sa.Integer(),
            nullable=False,
        ),
    )

    op.add_column(
        "campaigns",
        sa.Column(
            "competitive_group",
            sa.String(length=100),
            nullable=True,
        ),
    )

    op.create_check_constraint(
        "ck_campaign_duration_positive",
        "campaigns",
        "duration_seconds > 0",
    )

def downgrade() -> None:
    """Downgrade schema."""

    op.drop_constraint(
        "ck_campaign_duration_positive",
        "campaigns",
        type_="check",
    )

    op.drop_column(
        "campaigns",
        "competitive_group",
    )

    op.drop_column(
        "campaigns",
        "duration_seconds",
    )
