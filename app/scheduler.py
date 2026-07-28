"""Planificator intern — APScheduler, în procesul aplicației, fără broker
extern (cap. 10: „Celery pe Windows e de evitat"). Monitorizează periodic
directoarele configurate în WATCH_DIRECTORIES."""

from __future__ import annotations

import logging
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_settings
from app.db import SessionLocal
from app.services.alerting import run_checks_and_notify
from app.services.scanner import scan_directory

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_watch_scan() -> None:
    settings = get_settings()
    for director in settings.watch_directory_list:
        session = SessionLocal()
        try:
            _batch, rezultate = scan_directory(
                session, Path(director), tip="scan_local", sursa=f"watch:{director}"
            )
            session.commit()
            if rezultate:
                logger.info("scanare automată %s: %d fișiere procesate", director, len(rezultate))
        except Exception:
            session.rollback()
            logger.exception("scanare automată eșuată pentru %s", director)
        finally:
            session.close()


def _run_integrity_checks() -> None:
    session = SessionLocal()
    try:
        status = run_checks_and_notify(session)
        session.commit()
        if status["stare"] != "ok":
            logger.warning("verificări de integritate: %s", status["alerte_deschise"])
    except Exception:
        session.rollback()
        logger.exception("verificările de completitudine/integritate au eșuat")
    finally:
        session.close()


def start_scheduler(interval_minutes: int = 5) -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    settings = get_settings()
    if not settings.watch_directory_list:
        logger.info("WATCH_DIRECTORIES gol — scheduler pornit fără job de scanare automată")

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _run_watch_scan, "interval", minutes=interval_minutes, id="watch_scan", replace_existing=True
    )
    scheduler.add_job(
        _run_integrity_checks,
        "interval",
        minutes=settings.integrity_check_interval_minutes,
        id="integrity_checks",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
