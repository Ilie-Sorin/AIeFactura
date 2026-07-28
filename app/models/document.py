import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import DocumentState, PartyRole


class Invoice(Base):
    """Document normalizat. NOTĂ despre partiționare: `data_emitere` e NOT NULL și
    tabelul e proiectat ca viitoare cheie de partiționare RANGE pe an (cap. 2, 11).
    Partiționarea fizică e amânată la anul 2 per specificație — activarea ei cere
    chei primare/străine compuse (id, data_emitere) în cascadă pe invoice_line,
    tax_summary, attachment, invoice_relation, invoice_group_member — schimbare
    documentată separat, nu executată în migrarea inițială."""

    __tablename__ = "invoice"
    __table_args__ = (
        CheckConstraint("directie IN ('intrare', 'iesire')", name="directie_valida"),
        CheckConstraint(
            "stare IN ('primit', 'parsat', 'normalizat', 'validat', 'indexat', 'eroare')",
            name="stare_valida",
        ),
        Index("ix_invoice_dedup_avertizare", "cif_emitent", "numar_normalizat", "data_emitere"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id"), index=True)
    source_object_id: Mapped[int] = mapped_column(ForeignKey("source_object.id"))
    anaf_message_id: Mapped[int | None] = mapped_column(ForeignKey("anaf_message.id"), nullable=True)

    directie: Mapped[str] = mapped_column(String(10))
    cif_emitent: Mapped[str] = mapped_column(String(20), index=True)
    cif_beneficiar: Mapped[str | None] = mapped_column(String(20), index=True, nullable=True)

    numar_brut: Mapped[str] = mapped_column(String(100))
    numar_normalizat: Mapped[str] = mapped_column(String(100), index=True)
    serie: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # BigInteger, nu Integer: numere de factura reale (secvente ERP) pot depasi
    # 2,147,483,647 -- am vazut asta cu date reale (ex. "6030066180").
    numar_numeric: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    data_emitere: Mapped[dt.date] = mapped_column(Date, index=True)
    data_scadenta: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    tip_document: Mapped[str | None] = mapped_column(String(50), nullable=True)

    moneda: Mapped[str] = mapped_column(String(3), default="RON")
    curs: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    total_fara_tva: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    total_tva: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    total_document: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    total_de_plata: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)

    nr_contract: Mapped[str | None] = mapped_column(String(100), nullable=True)
    nr_comanda: Mapped[str | None] = mapped_column(String(100), nullable=True)
    perioada_start: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    perioada_sfarsit: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    versiune_cius: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stare: Mapped[str] = mapped_column(String(20), default=DocumentState.PRIMIT, index=True)
    eroare_mesaj: Mapped[str | None] = mapped_column(Text, nullable=True)
    eroare_detalii: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    xpath_map: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Referintele brute extrase din XML (storno/comanda/contract/aviz/receptie),
    # pastrate pentru rezolvarea relatiilor explicite (cap. 6) -- inclusiv cele
    # intarziate, cand documentul referit soseste ulterior.
    referinte_xml: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    retras: Mapped[bool] = mapped_column(default=False)
    retras_la: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retras_motiv: Mapped[str | None] = mapped_column(Text, nullable=True)

    creat_la: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default="now()")

    parts: Mapped[list["InvoiceParty"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    lines: Mapped[list["InvoiceLine"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    tax_summaries: Mapped[list["TaxSummary"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


class InvoiceParty(Base):
    __tablename__ = "invoice_party"
    __table_args__ = (
        CheckConstraint(
            "rol IN ('furnizor', 'client', 'reprezentant_fiscal')", name="rol_valid"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id"), index=True)
    rol: Mapped[str] = mapped_column(String(30))
    denumire: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cif_brut: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cif_normalizat: Mapped[str | None] = mapped_column(String(30), index=True, nullable=True)
    nr_reg_com: Mapped[str | None] = mapped_column(String(50), nullable=True)
    adresa: Mapped[str | None] = mapped_column(Text, nullable=True)
    tara: Mapped[str | None] = mapped_column(String(2), nullable=True)
    cod_tva: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cont_bancar: Mapped[str | None] = mapped_column(String(50), nullable=True)
    contact: Mapped[str | None] = mapped_column(String(255), nullable=True)

    invoice: Mapped["Invoice"] = relationship(back_populates="parts")


class InvoiceLine(Base):
    __tablename__ = "invoice_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id"), index=True)
    nr_crt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cod_articol_furnizor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cod_articol_client: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # cbc:Name (denumirea articolului) si cbc:Description (detalii text
    # suplimentare) nu sunt interschimbabile -- pastrate separat.
    denumire: Mapped[str | None] = mapped_column(Text, nullable=True)
    descriere: Mapped[str | None] = mapped_column(Text, nullable=True)
    cantitate: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    um: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pret_unitar: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    valoare_fara_tva: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    cota_tva: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    categorie_tva: Mapped[str | None] = mapped_column(String(10), nullable=True)
    reducere: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    nr_comanda: Mapped[str | None] = mapped_column(String(100), nullable=True)
    centru_cost: Mapped[str | None] = mapped_column(String(100), nullable=True)
    xpath: Mapped[str | None] = mapped_column(Text, nullable=True)

    descriere_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('romanian', coalesce(denumire, '') || ' ' || coalesce(descriere, ''))",
            persisted=True,
        ),
    )

    invoice: Mapped["Invoice"] = relationship(back_populates="lines")

    __table_args__ = (Index("ix_invoice_line_descriere_tsv", "descriere_tsv", postgresql_using="gin"),)


class TaxSummary(Base):
    __tablename__ = "tax_summary"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id"), index=True)
    cota: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    categorie: Mapped[str | None] = mapped_column(String(10), nullable=True)
    baza: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    tva: Mapped[Decimal] = mapped_column(Numeric(18, 2))

    invoice: Mapped["Invoice"] = relationship(back_populates="tax_summaries")


class Attachment(Base):
    __tablename__ = "attachment"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id"), index=True)
    source_object_id: Mapped[int | None] = mapped_column(ForeignKey("source_object.id"), nullable=True)
    nume: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    descriere: Mapped[str | None] = mapped_column(Text, nullable=True)

    invoice: Mapped["Invoice"] = relationship(back_populates="attachments")
