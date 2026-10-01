"""add campaign constraints

Revision ID: d51009978bca
Revises: 8d25c8112b3d
Create Date: 2026-09-28 19:36:45.323826

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd51009978bca'
down_revision: Union[str, Sequence[str], None] = '8d25c8112b3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# def upgrade() -> None:
#     """Upgrade schema."""
#     pass

def upgrade() -> None:
    op.create_check_constraint(
        "ck_campaign_capacity_positive",
        "campaigns",
        "capacity > 0",
    )

    op.create_check_constraint(
        "ck_campaign_time_window",
        "campaigns",
        "end_time > start_time",
    )


# def downgrade() -> None:
#     """Downgrade schema."""
#     pass

def downgrade() -> None:
    op.drop_constraint(
        "ck_campaign_time_window",
        "campaigns",
        type_="check",
    )

    op.drop_constraint(
        "ck_campaign_capacity_positive",
        "campaigns",
        type_="check",
    )
