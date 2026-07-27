import datetime as dt

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import BatchStatus, BatchType, SourceObjectType


class ImportBatch(Base):
    """Un lot de import — unitatea anulabilă integral (cap. 4)."""

    __tablename__ = "import_batch"
    __table_args__ = (
        CheckConstraint("tip IN ('scan_local', 'anaf', 'import_extern')", name="tip_valid"),
        CheckConstraint(
            "stare IN ('in_curs', 'terminat', 'terminat_cu_erori', 'anulat')", name="stare_valida"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tip: Mapped[str] = mapped_column(String(20))
    sursa: Mapped[str | None] = mapped_column(Text, nullable=True)
    pornit_la: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
    terminat_la: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stare: Mapped[str] = mapped_column(String(20), default=BatchStatus.IN_CURS)
    utilizator_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    nr_fisiere: Mapped[int] = mapped_column(Integer, default=0)
    nr_documente: Mapped[int] = mapped_column(Integer, default=0)
    nr_erori: Mapped[int] = mapped_column(Integer, default=0)
    anulat_la: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    motiv_anulare: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_objects: Mapped[list["SourceObject"]] = relationship(back_populates="batch")


class SourceObject(Base):
    """Conținut binar imutabil. Doar INSERT — impus și prin GRANT (vezi migrare)."""

    __tablename__ = "source_object"
    __table_args__ = (
        CheckConstraint(
            "tip IN ('zip', 'xml_factura', 'xml_semnatura', 'atasament', 'pdf')", name="tip_valid"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id"), index=True)
    tip: Mapped[str] = mapped_column(String(20))
    continut: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    marime: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    nume_original: Mapped[str | None] = mapped_column(Text, nullable=True)
    cale_originala: Mapped[str | None] = mapped_column(Text, nullable=True)
    creat_la: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default="now()")

    batch: Mapped["ImportBatch"] = relationship(back_populates="source_objects")


class AnafMessage(Base):
    """Mesaj din lista SPV — schema pregătită pentru etapa 3 (sincronizare ANAF)."""

    __tablename__ = "anaf_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    cif: Mapped[str] = mapped_column(String(20), index=True)
    id_descarcare: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    id_incarcare: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tip_mesaj: Mapped[str | None] = mapped_column(String(50), nullable=True)
    data_publicare: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_descarcare: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stare: Mapped[str | None] = mapped_column(String(30), nullable=True)
    expira_la: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InvoiceSourceLink(Base):
    """Leagă o factură deja existentă de o sursă suplimentară primită ulterior
    (tier 2 de deduplicare: același XML, ambalaj diferit — cap. 3)."""

    __tablename__ = "invoice_source_link"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id"), index=True)
    source_object_id: Mapped[int] = mapped_column(ForeignKey("source_object.id"))
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id"))
    creat_la: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
