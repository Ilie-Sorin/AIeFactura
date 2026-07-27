import datetime as dt

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class NumberingRule(Base):
    """Regulă de normalizare a numărului de factură, configurabilă per furnizor
    (cap. 5). `cif_emitent = NULL` reprezintă regula implicită globală."""

    __tablename__ = "numbering_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    cif_emitent: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    descriere: Mapped[str | None] = mapped_column(String(255), nullable=True)
    configuratie: Mapped[dict] = mapped_column(JSONB, default=dict)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    creat_la: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
