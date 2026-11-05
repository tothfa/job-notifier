import json
import logging
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterator
from sqlalchemy import create_engine, text
from config import settings
from schemas import JobPostingCreate

engine = create_engine(settings.database_url, pool_pre_ping=True)
logger = logging.getLogger("JobNotifier.Database")


def verify_db_connection() -> bool:
    """Ellenőrzi, hogy él-e a kapcsolat és léteznek-e a táblák."""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM sources;"))
            count = result.scalar()
            print(f"[DB OK] Sikeres csatlakozás! Regisztrált források száma: {count}")
            return True
    except Exception as e:
        print(f"[DB HIBA] Nem sikerült csatlakozni az adatbázishoz: {e}")
        return False


def ensure_runtime_schema() -> None:
    """Create runtime-owned state storage for existing database volumes."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bot_state (
                key VARCHAR(100) PRIMARY KEY,
                value TEXT NOT NULL
            );
        """))
        conn.execute(text("""
            ALTER TABLE saved_filters
            ADD COLUMN IF NOT EXISTS delivery_mode VARCHAR(20) NOT NULL DEFAULT 'instant'
                CHECK (delivery_mode IN ('instant', 'daily'));
        """))
        conn.execute(text("""
            ALTER TABLE saved_filters
            ADD COLUMN IF NOT EXISTS summary_time TIME NOT NULL DEFAULT '20:00';
        """))
        conn.execute(text("""
            ALTER TABLE saved_filters
            ADD COLUMN IF NOT EXISTS last_summary_date DATE;
        """))
        conn.execute(text("""
            ALTER TABLE notification_logs
            ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;
        """))
        conn.execute(text("""
            ALTER TABLE notification_logs
            ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ;
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_saved_filters_digest_due
            ON saved_filters (summary_time)
            WHERE is_active = TRUE AND delivery_mode = 'daily';
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_notification_digest_retry
            ON notification_logs (filter_id, next_attempt_at)
            WHERE status IN ('QUEUED', 'SENDING');
        """))


@contextmanager
def digest_filter_lock(filter_id: int) -> Iterator[bool]:
    """Serialize digest dispatch and filter changes for a single saved filter."""
    with engine.connect() as conn:
        conn.begin()
        acquired = conn.execute(
            text("SELECT pg_try_advisory_xact_lock(1, :filter_id);"),
            {"filter_id": filter_id},
        ).scalar_one()
        try:
            yield acquired
        finally:
            conn.rollback()


def get_update_offset() -> int | None:
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT value FROM bot_state WHERE key = 'telegram_update_offset';")
        ).scalar_one_or_none()
    return int(value) if value is not None else None


def save_update_offset(offset: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO bot_state (key, value)
            VALUES ('telegram_update_offset', :value)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
        """), {"value": str(offset)})


def save_chat_filter(
    chat_id: str,
    cities: list[str] | None,
    include_remote: bool,
    min_hourly_wage: int,
    keywords_include: list[str] | None,
) -> int:
    """Create or replace the single active filter for one Telegram chat."""
    with engine.begin() as conn:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:chat_id));"),
            {"chat_id": chat_id},
        )
        rows = conn.execute(text("""
            SELECT id FROM saved_filters
            WHERE telegram_chat_id = :chat_id AND is_active = TRUE
            ORDER BY id
        """), {"chat_id": chat_id}).scalars().all()
        for active_filter_id in rows:
            conn.execute(
                text("SELECT pg_advisory_xact_lock(1, :filter_id);"),
                {"filter_id": active_filter_id},
            )
        if rows:
            conn.execute(text("""
                SELECT id FROM saved_filters
                WHERE id = ANY(:filter_ids)
                FOR UPDATE;
            """), {"filter_ids": rows}).all()
        filter_id = rows[0] if rows else None

        if filter_id is None:
            filter_id = conn.execute(text("""
                INSERT INTO saved_filters (
                    name, telegram_chat_id, cities, include_remote, min_hourly_wage,
                    keywords_include, is_active
                ) VALUES (
                    'Telegram szűrő', :chat_id, :cities, :include_remote,
                    :min_hourly_wage, :keywords_include, TRUE
                )
                RETURNING id;
            """), {
                "chat_id": chat_id,
                "cities": cities,
                "include_remote": include_remote,
                "min_hourly_wage": min_hourly_wage,
                "keywords_include": keywords_include,
            }).scalar_one()
        else:
            conn.execute(text("""
                UPDATE saved_filters
                SET cities = :cities,
                    categories = NULL,
                    include_remote = :include_remote,
                    min_hourly_wage = :min_hourly_wage,
                    keywords_include = :keywords_include,
                    keywords_exclude = NULL,
                    name = 'Telegram szűrő'
                WHERE id = :filter_id;
            """), {
                "filter_id": filter_id,
                "cities": cities,
                "include_remote": include_remote,
                "min_hourly_wage": min_hourly_wage,
                "keywords_include": keywords_include,
            })

        conn.execute(text("""
            UPDATE saved_filters SET is_active = FALSE
            WHERE telegram_chat_id = :chat_id AND is_active = TRUE AND id != :filter_id;
        """), {"chat_id": chat_id, "filter_id": filter_id})

    return filter_id


def get_chat_filter(chat_id: str) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT cities, include_remote, min_hourly_wage, keywords_include,
                   delivery_mode, summary_time
            FROM saved_filters
            WHERE telegram_chat_id = :chat_id AND is_active = TRUE
            ORDER BY id
            LIMIT 1;
        """), {"chat_id": chat_id}).mappings().one_or_none()
    return dict(row) if row is not None else None


