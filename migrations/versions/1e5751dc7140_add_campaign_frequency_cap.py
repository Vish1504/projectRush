"""add campaign frequency cap

Revision ID: 1e5751dc7140
Revises: 1f5949987e3d
Create Date: 2026-10-05 16:49:03.499784

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e5751dc7140'
down_revision: Union[str, Sequence[str], None] = '1f5949987e3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add the column temporarily allowing NULL.
    op.add_column(
        "campaigns",
        sa.Column(
            "frequency_cap_per_hour",
            sa.Integer(),
            nullable=True,
        ),
    )

    # 2. Existing campaigns were created before frequency caps existed.
    # Give those development rows a sensible initial value.
    op.execute(
        """
        UPDATE campaigns
        SET frequency_cap_per_hour = 3
        WHERE frequency_cap_per_hour IS NULL
        """
    )

    # 3. Now that every row has a value, make the column mandatory.
    op.alter_column(
        "campaigns",
        "frequency_cap_per_hour",
        nullable=False,
    )

    # 4. PostgreSQL itself should reject invalid values.
    op.create_check_constraint(
        "ck_campaign_frequency_cap_positive",
        "campaigns",
        "frequency_cap_per_hour > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_campaign_frequency_cap_positive",
        "campaigns",
        type_="check",
    )

    op.drop_column(
        "campaigns",
        "frequency_cap_per_hour",
    )