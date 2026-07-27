"""Orchestrarea unui lot de import (cap. 3-4): identificare ZIP/XML, stocare
surse binare (insert-only), parsare -> normalizare -> validare -> indexare,
deduplicare ierarhică. O eroare pe un fișier NU oprește lotul (cap. 4A)."""

from __future__ import annotations

import base64
import hashlib
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.consolidation import InvoiceGroup, InvoiceGroupMember, InvoiceRelation
from app.models.document import Attachment, Invoice, InvoiceLine, InvoiceParty, TaxSummary
from app.models.enums import BatchStatus, DocumentState, Direction
from app.models.ingestion import ImportBatch, InvoiceSourceLink, SourceObject
from app.services.dedup import DedupOutcome, check_duplicate
from app.services.integrity import check_cif_valid, check_line_sum_vs_total
from app.services.audit import write_audit as _audit_entry
from app.services.consolidation import consolidate_invoice, recompute_group
from app.services.normalize_cif import normalize_cif
from app.services.normalize_number import normalize_invoice_number, resolve_numbering_config
from app.services.xml_parser import InvoiceXmlError, classify_xml_bytes, parse_invoice_xml


@dataclass
class IngestFile:
    """Un fișier de intrare pentru un lot: ZIP sau XML de sine stătător."""

    continut: bytes
    nume_original: str
    cale_originala: str | None = None


@dataclass
class FileResult:
    nume_original: str
    stare: str  # 'importat' | 'duplicat' | 'eroare'
    invoice_id: int | None = None
    mesaj: str | None = None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _store_source_object(
    session: Session,
    batch_id: int,
    tip: str,
    continut: bytes,
    *,
    nume_original: str | None = None,
    cale_originala: str | None = None,
    mime: str | None = None,
) -> SourceObject:
    obj = SourceObject(
        batch_id=batch_id,
        tip=tip,
        continut=continut,
        sha256=_sha256(continut),
        marime=len(continut),
        mime=mime,
        nume_original=nume_original,
        cale_originala=cale_originala,
    )
    session.add(obj)
    session.flush()
    return obj


def _audit(
    session: Session,
    actiune: str,
    *,
    entitate: str | None = None,
    entitate_id: int | None = None,
    detalii: dict | None = None,
    utilizator_id: int | None = None,
) -> None:
    _audit_entry(
        session,
        actiune,
        utilizator_id=utilizator_id,
        entitate=entitate,
        entitate_id=entitate_id,
        detalii=detalii,
    )


def _mime_for(nume: str) -> str | None:
    lower = nume.lower()
    if lower.endswith(".xml"):
        return "application/xml"
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".zip"):
        return "application/zip"
    return None


def _extract_members(
    continut: bytes, nume_original: str
) -> tuple[bytes | None, list[tuple[bytes, str]]]:
    """(bytes ZIP dacă e arhivă, listă (conținut, nume) membri). Pentru XML de
    sine stătător: (None, [(continut, nume_original)])."""
    if nume_original.lower().endswith(".zip") or continut[:2] == b"PK":
        try:
            with zipfile.ZipFile(BytesIO(continut)) as zf:
                return continut, [
                    (zf.read(info), info.filename) for info in zf.infolist() if not info.is_dir()
                ]
        except zipfile.BadZipFile as exc:
            raise InvoiceXmlError(f"arhivă ZIP coruptă: {exc}") from exc
    return None, [(continut, nume_original)]


def start_batch(
    session: Session, tip: str, sursa: str | None = None, utilizator_id: int | None = None
) -> ImportBatch:
    batch = ImportBatch(tip=tip, sursa=sursa, stare=BatchStatus.IN_CURS, utilizator_id=utilizator_id)
    session.add(batch)
    session.flush()
    _audit(
        session,
        "import_pornit",
        entitate="import_batch",
        entitate_id=batch.id,
        detalii={"tip": tip, "sursa": sursa},
        utilizator_id=utilizator_id,
    )
    return batch