def set_chat_notification_preference(
    chat_id: str,
    delivery_mode: str,
    summary_time: time,
) -> None:
    if delivery_mode not in {"instant", "daily"}:
        raise ValueError("Unsupported notification delivery mode.")

    with engine.begin() as conn:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:chat_id));"),
            {"chat_id": chat_id},
        )
        row = conn.execute(text("""
            SELECT id FROM saved_filters
            WHERE telegram_chat_id = :chat_id AND is_active = TRUE
            ORDER BY id
            LIMIT 1
        """), {"chat_id": chat_id}).first()
        if row is None:
            raise ValueError("An active filter is required to set notification preferences.")

        filter_id = row[0]
        conn.execute(
            text("SELECT pg_advisory_xact_lock(1, :filter_id);"),
            {"filter_id": filter_id},
        )
        conn.execute(
            text("SELECT id FROM saved_filters WHERE id = :filter_id FOR UPDATE;"),
            {"filter_id": filter_id},
        ).one()
        conn.execute(text("""
            UPDATE saved_filters
            SET delivery_mode = :delivery_mode, summary_time = :summary_time
            WHERE id = :filter_id;
        """), {
            "delivery_mode": delivery_mode,
            "summary_time": summary_time,
            "filter_id": filter_id,
        })
        if delivery_mode == "daily":
            result = conn.execute(text("""
                UPDATE notification_logs
                SET status = 'QUEUED', error_message = NULL, attempt_count = 0,
                    next_attempt_at = NULL
                WHERE filter_id = :filter_id AND status IN ('FAILED', 'DIGEST_FAILED');
            """), {"filter_id": filter_id})
            if result.rowcount:
                conn.execute(text("""
                    UPDATE saved_filters SET last_summary_date = NULL
                    WHERE id = :filter_id;
                """), {"filter_id": filter_id})
        else:
            conn.execute(text("""
                UPDATE notification_logs SET status = 'FAILED'
                WHERE filter_id = :filter_id AND status IN ('QUEUED', 'SENDING');
            """), {"filter_id": filter_id})


def get_due_daily_filters(current_time: time, today: date) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, telegram_chat_id
            FROM saved_filters
            WHERE is_active = TRUE
              AND delivery_mode = 'daily'
              AND summary_time <= :current_time
              AND (last_summary_date IS NULL OR last_summary_date < :today)
            ORDER BY id;
        """), {"current_time": current_time, "today": today}).mappings().all()
    return [dict(row) for row in rows]


def get_queued_notifications(filter_id: int) -> list[tuple[int, JobPostingCreate]]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT nl.job_id, j.external_id, j.title, j.category, j.city,
                   j.is_remote, j.hourly_wage_min, j.hourly_wage_max, j.wage_raw_text,
                   j.description, j.url, j.raw_metadata, s.code AS source_code
            FROM notification_logs nl
            JOIN job_postings j ON j.id = nl.job_id
            JOIN sources s ON s.id = j.source_id
            WHERE nl.filter_id = :filter_id AND nl.status = 'QUEUED'
            ORDER BY j.first_seen_at, j.id;
        """), {"filter_id": filter_id}).mappings().all()

    return [
        (
            row["job_id"],
            JobPostingCreate(
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
            ),
        )
        for row in rows
    ]


