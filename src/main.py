import logging
import signal
from datetime import datetime
from threading import Event, Thread
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from config import settings
from database import (
    ensure_runtime_schema,
    ensure_source_exists,
    save_scraped_jobs,
    verify_db_connection,
)
from scrapers.schonherz import SchonherzScraper
from scrapers.melodiak import MeloDiakScraper
from scrapers.minddiak import MindDiakScraper
from scrapers.mads import MadsScraper
from services.filter_engine import FilterEngine
from services.telegram_bot import TelegramBot

SCRAPE_INTERVAL_MINUTES = 10
TIMEZONE = ZoneInfo("Europe/Budapest")

# Naplózási formátum beállítása időbélyeggel
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("JobNotifier")


def scrape_cycle():
    """Egy teljes adatgyűjtési és szűrési ciklus."""
    logger.info("=== Ütemezett adatgyűjtési ciklus indítása ===")
    
    filter_engine = FilterEngine()
    scrapers = [
        SchonherzScraper(),
        MeloDiakScraper(),
        MindDiakScraper(),
        MadsScraper(),
    ]

    total_scraped = 0
    total_new = 0
    all_new_jobs_with_ids = []

    for scraper in scrapers:
        try:
            source_id = ensure_source_exists(
                code=scraper.source_code,
                name=scraper.source_name,
                base_url=scraper.base_url,
            )

            scraped_jobs = scraper.fetch_jobs()
            if not scraped_jobs:
                continue

            total_scraped += len(scraped_jobs)

            # Mentés az adatbázisba (Upsert) és új rekordok kiszűrése
            new_jobs_with_ids = save_scraped_jobs(source_id, scraped_jobs)
            total_new += len(new_jobs_with_ids)
            all_new_jobs_with_ids.extend(new_jobs_with_ids)

            logger.info(
                f"[{scraper.source_code.upper()}] Talált: {len(scraped_jobs)} db | "
                f"Új: {len(new_jobs_with_ids)} db"
            )

        except Exception as e:
            logger.error(f"Hiba a(z) {scraper.source_name} feldolgozása közben: {e}", exc_info=True)

    try:
        filter_engine.process_new_jobs(all_new_jobs_with_ids)
    except Exception:
        logger.exception("Az új hirdetések értesítési feldolgozása sikertelen.")

    logger.info(
        f"=== Ciklus vége! Összesített találat: {total_scraped} db, "
        f"Ebből friss: {total_new} db. Következő futás {SCRAPE_INTERVAL_MINUTES} perc múlva. ==="
    )


def digest_cycle():
    """Deliver queued daily summaries when their configured time has arrived."""
    try:
        FilterEngine().process_due_digests()
    except Exception:
        logger.exception("A napi Telegram-összefoglalók feldolgozása sikertelen.")


def main():
    logger.info("Diákmunka Értesítő szolgáltatás indítása...")

    if not verify_db_connection():
        logger.critical("Nem sikerült csatlakozni az adatbázishoz.")
        raise SystemExit(1)
    ensure_runtime_schema()

    scheduler = BlockingScheduler(timezone=TIMEZONE)

    scheduler.add_job(
        scrape_cycle,
        trigger="interval",
        minutes=SCRAPE_INTERVAL_MINUTES,
        next_run_time=datetime.now(TIMEZONE),
        id="student_jobs_crawler",
        name="Diákmunka crawler és szűrő",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        digest_cycle,
        trigger="interval",
        minutes=1,
        next_run_time=datetime.now(TIMEZONE),
        id="daily_notification_digests",
        name="Napi Telegram-összefoglalók",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    stop_event = Event()
    shutdown_requested = Event()
    telegram_thread = Thread(
        target=TelegramBot().poll_updates,
        args=(stop_event,),
        name="telegram-command-poller",
        daemon=True,
    )

    def request_shutdown(signum, _frame):
        logger.info("Leállítási jelzés érkezett (%s).", signum)
        shutdown_requested.set()
        if scheduler.running:
            scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, request_shutdown)

    try:
        telegram_thread.start()
        if shutdown_requested.is_set():
            return
        logger.info(
            "Ütemező elindítva; az első adatgyűjtés azonnal, majd %s percenként fut.",
            SCRAPE_INTERVAL_MINUTES,
        )
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Szolgáltatás leállítása kérésre...")
    finally:
        stop_event.set()
        if telegram_thread.is_alive():
            telegram_thread.join(timeout=35)
        if scheduler.running:
            scheduler.shutdown(wait=True)


if __name__ == "__main__":
    main()