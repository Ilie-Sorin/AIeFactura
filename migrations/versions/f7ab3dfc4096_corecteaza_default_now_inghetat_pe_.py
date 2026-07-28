"""corecteaza default now() inghetat pe coloane timestamp

Revision ID: f7ab3dfc4096
Revises: 57bd6c26330f
Create Date: 2026-07-28 23:07:41.481898

`server_default="now()"` in modelele SQLAlchemy era un string Python simplu,
nu `sa.text("now()")` -- Postgres tratează un DEFAULT dat ca literal (nu ca
apel de funcție) evaluându-l O SINGURĂ DATĂ, la rularea DDL-ului, nu per rând
la fiecare INSERT. Rezultat: toate coloanele astea au căpătat un default
ÎNGHEȚAT la un moment fix din trecut (data la care a rulat `alembic upgrade
head`), nu ora reală a inserării. Modelele au fost corectate să folosească
`sa.text("now()")`; migrarea de față repară coloanele deja create.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7ab3dfc4096'
down_revision: Union[str, None] = '57bd6c26330f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLOANE = [
    ("app_user", "creat_la"),
    ("audit_log", "moment"),
    ("import_batch", "pornit_la"),
    ("integrity_alert", "generat_la"),
    ("integrity_alert", "actualizat_la"),
    ("invoice", "creat_la"),
    ("invoice_relation", "creat_la"),
    ("invoice_source_link", "creat_la"),
    ("numbering_rule", "creat_la"),
    ("reconciliation_run", "rulat_la"),
    ("source_object", "creat_la"),
]


def upgrade() -> None:
    for tabel, coloana in COLOANE:
        op.alter_column(tabel, coloana, server_default=sa.text("now()"))


def downgrade() -> None:
    # Nu exista o stare "corecta" anterioara de restaurat -- defaultul inghetat
    # era un bug, nu un comportament intentionat.
    pass
