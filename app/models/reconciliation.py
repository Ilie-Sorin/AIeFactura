import datetime as dt
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ImportProfile(Base):
    """Profil de import extern (cap. 7): format, mapare coloane, reguli de curățare."""

    __tablename__ = "import_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    denumire: Mapped[str] = mapped_column(String(100), unique=True)
    tip_sursa: Mapped[str] = mapped_column(String(50))
    format: Mapped[str] = mapped_column(String(20))
    mapare: Mapped[dict] = mapped_column(JSONB)
    reguli_curatare: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    activ: Mapped[bool] = mapped_column(Boolean, default=True)


class ExternalRecord(Base):
    """Rând dintr-o sursă externă, într-un tabel de tranzit, gata pentru confruntare."""

    __tablename__ = "external_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id"), index=True)
    profil_id: Mapped[int] = mapped_column(ForeignKey("import_profile.id"))
    cif: Mapped[str | None] = mapped_column(String(20), index=True, nullable=True)
    numar_brut: Mapped[str | None] = mapped_column(String(100), nullable=True)
    numar_normalizat: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    data: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    total_fara_tva: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    total_tva: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    date_brute: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ReconciliationRule(Base):
    """Regulă de reconciliere — definiția completă (grupare, componente, ponderi,
    praguri, structura YAML din cap. 7) e stocată în JSONB, nu în cod."""

    __tablename__ = "reconciliation_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    denumire: Mapped[str] = mapped_column(String(100), unique=True)
    definitie: Mapped[dict] = mapped_column(JSONB)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("reconciliation_rule.id"), index=True)
    rulat_la: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
    nr_potriviri: Mapped[int] = mapped_column(Integer, default=0)
    nr_exceptii: Mapped[int] = mapped_column(Integer, default=0)
    nr_ambigue: Mapped[int] = mapped_column(Integer, default=0)


class ReconciliationResult(Base):
    """Deciziile umane (decizie/motiv/utilizator) supraviețuiesc rulărilor
    ulterioare — la re-rulare se actualizează doar scor/diferențe (impus în serviciu)."""

    __tablename__ = "reconciliation_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("reconciliation_run.id"), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("invoice_group.id"), nullable=True)
    external_record_id: Mapped[int | None] = mapped_column(ForeignKey("external_record.id"), nullable=True)
    scor: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    stare: Mapped[str] = mapped_column(String(25), default="noua")
    diferente: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    decizie: Mapped[str | None] = mapped_column(String(50), nullable=True)
    motiv: Mapped[str | None] = mapped_column(Text, nullable=True)
    utilizator_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    decis_la: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
