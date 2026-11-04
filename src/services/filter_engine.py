from typing import List, Dict, Any
from sqlalchemy import create_engine, text
from config import settings
from schemas import JobPostingCreate
from services.notifier import TelegramNotifier

engine = create_engine(settings.database_url, pool_pre_ping=True)


class FilterEngine:
    def __init__(self):
        self.notifier = TelegramNotifier()

    def _get_active_filters(self) -> List[Dict[str, Any]]:
        """Lekérdezi az összes aktív felhasználói szűrőt az adatbázisból."""
        query = text("""
            SELECT id, name, telegram_chat_id, cities, categories, 
                   include_remote, min_hourly_wage, keywords_include, keywords_exclude
            FROM saved_filters
            WHERE is_active = TRUE;
        """)
        with engine.connect() as conn:
            result = conn.execute(query)
            filters = []
            for row in result.mappings():
                filters.append(dict(row))
            return filters

    def _job_matches_filter(self, job: JobPostingCreate, f: Dict[str, Any]) -> bool:
        """Eldönti, hogy egy adott hirdetés illeszkedik-e a szűrő feltételeire."""
        
        # 1. Minimum órabér szűrés
        min_wage = f.get("min_hourly_wage") or 0
        if min_wage > 0:
            job_wage = job.hourly_wage_min or job.hourly_wage_max or 0
            if job_wage < min_wage:
                return False

        # 2. Város & Távmunka szűrés
        allowed_cities = [city.lower() for city in (f.get("cities") or [])]
        include_remote = f.get("include_remote", True)

        job_city = (job.city or "").lower()
        is_matched_city = any(ac in job_city for ac in allowed_cities) or not allowed_cities

        if job.is_remote and not include_remote:
            return False
        if not job.is_remote and not is_matched_city:
            return False

        allowed_categories = [category.lower() for category in (f.get("categories") or [])]
        if allowed_categories and (job.category or "").lower() not in allowed_categories:
            return False

        full_text = f"{job.title} {job.description or ''}".lower()

        # 3. Kizárt kulcsszavak (Keywords Exclude)
        exclude_keywords = [kw.lower() for kw in (f.get("keywords_exclude") or [])]
        for kw in exclude_keywords:
            if kw in full_text:
                return False

        # 4. Kötelező kulcsszavak (Keywords Include - ha meg vannak adva, legalább egynek illeszkednie kell)
        include_keywords = [kw.lower() for kw in (f.get("keywords_include") or [])]
        if include_keywords:
            has_required_keyword = any(kw in full_text for kw in include_keywords)
            if not has_required_keyword:
                return False

        return True

    def _has_already_notified(self, job_id: int, filter_id: int) -> bool:
        """Ellenőrzi, hogy erre a munkára sikeresen küldtünk-e már értesítést."""
        query = text("""
            SELECT 1 FROM notification_logs 
            WHERE job_id = :job_id AND filter_id = :filter_id AND status = 'SENT';
        """)
        with engine.connect() as conn:
            row = conn.execute(query, {"job_id": job_id, "filter_id": filter_id}).first()
            return row is not None

    def _log_notification(self, job_id: int, filter_id: int, status: str = "SENT", error: str | None = None):
        """Rögzíti az értesítés állapotát, beleértve a később újrapróbálható hibákat."""
        query = text("""
            INSERT INTO notification_logs (job_id, filter_id, status, error_message)
            VALUES (:job_id, :filter_id, :status, :error)
            ON CONFLICT (job_id, filter_id) DO UPDATE SET
                status = EXCLUDED.status,
                error_message = EXCLUDED.error_message,
                sent_at = CASE
                    WHEN EXCLUDED.status = 'SENT' THEN CURRENT_TIMESTAMP
                    ELSE notification_logs.sent_at
                END;
        """)
        with engine.begin() as conn:
            conn.execute(query, {"job_id": job_id, "filter_id": filter_id, "status": status, "error": error})

    def _retry_failed_notifications(self, filters: List[Dict[str, Any]]) -> None:
        """Újraküldi a korábbi ciklusokban sikertelen, továbbra is illeszkedő értesítéseket."""
        filters_by_id = {f["id"]: f for f in filters}
        query = text("""
            SELECT nl.job_id, nl.filter_id, j.external_id, j.title, j.category, j.city,
                   j.is_remote, j.hourly_wage_min, j.hourly_wage_max, j.wage_raw_text,
                   j.description, j.url, j.raw_metadata, s.code AS source_code
            FROM notification_logs nl
            JOIN saved_filters f ON f.id = nl.filter_id
            JOIN job_postings j ON j.id = nl.job_id
            JOIN sources s ON s.id = j.source_id
            WHERE nl.status = 'FAILED' AND f.is_active = TRUE;
        """)
        with engine.connect() as conn:
            failed_notifications = conn.execute(query).mappings().all()

        for row in failed_notifications:
            filter_data = filters_by_id.get(row["filter_id"])
            if filter_data is None:
                continue
            job = JobPostingCreate(
                source_code=row["source_code"],
                external_id=row["external_id"],
                title=row["title"],
                category=row["category"],
                city=row["city"],
                is_remote=row["is_remote"],
                hourly_wage_min=row["hourly_wage_min"],
                hourly_wage_max=row["hourly_wage_max"],
                wage_raw_text=row["wage_raw_text"],
                description=row["description"],
                url=row["url"],
                raw_metadata=row["raw_metadata"] or {},
            )
            if not self._job_matches_filter(job, filter_data):
                continue

            success = self.notifier.send_job_alert(job, chat_id=filter_data["telegram_chat_id"])
            self._log_notification(
                row["job_id"],
                row["filter_id"],
                status="SENT" if success else "FAILED",
                error=None if success else "Telegram API hiba",
            )

    def process_new_jobs(self, new_jobs_with_ids: List[tuple[int, JobPostingCreate]]):
        """
        Összeveti az újonnan mentett hirdetéseket (adatbázis ID-val) a mentett szűrőkkel,
        és elküldi a Telegram értesítéseket.
        """
        filters = self._get_active_filters()
        if not filters:
            print("[SZŰRŐ] Nincs aktív szűrő az adatbázisban! Hozz létre egyet a saved_filters táblában.")
            return

        self._retry_failed_notifications(filters)
        if not new_jobs_with_ids:
            return

        print(f"[SZŰRŐMOTOR] {len(new_jobs_with_ids)} új munka vizsgálata {len(filters)} db szűrő alapján...")

        for job_id, job in new_jobs_with_ids:
            for f in filters:
                # Ha már kapott erről értesítést ez a szűrő, átugorjuk
                if self._has_already_notified(job_id, f["id"]):
                    continue

                # Ha illeszkedik a szűrőre:
                if self._job_matches_filter(job, f):
                    chat_id = f["telegram_chat_id"]
                    print(f"  -> Találat! '{job.title}' illeszkedik a(z) '{f['name']}' szűrőre.")
                    
                    # Küldés Telegramra
                    success = self.notifier.send_job_alert(job, chat_id=chat_id)
                    
                    status = "SENT" if success else "FAILED"
                    error_msg = None if success else "Telegram API hiba"
                    
                    # Naplózás
                    self._log_notification(job_id, f["id"], status=status, error=error_msg)