def get_active_filter(filter_id: int) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT id, name, telegram_chat_id, cities, categories,
                   include_remote, min_hourly_wage, keywords_include, keywords_exclude,
                   delivery_mode, summary_time
            FROM saved_filters
            WHERE id = :filter_id AND is_active = TRUE;
        """), {"filter_id": filter_id}).mappings().one_or_none()
    return dict(row) if row is not None else None


def _retry_delay(attempt_count: int) -> timedelta:
    seconds = min(60 * (2 ** max(attempt_count - 1, 0)), 6 * 60 * 60)
    return timedelta(seconds=seconds)


def claim_digest_notifications(
    filter_id: int,
    now: datetime | None = None,
) -> list[tuple[int, JobPostingCreate]]:
    current = now or datetime.now(timezone.utc)
    with engine.begin() as conn:
        interrupted = conn.execute(text("""
            SELECT job_id, attempt_count
            FROM notification_logs
            WHERE filter_id = :filter_id AND status = 'SENDING';
        """), {"filter_id": filter_id}).mappings().all()
        for row in interrupted:
            attempts = row["attempt_count"]
            if attempts >= 8:
                status = "DIGEST_FAILED"
                next_attempt = None
                error = "Delivery outcome unknown after interrupted send; retry limit reached."
                logger.warning(
                    "Digest retry limit reached after interrupted delivery (filter_id=%s, job_id=%s).",
                    filter_id,
                    row["job_id"],
                )
            else:
                status = "QUEUED"
                next_attempt = current + _retry_delay(attempts)
                error = "Delivery outcome unknown after interrupted send; retry scheduled."
            conn.execute(text("""
                UPDATE notification_logs
                SET status = :status, next_attempt_at = :next_attempt,
                    error_message = :error
                WHERE filter_id = :filter_id AND job_id = :job_id AND status = 'SENDING';
            """), {
                "status": status,
                "next_attempt": next_attempt,
                "error": error,
                "filter_id": filter_id,
                "job_id": row["job_id"],
            })

        rows = conn.execute(text("""
            SELECT nl.job_id, nl.attempt_count, j.external_id, j.title, j.category, j.city,
                   j.is_remote, j.hourly_wage_min, j.hourly_wage_max, j.wage_raw_text,
                   j.description, j.url, j.raw_metadata, s.code AS source_code
            FROM notification_logs nl
            JOIN saved_filters f ON f.id = nl.filter_id
            JOIN job_postings j ON j.id = nl.job_id
            JOIN sources s ON s.id = j.source_id
            WHERE nl.filter_id = :filter_id
              AND nl.status = 'QUEUED'
              AND (nl.next_attempt_at IS NULL OR nl.next_attempt_at <= :now)
              AND f.is_active = TRUE AND f.delivery_mode = 'daily'
            ORDER BY j.first_seen_at, j.id
            FOR UPDATE OF nl;
        """), {"filter_id": filter_id, "now": current}).mappings().all()

        for row in rows:
            conn.execute(text("""
                UPDATE notification_logs
                SET status = 'SENDING', attempt_count = attempt_count + 1,
                    next_attempt_at = NULL, error_message = NULL
                WHERE filter_id = :filter_id AND job_id = :job_id AND status = 'QUEUED';
            """), {"filter_id": filter_id, "job_id": row["job_id"]})

    return [
        (
            row["job_id"],
            JobPostingCreate(
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
            ),
        )
        for row in rows
    ]


def mark_notifications_sent(filter_id: int, job_ids: list[int]) -> None:
    if not job_ids:
        return
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE notification_logs
            SET status = 'SENT', sent_at = CURRENT_TIMESTAMP, error_message = NULL,
                next_attempt_at = NULL
            WHERE filter_id = :filter_id AND job_id = :job_id AND status = 'SENDING';
        """), [
            {"filter_id": filter_id, "job_id": job_id}
            for job_id in job_ids
        ])


