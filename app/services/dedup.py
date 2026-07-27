"""Deduplicare ierarhică, cu certitudine descrescătoare (cap. 3).

1. ID descărcare ANAF — identificator autoritar. Potrivire -> duplicat cert.
2. SHA-256 pe XML-ul facturii — conținut identic, ambalaj diferit. Duplicat cert;
   se înregistrează sursa suplimentară, nu un document nou.
3. (CIF emitent, număr normalizat, dată, total) — surse eterogene. Potrivire ->
   duplicat probabil, semnalat nu blocat. Același număr cu valori diferite e
   cazul patologic care trebuie să ajungă la om.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Invoice
from app.models.ingestion import AnafMessage, SourceObject


class DedupOutcome(StrEnum):
    DUPLICAT_CERT_ID_DESCARCARE = "duplicat_cert_id_descarcare"
    DUPLICAT_CERT_CONTINUT = "duplicat_cert_continut"
    DUPLICAT_PROBABIL = "duplicat_probabil"
    NUMAR_DUPLICAT_VALORI_DIFERITE = "numar_duplicat_valori_diferite"
    NOU = "nou"


@dataclass
class DedupResult:
    outcome: DedupOutcome
    existing_invoice_id: int | None = None
    detalii: dict | None = None

    @property
    def is_certain_duplicate(self) -> bool:
        return self.outcome in (
            DedupOutcome.DUPLICAT_CERT_ID_DESCARCARE,
            DedupOutcome.DUPLICAT_CERT_CONTINUT,
        )


def check_duplicate(
    session: Session,
    *,
    sha256_xml: str,
    cif_emitent: str,
    numar_normalizat: str,
    data_emitere: date,
    total_document: Decimal,
    id_descarcare: str | None = None,
) -> DedupResult:
    if id_descarcare:
        existing = session.scalar(
            select(Invoice)
            .join(AnafMessage, Invoice.anaf_message_id == AnafMessage.id)
            .where(AnafMessage.id_descarcare == id_descarcare)
        )
        if existing is not None:
            return DedupResult(
                DedupOutcome.DUPLICAT_CERT_ID_DESCARCARE,
                existing_invoice_id=existing.id,
                detalii={"id_descarcare": id_descarcare},
            )

    existing_source = session.scalar(
        select(SourceObject).where(
            SourceObject.tip == "xml_factura", SourceObject.sha256 == sha256_xml
        )
    )
    if existing_source is not None:
        existing_invoice = session.scalar(
            select(Invoice).where(Invoice.source_object_id == existing_source.id)
        )
        if existing_invoice is not None:
            return DedupResult(
                DedupOutcome.DUPLICAT_CERT_CONTINUT,
                existing_invoice_id=existing_invoice.id,
                detalii={"sha256_xml": sha256_xml},
            )

    candidates = session.scalars(
        select(Invoice).where(
            Invoice.cif_emitent == cif_emitent,
            Invoice.numar_normalizat == numar_normalizat,
            Invoice.data_emitere == data_emitere,
        )
    ).all()
    if candidates:
        exact = [c for c in candidates if c.total_document == total_document]
        if exact:
            return DedupResult(
                DedupOutcome.DUPLICAT_PROBABIL,
                existing_invoice_id=exact[0].id,
                detalii={"candidati": [c.id for c in exact]},
            )
        return DedupResult(
            DedupOutcome.NUMAR_DUPLICAT_VALORI_DIFERITE,
            existing_invoice_id=candidates[0].id,
            detalii={
                "candidati": [c.id for c in candidates],
                "total_nou": str(total_document),
                "total_existent": [str(c.total_document) for c in candidates],
            },
        )

    return DedupResult(DedupOutcome.NOU)
