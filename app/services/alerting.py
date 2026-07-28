"""Alertare activă (cap. 8): „un tablou de bord nu este o alertă". Scrie un
fișier de stare monitorizat la fiecare rulare (verificabil extern, ex. un
task programat Windows sau un plugin Nagios/Zabbix) și, dacă SMTP e
configurat, trimite un email când există alerte critice deschise. Ambele
sunt best-effort — o eroare de trimitere nu oprește rularea verificărilor."""

from __future__ import annotations

import datetime as dt
import json
import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.monitoring import IntegrityAlert
from app.services.monitoring import run_integrity_checks

logger = logging.getLogger(__name__)


def _serialize_alert(a: IntegrityAlert) -> dict:
    return {
        "id": a.id,
        "cod": a.cod,
        "nivel": a.nivel,
        "mesaj": a.mesaj,
        "generat_la": a.generat_la.isoformat() if a.generat_la else None,
    }


def write_status_file(session: Session) -> dict:
    settings = get_settings()
    deschise = session.scalars(
        select(IntegrityAlert)
        .where(IntegrityAlert.rezolvat_la.is_(None))
        .order_by(IntegrityAlert.generat_la.desc())
    ).all()

    numar_pe_nivel = {"critic": 0, "avertisment": 0, "info": 0}
    for a in deschise:
        numar_pe_nivel[a.nivel] = numar_pe_nivel.get(a.nivel, 0) + 1

    stare = "alerta" if (numar_pe_nivel["critic"] or numar_pe_nivel["avertisment"]) else "ok"
    continut = {
        "verificat_la": dt.datetime.now(dt.timezone.utc).isoformat(),
        "stare": stare,
        "alerte_deschise": numar_pe_nivel,
        "detalii": [_serialize_alert(a) for a in deschise[:200]],
    }

    cale = Path(settings.status_file_path)
    cale.parent.mkdir(parents=True, exist_ok=True)
    cale.write_text(json.dumps(continut, ensure_ascii=False, indent=2), encoding="utf-8")
    return continut


def send_alert_email(subiect: str, corp: str) -> bool:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_to_list:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subiect
        msg["From"] = settings.smtp_from or "aiefactura@localhost"
        msg["To"] = ", ".join(settings.smtp_to_list)
        msg.set_content(corp)

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        return True
    except Exception:
        logger.exception("trimiterea emailului de alertă a eșuat")
        return False


def run_checks_and_notify(session: Session) -> dict:
    """Punctul de intrare folosit de planificator și de acțiunea manuală
    din UI: rulează verificările, scrie fișierul de stare, trimite email
    dacă există alerte critice deschise."""
    run_integrity_checks(session)
    status = write_status_file(session)
    if status["alerte_deschise"]["critic"] > 0:
        critice = [d for d in status["detalii"] if d["nivel"] == "critic"]
        corp = "\n".join(f"- [{d['cod']}] {d['mesaj']}" for d in critice)
        send_alert_email(
            f"AIeFactura: {status['alerte_deschise']['critic']} alertă(e) critică(e) deschisă(e)",
            corp,
        )
    return status
