"""Controale de completitudine și integritate (cap. 8) — rulate programat
(vezi `app/scheduler.py`), cu rezultatele persistate ca alerte, nu doar
afișate pasiv: „un tablou de bord nu este o alertă".

Două verificări din capitolul 8 rămân în afara acestui build fiindcă depind
de sincronizarea ANAF (etapa 3, neimplementată): documentele prezente în
lista de mesaje ANAF dar lipsă local, și proximitatea de expirarea ferestrei
de 60 de zile — ambele au nevoie de `anaf_message`, care rămâne gol până
atunci.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models.consolidation import InvoiceRelation
from app.models.document import Invoice, InvoiceLine
from app.models.ingestion import ImportBatch, SourceObject
from app.models.monitoring import IntegrityAlert
from app.services.normalize_cif import is_valid_cif

COTE_TVA_UZUALE = {Decimal("0"), Decimal("5"), Decimal("9"), Decimal("19")}
CATEGORII_COTA_ZERO = {"Z", "E", "G", "O"}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _furnizor_denumire(invoice: Invoice) -> str:
    """Nume lizibil pentru mesajele de alertă -- CIF-ul brut nu spune nimic
    utilizatorului. Necesită `invoice.parts` deja încărcat (selectinload)."""
    denumire = next((p.denumire for p in invoice.parts if p.rol == "furnizor" and p.denumire), None)
    return denumire or invoice.cif_emitent


@dataclass
class Finding:
    cod: str
    nivel: str  # info | avertisment | critic
    cheie: str
    mesaj: str
    detalii: dict | None = None


def check_numbering_gaps(session: Session) -> list[Finding]:
    """Discontinuități în seriile de numere la facturile emise (direcție='iesire')."""
    randuri = session.execute(
        select(Invoice.cif_emitent, Invoice.serie, Invoice.numar_numeric).where(
            Invoice.directie == "iesire", Invoice.numar_numeric.isnot(None)
        )
    ).all()
    grupuri: dict[tuple[str, str | None], list[int]] = {}
    for cif, serie, numar in randuri:
        grupuri.setdefault((cif, serie), []).append(numar)

    findings = []
    for (cif, serie), numere in grupuri.items():
        unice = sorted(set(numere))
        if len(unice) < 2:
            continue
        prezente = set(unice)
        lipsa = [n for n in range(unice[0], unice[-1] + 1) if n not in prezente]
        if not lipsa:
            continue
        serie_afisata = serie or "(fără serie)"
        findings.append(
            Finding(
                cod="discontinuitate_serie",
                nivel="avertisment",
                cheie=f"cif={cif}|serie={serie or ''}",
                mesaj=f"Discontinuitate în seria {serie_afisata} a CIF {cif}: lipsesc {len(lipsa)} numere.",
                detalii={
                    "cif": cif,
                    "serie": serie,
                    "interval": [unice[0], unice[-1]],
                    "lipsa": lipsa[:50],
                    "nr_lipsa_total": len(lipsa),
                },
            )
        )
    return findings


def check_stalled_scans(session: Session, prag_zile: int = 3) -> list[Finding]:
    """Scanare automată eșuată sau neexecutată de N zile — doar dacă există
    directoare configurate spre monitorizare (altfel nu era de așteptat)."""
    settings = get_settings()
    if not settings.watch_directory_list:
        return []

    ultimul = session.scalar(
        select(ImportBatch).where(ImportBatch.tip == "scan_local").order_by(ImportBatch.pornit_la.desc())
    )
    if ultimul is None:
        return [
            Finding(
                cod="scanare_neexecutata",
                nivel="critic",
                cheie="scanare_automata",
                mesaj="Nicio scanare automată nu a rulat încă, deși există directoare configurate (WATCH_DIRECTORIES).",
                detalii={"varsta_zile": None, "prag_zile": prag_zile},
            )
        ]

    varsta_zile = (_now() - ultimul.pornit_la).days
    if varsta_zile < prag_zile:
        return []
    return [
        Finding(
            cod="scanare_neexecutata",
            nivel="critic",
            cheie="scanare_automata",
            mesaj=f"Ultima scanare automată a rulat acum {varsta_zile} zile (prag: {prag_zile}).",
            detalii={"varsta_zile": varsta_zile, "prag_zile": prag_zile},
        )
    ]


def check_document_integrity(session: Session) -> list[Finding]:
    """Suprafață alertă pentru documentele deja marcate `stare='eroare'` la
    ingestie (tripla verificare sumă linii/total/TVA, CIF) — cap. 8 cere ca
    astea să devină alerte active, nu doar rânduri de răsfoit în registru."""
    invoices = session.scalars(
        select(Invoice).options(selectinload(Invoice.parts)).where(Invoice.stare == "eroare")
    ).all()
    return [
        Finding(
            cod="integritate_document",
            nivel="avertisment",
            cheie=f"invoice={inv.id}",
            mesaj=f"Documentul {inv.numar_brut} ({_furnizor_denumire(inv)}) are o eroare de integritate: {inv.eroare_mesaj}",
            detalii={"invoice_id": inv.id, "eroare_detalii": inv.eroare_detalii},
        )
        for inv in invoices
    ]


def check_unusual_vat(session: Session) -> list[Finding]:
    """Cote TVA neobișnuite sau incoerente cu categoria declarată."""
    randuri = session.execute(
        select(InvoiceLine.invoice_id, InvoiceLine.cota_tva, InvoiceLine.categorie_tva, InvoiceLine.id).where(
            InvoiceLine.cota_tva.isnot(None)
        )
    ).all()

    probleme_per_factura: dict[int, list[dict]] = {}
    for invoice_id, cota, categorie, line_id in randuri:
        motiv = None
        if cota not in COTE_TVA_UZUALE:
            motiv = f"cotă neobișnuită {cota}%"
        elif categorie in CATEGORII_COTA_ZERO and cota != 0:
            motiv = f"categorie {categorie} (scutit) cu cotă {cota}% ≠ 0"
        elif categorie == "S" and cota == 0:
            motiv = "categorie S (standard) cu cotă 0%"
        if motiv:
            probleme_per_factura.setdefault(invoice_id, []).append(
                {"linie_id": line_id, "cota": str(cota), "categorie": categorie, "motiv": motiv}
            )

    return [
        Finding(
            cod="tva_neobisnuit",
            nivel="avertisment",
            cheie=f"invoice={invoice_id}",
            mesaj=f"{len(probleme)} linie(i) cu TVA neobișnuit sau incoerent cu categoria.",
            detalii={"invoice_id": invoice_id, "probleme": probleme},
        )
        for invoice_id, probleme in probleme_per_factura.items()
    ]


def check_invalid_cif(session: Session) -> list[Finding]:
    """CIF invalid la cifra de control — emitent și beneficiar (CIF-uri
    străine sau needeterminabile nu se pot valida prin acest algoritm)."""
    randuri = session.execute(select(Invoice.id, Invoice.cif_emitent, Invoice.cif_beneficiar)).all()
    findings = []
    for invoice_id, cif_emitent, cif_beneficiar in randuri:
        invalide = []
        if cif_emitent and cif_emitent.isdigit() and not is_valid_cif(cif_emitent):
            invalide.append({"rol": "emitent", "cif": cif_emitent})
        if cif_beneficiar and cif_beneficiar.isdigit() and not is_valid_cif(cif_beneficiar):
            invalide.append({"rol": "beneficiar", "cif": cif_beneficiar})
        if invalide:
            findings.append(
                Finding(
                    cod="cif_invalid",
                    nivel="avertisment",
                    cheie=f"invoice={invoice_id}",
                    mesaj="CIF invalid la cifra de control: "
                    + ", ".join(f"{i['rol']} {i['cif']}" for i in invalide),
                    detalii={"invoice_id": invoice_id, "invalide": invalide},
                )
            )
    return findings


def check_orphan_storno(session: Session) -> list[Finding]:
    """Storno fără document de referință — nicio legătură (explicită sau
    dedusă) către o factură stornată."""
    stornouri = session.scalars(
        select(Invoice).options(selectinload(Invoice.parts)).where(Invoice.tip_document == "381")
    ).all()
    if not stornouri:
        return []
    ids = [s.id for s in stornouri]
    legate = set(
        session.scalars(
            select(InvoiceRelation.invoice_from).where(
                InvoiceRelation.invoice_from.in_(ids),
                InvoiceRelation.tip.in_(["storno", "storno_dedus"]),
            )
        ).all()
    )
    return [
        Finding(
            cod="storno_orfan",
            nivel="avertisment",
            cheie=f"invoice={s.id}",
            mesaj=f"Document de tip credit {s.numar_brut} ({_furnizor_denumire(s)}) fără nicio legătură către factura stornată.",
            detalii={"invoice_id": s.id},
        )
        for s in stornouri
        if s.id not in legate
    ]


def check_source_checksum_sample(session: Session, esantion: int = 50) -> list[Finding]:
    """Recalcularea periodică a SHA-256 pe eșantion, pentru coruperea silențioasă."""
    obiecte = session.scalars(select(SourceObject).order_by(func.random()).limit(esantion)).all()
    findings = []
    for obj in obiecte:
        calculat = hashlib.sha256(obj.continut).hexdigest()
        if calculat != obj.sha256:
            findings.append(
                Finding(
                    cod="coruptie_silentioasa",
                    nivel="critic",
                    cheie=f"source_object={obj.id}",
                    mesaj=f"SHA-256 recalculat nu corespunde cu cel stocat pentru source_object #{obj.id}.",
                    detalii={
                        "source_object_id": obj.id,
                        "sha256_stocat": obj.sha256,
                        "sha256_recalculat": calculat,
                    },
                )
            )
    return findings


VERIFICARI: list[tuple[str, Callable[[Session], list[Finding]]]] = [
    ("discontinuitate_serie", check_numbering_gaps),
    ("scanare_neexecutata", check_stalled_scans),
    ("integritate_document", check_document_integrity),
    ("tva_neobisnuit", check_unusual_vat),
    ("cif_invalid", check_invalid_cif),
    ("storno_orfan", check_orphan_storno),
    ("coruptie_silentioasa", check_source_checksum_sample),
]


def reconcile_alerts(session: Session, cod: str, findings: list[Finding]) -> None:
    """Idempotent: creează alerte noi, actualizează mesajul/detaliile celor
    deja deschise pentru aceeași cheie, și rezolvă automat pe cele a căror
    cauză a dispărut — fără să dubleze sau să șteargă istoricul."""
    acum = _now()
    findings_by_key = {f.cheie: f for f in findings}

    deschise = session.scalars(
        select(IntegrityAlert).where(IntegrityAlert.cod == cod, IntegrityAlert.rezolvat_la.is_(None))
    ).all()
    deschise_by_key = {a.cheie: a for a in deschise}

    for cheie, alerta in deschise_by_key.items():
        if cheie not in findings_by_key:
            alerta.rezolvat_la = acum
            alerta.rezolvat_automat = True

    for cheie, finding in findings_by_key.items():
        existenta = deschise_by_key.get(cheie)
        if existenta is not None:
            existenta.mesaj = finding.mesaj
            existenta.detalii = finding.detalii
            existenta.nivel = finding.nivel
            existenta.actualizat_la = acum
        else:
            session.add(
                IntegrityAlert(
                    cod=cod,
                    nivel=finding.nivel,
                    cheie=cheie,
                    mesaj=finding.mesaj,
                    detalii=finding.detalii,
                    generat_la=acum,
                    actualizat_la=acum,
                )
            )
    session.flush()


def run_integrity_checks(session: Session) -> list[Finding]:
    toate: list[Finding] = []
    for cod, functie in VERIFICARI:
        findings = functie(session)
        reconcile_alerts(session, cod, findings)
        toate.extend(findings)
    session.flush()
    return toate


def resolve_alert(session: Session, alert_id: int, utilizator_id: int, motiv: str | None = None) -> IntegrityAlert:
    alerta = session.get(IntegrityAlert, alert_id)
    if alerta is None:
        raise ValueError(f"alerta {alert_id} nu există")
    alerta.rezolvat_la = _now()
    alerta.rezolvat_automat = False
    alerta.rezolvat_de_id = utilizator_id
    alerta.motiv_rezolvare = motiv
    session.flush()
    return alerta