def ingest_file(
    session: Session, batch: ImportBatch, file: IngestFile, *, utilizator_id: int | None = None
) -> FileResult:
    batch.nr_fisiere += 1

    try:
        zip_bytes, members = _extract_members(file.continut, file.nume_original)
    except InvoiceXmlError as exc:
        batch.nr_erori += 1
        _audit(
            session,
            "eroare_import",
            entitate="source_object",
            detalii={"fisier": file.nume_original, "mesaj": exc.message},
            utilizator_id=utilizator_id,
        )
        return FileResult(file.nume_original, "eroare", mesaj=exc.message)

    zip_source = None
    if zip_bytes is not None:
        zip_source = _store_source_object(
            session,
            batch.id,
            "zip",
            zip_bytes,
            nume_original=file.nume_original,
            cale_originala=file.cale_originala,
            mime="application/zip",
        )

    xml_factura_member: tuple[bytes, str] | None = None
    for continut, nume in members:
        tip = classify_xml_bytes(continut) if nume.lower().endswith(".xml") else "atasament"
        if tip == "xml_factura" and xml_factura_member is None:
            xml_factura_member = (continut, nume)
            continue
        _store_source_object(
            session,
            batch.id,
            tip,
            continut,
            nume_original=nume,
            cale_originala=file.cale_originala,
            mime=_mime_for(nume),
        )

    if xml_factura_member is None:
        batch.nr_erori += 1
        _audit(
            session,
            "eroare_import",
            detalii={"fisier": file.nume_original, "mesaj": "nu s-a găsit un XML de factură interpretabil"},
            utilizator_id=utilizator_id,
        )
        return FileResult(
            file.nume_original, "eroare", mesaj="nu s-a găsit un XML de factură interpretabil"
        )

    xml_bytes, xml_nume = xml_factura_member
    try:
        parsed = parse_invoice_xml(xml_bytes)
    except InvoiceXmlError as exc:
        batch.nr_erori += 1
        # Pastram octetii primiti (P1: captura completa) chiar daca nu putem
        # crea un rand Invoice fara campurile obligatorii (cif, numar, data, totaluri).
        _store_source_object(
            session,
            batch.id,
            "xml_factura",
            xml_bytes,
            nume_original=xml_nume,
            cale_originala=file.cale_originala,
            mime="application/xml",
        )
        _audit(
            session,
            "eroare_parsare",
            detalii={"fisier": xml_nume, "mesaj": exc.message, "xpath": exc.xpath},
            utilizator_id=utilizator_id,
        )
        return FileResult(xml_nume, "eroare", mesaj=exc.message)

    xml_source = _store_source_object(
        session,
        batch.id,
        "xml_factura",
        xml_bytes,
        nume_original=xml_nume,
        cale_originala=file.cale_originala,
        mime="application/xml",
    )

    cif_emitent_raw = next((p.cif_brut for p in parsed.parts if p.rol == "furnizor"), None)
    cif_beneficiar_raw = next((p.cif_brut for p in parsed.parts if p.rol == "client"), None)
    cif_emitent = normalize_cif(cif_emitent_raw)
    cif_beneficiar = normalize_cif(cif_beneficiar_raw) if cif_beneficiar_raw else None

    if not cif_emitent:
        batch.nr_erori += 1
        _audit(
            session,
            "eroare_parsare",
            detalii={"fisier": xml_nume, "mesaj": "lipsește CIF-ul furnizorului"},
            utilizator_id=utilizator_id,
        )
        return FileResult(xml_nume, "eroare", mesaj="lipsește CIF-ul furnizorului")

    numbering_config = resolve_numbering_config(session, cif_emitent)
    forms = normalize_invoice_number(parsed.numar_brut, numbering_config)

    own_cifs = {normalize_cif(c) for c in get_settings().own_cif_list}
    directie = Direction.IESIRE if cif_emitent in own_cifs else Direction.INTRARE

    dedup = check_duplicate(
        session,
        sha256_xml=xml_source.sha256,
        cif_emitent=cif_emitent,
        numar_normalizat=forms.normalizata,
        data_emitere=parsed.data_emitere,
        total_document=parsed.total_document,
    )

    if dedup.is_certain_duplicate:
        session.add(
            InvoiceSourceLink(
                invoice_id=dedup.existing_invoice_id, source_object_id=xml_source.id, batch_id=batch.id
            )
        )
        if zip_source is not None:
            session.add(
                InvoiceSourceLink(
                    invoice_id=dedup.existing_invoice_id,
                    source_object_id=zip_source.id,
                    batch_id=batch.id,
                )
            )
        _audit(
            session,
            "duplicat_detectat",
            entitate="invoice",
            entitate_id=dedup.existing_invoice_id,
            detalii={"nivel": dedup.outcome.value, **(dedup.detalii or {})},
            utilizator_id=utilizator_id,
        )
        return FileResult(
            xml_nume, "duplicat", invoice_id=dedup.existing_invoice_id, mesaj=dedup.outcome.value
        )

    issues = [i for i in (check_line_sum_vs_total(parsed), check_cif_valid(cif_emitent)) if i]
    stare = DocumentState.EROARE if issues else DocumentState.INDEXAT
    eroare_mesaj = "; ".join(i.mesaj for i in issues) or None
    eroare_detalii = (
        {"probleme": [{"cod": i.cod, "detalii": i.detalii} for i in issues]} if issues else None
    )

    invoice = Invoice(
        batch_id=batch.id,
        source_object_id=xml_source.id,
        directie=directie,
        cif_emitent=cif_emitent,
        cif_beneficiar=cif_beneficiar,
        numar_brut=parsed.numar_brut,
        numar_normalizat=forms.normalizata,
        serie=forms.serie,
        numar_numeric=forms.numar_numeric,
        data_emitere=parsed.data_emitere,
        data_scadenta=parsed.data_scadenta,
        tip_document=parsed.tip_document,
        moneda=parsed.moneda,
        total_fara_tva=parsed.total_fara_tva,
        total_tva=parsed.total_tva,
        total_document=parsed.total_document,
        total_de_plata=parsed.total_de_plata,
        nr_contract=parsed.nr_contract,
        nr_comanda=parsed.nr_comanda,
        perioada_start=parsed.perioada_start,
        perioada_sfarsit=parsed.perioada_sfarsit,
        versiune_cius=parsed.versiune_cius,
        stare=stare,
        eroare_mesaj=eroare_mesaj,
        eroare_detalii=eroare_detalii,
        xpath_map=parsed.xpath_map,
        referinte_xml=[
            {"tip": r.tip, "valoare": r.valoare, "xpath": r.xpath} for r in parsed.references
        ]
        or None,
    )
    session.add(invoice)
    session.flush()

    if zip_source is not None:
        # Legatura la ZIP-ul original, pentru ecranul Document (cap. 9: "acces la
        # ZIP si XML") -- reutilizam invoice_source_link si pentru sursa "principala",
        # nu doar pentru sursele suplimentare de la duplicate.
        session.add(
            InvoiceSourceLink(invoice_id=invoice.id, source_object_id=zip_source.id, batch_id=batch.id)
        )

    for p in parsed.parts:
        session.add(
            InvoiceParty(
                invoice_id=invoice.id,
                rol=p.rol,
                denumire=p.denumire,
                cif_brut=p.cif_brut,
                cif_normalizat=normalize_cif(p.cif_brut),
                nr_reg_com=p.nr_reg_com,
                adresa=p.adresa,
                tara=p.tara,
                cod_tva=p.cod_tva,
                cont_bancar=p.cont_bancar,
                contact=p.contact,
            )
        )

    for l in parsed.lines:
        session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                nr_crt=l.nr_crt,
                cod_articol_furnizor=l.cod_articol_furnizor,
                cod_articol_client=l.cod_articol_client,
                descriere=l.descriere,
                cantitate=l.cantitate,
                um=l.um,
                pret_unitar=l.pret_unitar,
                valoare_fara_tva=l.valoare_fara_tva,
                cota_tva=l.cota_tva,
                categorie_tva=l.categorie_tva,
                reducere=l.reducere,
                nr_comanda=l.nr_comanda,
                xpath=l.xpath,
            )
        )

    for t in parsed.tax_summaries:
        session.add(
            TaxSummary(invoice_id=invoice.id, cota=t.cota, categorie=t.categorie, baza=t.baza, tva=t.tva)
        )

    for a in parsed.attachments:
        att_source_id = None
        if a.continut_base64:
            try:
                decoded = base64.b64decode(a.continut_base64, validate=False)
            except (ValueError, base64.binascii.Error):
                decoded = None
            if decoded:
                att_source = _store_source_object(
                    session, batch.id, "atasament", decoded, nume_original=a.nume, mime=a.mime
                )
                att_source_id = att_source.id
        session.add(
            Attachment(
                invoice_id=invoice.id,
                source_object_id=att_source_id,
                nume=a.nume,
                mime=a.mime,
                descriere=a.descriere,
            )
        )

    if dedup.outcome in (DedupOutcome.DUPLICAT_PROBABIL, DedupOutcome.NUMAR_DUPLICAT_VALORI_DIFERITE):
        _audit(
            session,
            dedup.outcome.value,
            entitate="invoice",
            entitate_id=invoice.id,
            detalii=dedup.detalii,
            utilizator_id=utilizator_id,
        )

    batch.nr_documente += 1
    _audit(
        session,
        "document_importat",
        entitate="invoice",
        entitate_id=invoice.id,
        detalii={"numar_brut": parsed.numar_brut, "stare": stare.value},
        utilizator_id=utilizator_id,
    )

    consolidate_invoice(session, invoice)

    return FileResult(xml_nume, "importat", invoice_id=invoice.id, mesaj=eroare_mesaj)