def mark_notifications_skipped(filter_id: int, job_ids: list[int]) -> None:
    if not job_ids:
        return
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE notification_logs
            SET status = 'SKIPPED', next_attempt_at = NULL,
                error_message = 'No longer matches the active filter.'
            WHERE filter_id = :filter_id AND job_id = :job_id
              AND status IN ('QUEUED', 'SENDING');
        """), [
            {"filter_id": filter_id, "job_id": job_id}
            for job_id in job_ids
        ])


def record_digest_chunk_failure(
    filter_id: int,
    job_ids: list[int],
    now: datetime | None = None,
) -> int:
    if not job_ids:
        return 0
    current = now or datetime.now(timezone.utc)
    terminal_count = 0
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT job_id, attempt_count
            FROM notification_logs
            WHERE filter_id = :filter_id AND job_id = ANY(:job_ids) AND status = 'SENDING';
        """), {"filter_id": filter_id, "job_ids": job_ids}).mappings().all()
        for row in rows:
            terminal = row["attempt_count"] >= 8
            terminal_count += int(terminal)
            conn.execute(text("""
                UPDATE notification_logs
                SET status = :status,
                    next_attempt_at = :next_attempt,
                    error_message = 'Telegram digest delivery failed.'
                WHERE filter_id = :filter_id AND job_id = :job_id AND status = 'SENDING';
            """), {
                "status": "DIGEST_FAILED" if terminal else "QUEUED",
                "next_attempt": None if terminal else current + _retry_delay(row["attempt_count"]),
                "filter_id": filter_id,
                "job_id": row["job_id"],
            })
    return terminal_count


def has_pending_digest_notifications(filter_id: int) -> bool:
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM notification_logs
                WHERE filter_id = :filter_id AND status IN ('QUEUED', 'SENDING')
            );
        """), {"filter_id": filter_id}).scalar_one()


def retry_failed_digest_notifications(chat_id: str) -> int:
    with engine.begin() as conn:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:chat_id));"),
            {"chat_id": chat_id},
        )
        row = conn.execute(text("""
            SELECT id, delivery_mode FROM saved_filters
            WHERE telegram_chat_id = :chat_id AND is_active = TRUE
            ORDER BY id LIMIT 1;
        """), {"chat_id": chat_id}).first()
        if row is None or row[1] != "daily":
            return 0
        filter_id = row[0]
        conn.execute(
            text("SELECT pg_advisory_xact_lock(1, :filter_id);"),
            {"filter_id": filter_id},
        )
        conn.execute(
            text("SELECT id FROM saved_filters WHERE id = :filter_id FOR UPDATE;"),
            {"filter_id": filter_id},
        ).one()
        result = conn.execute(text("""
            UPDATE notification_logs
            SET status = 'QUEUED', attempt_count = 0, next_attempt_at = NULL,
                error_message = NULL
            WHERE filter_id = :filter_id AND status = 'DIGEST_FAILED';
        """), {"filter_id": filter_id})
        if result.rowcount:
            conn.execute(text("""
                UPDATE saved_filters SET last_summary_date = NULL
                WHERE id = :filter_id;
            """), {"filter_id": filter_id})
        return result.rowcount


def mark_daily_summary_sent(filter_id: int, today: date) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE saved_filters
            SET last_summary_date = :today
            WHERE id = :filter_id AND is_active = TRUE AND delivery_mode = 'daily';
        """), {"filter_id": filter_id, "today": today})


