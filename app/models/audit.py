import datetime as dt

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AuditLog(Base):
    """Jurnal de operații (cap. 12): cine, când, ce a importat/anulat/exportat/reconciliat."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    moment: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), index=True)
    utilizator_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    actiune: Mapped[str] = mapped_column(String(50), index=True)
    entitate: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entitate_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    detalii: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