def finish_batch(session: Session, batch: ImportBatch) -> None:
    batch.stare = BatchStatus.TERMINAT_CU_ERORI if batch.nr_erori else BatchStatus.TERMINAT
    batch.terminat_la = _now()
    _audit(
        session,
        "import_terminat",
        entitate="import_batch",
        entitate_id=batch.id,
        detalii={"nr_fisiere": batch.nr_fisiere, "nr_documente": batch.nr_documente, "nr_erori": batch.nr_erori},
    )


def cancel_batch(
    session: Session, batch: ImportBatch, motiv: str, utilizator_id: int | None = None
) -> None:
    """Anulează lotul: șterge datele normalizate produse (invoice + tot ce
    depinde de el), dar NU sursele binare — insert-only, rămân „importate și
    retrase" doar prin faptul că nicio factură nu le mai referă (cap. 4)."""
    invoice_ids = session.scalars(select(Invoice.id).where(Invoice.batch_id == batch.id)).all()

    # Legaturile de sursa suplimentara pot fi fost create de ALT lot (ex.: un
    # reimport ulterior detectat ca duplicat catre o factura din acest lot) --
    # trebuie sterse inainte de Invoice, altfel FK-ul le blocheaza stergerea.
    session.execute(
        delete(InvoiceSourceLink).where(
            (InvoiceSourceLink.batch_id == batch.id)
            | (InvoiceSourceLink.invoice_id.in_(invoice_ids))
        )
    )

    # Facturile "de partea cealalta" a relatiilor sterse trebuie sa-si
    # recalculeze grupul dupa ce lotul dispare (posibila despartire de grup).
    alte_capete: set[int] = set()
    if invoice_ids:
        relatii_afectate = session.scalars(
            select(InvoiceRelation).where(
                InvoiceRelation.invoice_from.in_(invoice_ids)
                | InvoiceRelation.invoice_to.in_(invoice_ids)
            )
        ).all()
        alte_capete = {
            (r.invoice_to if r.invoice_from in invoice_ids else r.invoice_from)
            for r in relatii_afectate
        } - set(invoice_ids)

    if invoice_ids:
        session.execute(delete(Attachment).where(Attachment.invoice_id.in_(invoice_ids)))
        session.execute(delete(TaxSummary).where(TaxSummary.invoice_id.in_(invoice_ids)))
        session.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id.in_(invoice_ids)))
        session.execute(delete(InvoiceParty).where(InvoiceParty.invoice_id.in_(invoice_ids)))
        session.execute(delete(InvoiceGroupMember).where(InvoiceGroupMember.invoice_id.in_(invoice_ids)))
        session.execute(
            delete(InvoiceRelation).where(
                InvoiceRelation.invoice_from.in_(invoice_ids)
                | InvoiceRelation.invoice_to.in_(invoice_ids)
            )
        )
        session.execute(delete(Invoice).where(Invoice.id.in_(invoice_ids)))

        orfane = session.scalars(
            select(InvoiceGroup.id).where(
                ~InvoiceGroup.id.in_(select(InvoiceGroupMember.group_id).distinct())
            )
        ).all()
        if orfane:
            session.execute(delete(InvoiceGroup).where(InvoiceGroup.id.in_(orfane)))

        for inv_id in alte_capete:
            recompute_group(session, inv_id)

    batch.stare = BatchStatus.ANULAT
    batch.anulat_la = _now()
    batch.motiv_anulare = motiv
    _audit(
        session,
        "import_anulat",
        entitate="import_batch",
        entitate_id=batch.id,
        detalii={"motiv": motiv, "invoices_sterse": len(invoice_ids)},
        utilizator_id=utilizator_id,
    )
