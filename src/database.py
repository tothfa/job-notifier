import json
from typing import Any
from sqlalchemy import create_engine, text
from config import settings
from schemas import JobPostingCreate

engine = create_engine(settings.database_url, pool_pre_ping=True)


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
            FOR UPDATE;
        """), {"chat_id": chat_id}).scalars().all()
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
            SELECT cities, include_remote, min_hourly_wage, keywords_include
            FROM saved_filters
            WHERE telegram_chat_id = :chat_id AND is_active = TRUE
            ORDER BY id
            LIMIT 1;
        """), {"chat_id": chat_id}).mappings().one_or_none()
    return dict(row) if row is not None else None


def disable_chat_filter(chat_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE saved_filters SET is_active = FALSE
            WHERE telegram_chat_id = :chat_id AND is_active = TRUE;
        """), {"chat_id": chat_id})
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