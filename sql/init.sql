-- 1. Szövetkezetek / Forrásoldalak (pl. Meló-Diák, MADS, Schönherz)
CREATE TABLE sources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    code VARCHAR(50) NOT NULL UNIQUE, -- pl. 'melodiak', 'mads', 'schonherz'
    base_url TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    last_scraped_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 2. Diákmunka hirdetések (Normalizált központi tábla)
CREATE TABLE job_postings (
    id BIGSERIAL PRIMARY KEY,
    source_id INT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    external_id VARCHAR(255) NOT NULL, -- A hirdetés azonosítója a forrásoldalon
    fingerprint CHAR(64) NOT NULL UNIQUE, -- SHA-256 hash a deduplikációhoz
    
    title VARCHAR(300) NOT NULL,
    category VARCHAR(100), -- Normalizált kategória: 'IT', 'Irodai', 'Fizikai', stb.
    city VARCHAR(100),     -- Normalizált városnév: 'Debrecen', 'Nyíregyháza', 'Budapest'
    is_remote BOOLEAN DEFAULT FALSE,
    
    hourly_wage_min INT,   -- Normalizált órabér (HUF), pl. 2200
    hourly_wage_max INT,   -- Ha sávos a bér, pl. 2800
    wage_raw_text VARCHAR(150), -- Eredeti kiírás: "Br. 2200 - 2800 Ft/óra"
    
    description TEXT,
    url TEXT NOT NULL,
    raw_metadata JSONB DEFAULT '{}'::jsonb, -- Egyéb specifikus adatoknak
    
    is_active BOOLEAN DEFAULT TRUE,
    first_seen_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE (source_id, external_id)
);

-- 3. Felhasználói szűrők (mire kérsz értesítést)
CREATE TABLE saved_filters (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL, -- pl. "Debreceni és Remote IT munkák"
    telegram_chat_id VARCHAR(100) NOT NULL, -- Ahová az üzenet megy
    
    cities VARCHAR(100)[],          -- pl. ARRAY['Debrecen', 'Nyíregyháza']
    categories VARCHAR(100)[],      -- pl. ARRAY['IT', 'Irodai']
    include_remote BOOLEAN DEFAULT TRUE,
    min_hourly_wage INT DEFAULT 0,  -- pl. 2100
    
    keywords_include TEXT[],        -- pl. ARRAY['python', 'java', 'tesztelő', 'dokumentáció']
    keywords_exclude TEXT[],        -- pl. ARRAY['éjszakai', 'nehéz fizikai']
    delivery_mode VARCHAR(20) NOT NULL DEFAULT 'instant'
        CHECK (delivery_mode IN ('instant', 'daily')),
    summary_time TIME NOT NULL DEFAULT '20:00',
    last_summary_date DATE,

    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 4. Kiküldött értesítések naplója (garantálja, hogy nincs duplikált spam)
CREATE TABLE notification_logs (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES job_postings(id) ON DELETE CASCADE,
    filter_id INT NOT NULL REFERENCES saved_filters(id) ON DELETE CASCADE,
    sent_at TIMESTAMPTZ,
    status VARCHAR(30) DEFAULT 'SENT', -- 'QUEUED', 'SENDING', 'SENT', 'FAILED', 'DIGEST_FAILED', 'SKIPPED'
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ,
    error_message TEXT,
    
    -- Ugyanarra a munkára ugyanazzal a szűrővel csak egyszer mehet ki értesítés!
    UNIQUE (job_id, filter_id)
);

-- Telegram polling offset, retained across container restarts.
CREATE TABLE bot_state (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL
);

-- Indexek a gyors lekérdezésekhez és szűréshez
CREATE INDEX idx_jobs_city ON job_postings(city);
CREATE INDEX idx_jobs_wage ON job_postings(hourly_wage_min);
CREATE INDEX idx_jobs_first_seen ON job_postings(first_seen_at DESC);
CREATE INDEX idx_jobs_active ON job_postings(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_saved_filters_digest_due ON saved_filters(summary_time)
    WHERE is_active = TRUE AND delivery_mode = 'daily';
CREATE INDEX idx_notification_digest_retry ON notification_logs(filter_id, next_attempt_at)
    WHERE status IN ('QUEUED', 'SENDING');