def disable_chat_filter(chat_id: str) -> bool:
    with engine.begin() as conn:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:chat_id));"),
            {"chat_id": chat_id},
        )
        filter_ids = conn.execute(text("""
            SELECT id FROM saved_filters
            WHERE telegram_chat_id = :chat_id AND is_active = TRUE
            ORDER BY id;
        """), {"chat_id": chat_id}).scalars().all()
        for filter_id in filter_ids:
            conn.execute(
                text("SELECT pg_advisory_xact_lock(1, :filter_id);"),
                {"filter_id": filter_id},
            )
        result = conn.execute(text("""
            UPDATE saved_filters SET is_active = FALSE
            WHERE telegram_chat_id = :chat_id AND is_active = TRUE;
        """), {"chat_id": chat_id})
        if filter_ids:
            conn.execute(text("""
                UPDATE notification_logs
                SET status = 'SKIPPED', next_attempt_at = NULL,
                    error_message = 'Filter was disabled.'
                WHERE filter_id = ANY(:filter_ids)
                  AND status IN ('QUEUED', 'SENDING');
            """), {"filter_ids": filter_ids})
    return result.rowcount > 0


def ensure_source_exists(code: str, name: str, base_url: str) -> int:
    """
    Regisztrálja a diákmunka szövetkezetet a 'sources' táblában, ha még nincs bent,
    és visszaadja a belső adatbázis ID-ját.
    """
    query = text("""
        INSERT INTO sources (code, name, base_url)
        VALUES (:code, :name, :base_url)
        ON CONFLICT (code) DO UPDATE 
            SET name = EXCLUDED.name,
                base_url = EXCLUDED.base_url
        RETURNING id;
    """)
    with engine.begin() as conn:
        result = conn.execute(query, {"code": code, "name": name, "base_url": base_url})
        return result.scalar_one()


def save_scraped_jobs(source_id: int, jobs: list[JobPostingCreate]) -> list[tuple[int, JobPostingCreate]]:
    """
    Elmenti a begyűjtött munkákat (Upsert).
    Visszaadja a frissen beszúrt új munkák listáját (Adatbázis ID, JobPostingCreate objektum).
    """
    if not jobs:
        return []

    upsert_query = text("""
        INSERT INTO job_postings (
            source_id, external_id, fingerprint, title, category, city,
            is_remote, hourly_wage_min, hourly_wage_max, wage_raw_text,
            description, url, raw_metadata, last_seen_at
        ) VALUES (
            :source_id, :external_id, :fingerprint, :title, :category, :city,
            :is_remote, :hourly_wage_min, :hourly_wage_max, :wage_raw_text,
            :description, :url, CAST(:raw_metadata AS jsonb), CURRENT_TIMESTAMP
        )
        ON CONFLICT (fingerprint) DO UPDATE SET
            title = EXCLUDED.title,
            category = EXCLUDED.category,
            city = EXCLUDED.city,
            is_remote = EXCLUDED.is_remote,
            hourly_wage_min = EXCLUDED.hourly_wage_min,
            hourly_wage_max = EXCLUDED.hourly_wage_max,
            wage_raw_text = EXCLUDED.wage_raw_text,
            description = EXCLUDED.description,
            url = EXCLUDED.url,
            raw_metadata = EXCLUDED.raw_metadata,
            last_seen_at = CURRENT_TIMESTAMP,
            is_active = TRUE
        RETURNING id, (xmax = 0) AS is_new;
    """)

    update_source_query = text("""
        UPDATE sources SET last_scraped_at = CURRENT_TIMESTAMP WHERE id = :source_id;
    """)

    new_jobs_with_ids: list[tuple[int, JobPostingCreate]] = []

    with engine.begin() as conn:
        for job in jobs:
            params = {
                "source_id": source_id,
                "external_id": job.external_id,
                "fingerprint": job.fingerprint,
                "title": job.title,
                "category": job.category,
                "city": job.city,
                "is_remote": job.is_remote,
                "hourly_wage_min": job.hourly_wage_min,
                "hourly_wage_max": job.hourly_wage_max,
                "wage_raw_text": job.wage_raw_text,
                "description": job.description,
                "url": job.url,
                "raw_metadata": json.dumps(job.raw_metadata, ensure_ascii=False)
            }
            row = conn.execute(upsert_query, params).mappings().one()
            if row["is_new"]:
                new_jobs_with_ids.append((row["id"], job))

        conn.execute(update_source_query, {"source_id": source_id})

    return new_jobs_with_ids