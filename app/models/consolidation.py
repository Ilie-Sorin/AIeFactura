import datetime as dt
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class InvoiceRelation(Base):
    """Legătură între două documente — cap. 6. O rulare ulterioară a regulilor
    nu suprascrie niciodată o decizie manuală (impus la nivel de serviciu, nu DB)."""

    __tablename__ = "invoice_relation"
    __table_args__ = (
        CheckConstraint("sursa IN ('xml', 'regula', 'manual')", name="sursa_valida"),
        CheckConstraint("stare IN ('confirmata', 'propusa', 'respinsa')", name="stare_valida"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_from: Mapped[int] = mapped_column(ForeignKey("invoice.id"), index=True)
    invoice_to: Mapped[int] = mapped_column(ForeignKey("invoice.id"), index=True)
    tip: Mapped[str] = mapped_column(String(30))
    sursa: Mapped[str] = mapped_column(String(10))
    scor: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    stare: Mapped[str] = mapped_column(String(15), default="propusa")
    motiv: Mapped[str | None] = mapped_column(Text, nullable=True)
    utilizator_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    creat_la: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default="now()")


class InvoiceGroup(Base):
    """Grup de documente legate — unitatea comparată la reconciliere, nu factura individuală."""

    __tablename__ = "invoice_group"

    id: Mapped[int] = mapped_column(primary_key=True)
    tip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pozitie_neta: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    calculat_la: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    members: Mapped[list["InvoiceGroupMember"]] = relationship(back_populates="group", cascade="all, delete-orphan")


class InvoiceGroupMember(Base):
    __tablename__ = "invoice_group_member"
    __table_args__ = (CheckConstraint("semn IN (1, -1)", name="semn_valid"),)

    group_id: Mapped[int] = mapped_column(ForeignKey("invoice_group.id"), primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id"), primary_key=True)
    semn: Mapped[int] = mapped_column(SmallInteger, default=1)

    group: Mapped["InvoiceGroup"] = relationship(back_populates="members")
