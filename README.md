# Diákmunka-értesítő

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Licenc: MIT](https://img.shields.io/badge/Licenc-MIT-blue.svg)](LICENSE)

**Verzió:** `0.1.0` · **Állapot:** fejlesztés alatt

Docker Compose alapú háttérszolgáltatás, amely magyar diákszövetkezetek álláshirdetéseit gyűjti, normalizálja és PostgreSQL adatbázisban tárolja, majd a beállított szűrési feltételek alapján Telegram-értesítéseket küld.

## Funkciók

- 5 diákszövetkezet periodikus monitorozása (10 perces ciklusidő).
- SHA-256 fingerprint alapú deduplikáció.
- Normalizált adattárolás (bruttó órabér, egységes városnevek, távmunka státusz).
- Személyre szabható Telegram-szűrő (város, távmunka, minimum órabér, kulcsszavak).
- Rugalmas kézbesítés: azonnali értesítés vagy időzített napi összefoglaló.
- Hibatűrő működés: egyetlen forrás vagy hálózati kérés hibája nem akasztja meg a többi scraper futását.

## Támogatott források

| Forrás | Weboldal | Adatgyűjtés módja | Lefedettség |
| :--- | :--- | :--- | :--- |
| Schönherz | [schonherz.hu](https://www.schonherz.hu/) | HTML scraping | Budapest, Debrecen |
| Meló-Diák | [melodiak.hu](https://www.melodiak.hu/) | REST API | Országos |
| Mind-Diák | [minddiak.hu](https://minddiak.hu/) | HTML scraping | Országos |
| MADS | [mads.hu](https://mads.hu/) | HTML + JSON-LD | Országos |
| MŰISZ | [muisz.hu](https://muisz.hu/) | Angular Universal (SSR) | Országos |

## Működés

```text
Források → HTTPX scraperek → Normalizálás → PostgreSQL (Deduplikáció)
                                                   ↓
Telegram felhasználó ← Bot értesítő ← FilterEngine (Szűrés)
```

1. **Adatgyűjtés:** 10 percenként lekérdezi az 5 forrásoldalt.
2. **Normalizálás és tárolás:** Kiszűri a duplikátumokat és menti az új rekordokat.
3. **Szűrés és értesítés:** Összeveti az új állásokat az aktív Telegram-szűrőkkel (azonnal vagy napi összefoglalóként).

## Telepítés és indítás

### Követelmények
- Docker Engine és Docker Compose plugin
- Telegram Bot token ([@BotFather](https://t.me/BotFather))

### Lépések

1. Repository klónozása:
   ```sh
   git clone https://github.com/tothfa/job-notifier.git
   cd job-notifier
   ```

2. Környezeti változók beállítása:
   ```sh
   cp .env.example .env
   ```
   Állítsd be a `.env` fájlban az adatbázis adatokat (`DB_USER`, `DB_PASSWORD`, `DB_NAME`) és a `TELEGRAM_BOT_TOKEN`-t.

3. Szolgáltatás indítása:
   ```sh
   docker compose up -d --build
   ```

## Környezeti változók

| Változó | Kötelező | Alapértelmezett | Leírás |
| :--- | :---: | :---: | :--- |
| `DB_USER` | Igen | - | PostgreSQL felhasználónév |
| `DB_PASSWORD` | Igen | - | PostgreSQL jelszó |
| `DB_NAME` | Igen | - | Adatbázis neve |
| `DB_HOST` | Igen | `postgres` | Adatbázis hostnév Docker hálózaton |
| `DB_PORT` | Nem | `5432` | Adatbázis port |
| `TELEGRAM_BOT_TOKEN` | Igen | - | Telegram Bot API token |
| `LOG_LEVEL` | Nem | `INFO` | Naplózási szint |

## Telegram-parancsok

| Parancs | Leírás |
| :--- | :--- |
| `/start`, `/help` | Súgó megjelenítése |
| `/filter` | Szűrő beállítása vagy módosítása |
| `/myfilter` | Aktív szűrő megtekintése |
| `/notify` | Értesítési mód lekérdezése |
| `/notify instant` | Azonnali értesítés minden új találatról |
| `/notify daily HH:MM` | Napi összefoglaló adott időpontban (pl. `/notify daily 20:00`) |
| `/notify retry` | Sikertelen összefoglalók újrapróbálása |
| `/stopfilter` | Értesítések szüneteltetése |
| `/cancel` | Folyamatban lévő beállítás megszakítása |

## Üzemeltetés

Konténerek állapota:
```sh
docker compose ps
```

Naplók követése:
```sh
docker compose logs -f bot-service
```

Leállítás az adatok megőrzésével:
```sh
docker compose down
```

## Licenc

[MIT](LICENSE)
