"""denumire articol pe invoice_line (separat de descriere)

Revision ID: 57bd6c26330f
Revises: 32391cd4bb1d
Create Date: 2026-07-28 12:00:00.000000

Scris manual (nu autogenerate): Alembic nu poate diff-ui expresii pe coloane
GENERATED ALWAYS AS -- descriere_tsv trebuie dropată și recreată, nu alterată.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '57bd6c26330f'
down_revision: Union[str, None] = '32391cd4bb1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('invoice_line', sa.Column('denumire', sa.Text(), nullable=True))

    # Backfill istoric: pana acum "descriere" servea si ca denumire (parserul
    # vechi cadea pe cbc:Name doar cand lipsea cbc:Description) -- e cea mai
    # buna aproximare disponibila pentru randurile deja importate.
    op.execute("UPDATE invoice_line SET denumire = descriere WHERE denumire IS NULL")

    op.drop_index('ix_invoice_line_descriere_tsv', table_name='invoice_line')
    op.drop_column('invoice_line', 'descriere_tsv')
    op.add_column(
        'invoice_line',
        sa.Column(
            'descriere_tsv',
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('romanian', coalesce(denumire, '') || ' ' || coalesce(descriere, ''))",
                persisted=True,
            ),
            nullable=True,
        ),
    )
    op.create_index(
        'ix_invoice_line_descriere_tsv', 'invoice_line', ['descriere_tsv'], unique=False,
        postgresql_using='gin',
    )


def downgrade() -> None:
    op.drop_index('ix_invoice_line_descriere_tsv', table_name='invoice_line')
    op.drop_column('invoice_line', 'descriere_tsv')
    op.add_column(
        'invoice_line',
        sa.Column(
            'descriere_tsv',
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('romanian', coalesce(descriere, ''))", persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        'ix_invoice_line_descriere_tsv', 'invoice_line', ['descriere_tsv'], unique=False,
        postgresql_using='gin',
    )
    op.drop_column('invoice_line', 'denumire')
