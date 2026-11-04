# Diákmunka-értesítő

**Verzió:** `0.1.0` · **Állapot:** fejlesztés alatt

Docker Compose-ban futó, nyílt forrású alkalmazás, amely magyar diákszövetkezetek álláshirdetéseit figyeli, és az új, személyes feltételeknek megfelelő munkákról Telegramon értesít. A szűrők Telegram-parancsokkal állíthatók be; a használatukhoz nem kell adatbázist vagy konfigurációs fájlt szerkeszteni.

> A projekt korai, 1.0 előtti verzió. A forrásoldalak felépítése változhat, ezért a scraperek időnként karbantartást igényelhetnek.

## Funkciók

- Négy diákmunkás forrás rendszeres lekérdezése.
- Az állások egységesítése és PostgreSQL-ben való tárolása.
- Duplikált hirdetések kiszűrése.
- Telegram-szűrő város, távmunka, minimum bruttó órabér és kulcsszavak szerint.
- Az új, szűrőnek megfelelő állásokról Telegram-értesítés.
- Indulás utáni azonnali scrape, majd ismétlés 10 percenként.
- Docker Compose-alapú futtatás és tartós adatbázis-kötet.

## Források

- [Schönherz](https://www.schonherz.hu/)
- [Meló-Diák](https://www.melodiak.hu/)
- [Mind-Diák](https://minddiak.hu/)
- [MADS](https://mads.hu/)

A forrásoldalak tartalma és technikai felépítése külső szolgáltatók ellenőrzése alatt áll; változásuk átmenetileg megakadályozhatja az adatgyűjtést. Futtatás előtt ellenőrizd a források felhasználási feltételeit és az automatizált lekérésekre vonatkozó szabályait.

## Működés és technológiák

Az alkalmazás két feladatot futtat egy konténerben: az APScheduler ütemezi az adatgyűjtést, a Telegram bot pedig külön szálon long pollinggal fogadja a felhasználói parancsokat.

```text
Forrásoldalak → HTTPX scraperek → normalizálás → PostgreSQL
                                                   ↓
Telegram szűrő ← parancsbot             illeszkedő új állás
                                                   ↓
                                         Telegram értesítés
```

| Terület | Technológia |
| --- | --- |
| Futási környezet | Python 3.12 |
| Adatbázis | PostgreSQL 16, SQLAlchemy 2, psycopg 3 |
| Adatellenőrzés és konfiguráció | Pydantic 2, pydantic-settings |
| HTTP és HTML-feldolgozás | HTTPX, Beautiful Soup 4, lxml |
| Ütemezés | APScheduler 3 |
| Telepítés | Docker, Docker Compose |
| Értesítések és parancsok | Telegram Bot API |

A függőségek alsó verziókorlátait a [`requirements.txt`](requirements.txt) tartalmazza.

## Követelmények

- Docker Engine és a Docker Compose plugin.
- Internetkapcsolat az állásoldalak és a Telegram Bot API eléréséhez.
- Telegram bot token, amelyet a Telegram [@BotFather](https://t.me/BotFather) segítségével hozhatsz létre.

## Telepítés és indítás

1. Klónozd a repositoryt, majd lépj a projekt könyvtárába.
2. Készíts helyi konfigurációt a mintából:

   ```sh
   cp .env.example .env
   ```

3. Nyisd meg a `.env` fájlt, és állítsd be:
   - a PostgreSQL felhasználónevet (`DB_USER`), adatbázisnevet (`DB_NAME`) és egyedi jelszót (`DB_PASSWORD`);
   - a BotFather által adott tokent (`TELEGRAM_BOT_TOKEN`).

   Erős adatbázis-jelszó generálásához például:

   ```sh
   openssl rand -hex 32
   ```

   A bot tokenjét és az adatbázis-jelszót kezeld titokként: ne másold nyilvános issue-ba, naplóba vagy verziókezelt fájlba.

4. Indítsd el az alkalmazást és az adatbázist:

   ```sh
   docker compose up -d --build
   ```

Az alkalmazás az indulás után azonnal elindítja az első adatgyűjtést, majd 10 percenként ismétli azt. A PostgreSQL portja nincs kitéve a host hálózatra; az alkalmazás a Compose hálózatán csatlakozik hozzá.

### Környezeti változók

| Változó | Szükséges | Leírás |
| --- | --- | --- |
| `DB_USER` | Igen | PostgreSQL felhasználónév |
| `DB_PASSWORD` | Igen | PostgreSQL jelszó; egyedi, erős értéket használj |
| `DB_NAME` | Igen | PostgreSQL adatbázis neve |
| `DB_HOST` | Igen | Compose alatt az adatbázis szolgáltatásneve (`postgres`) |
| `DB_PORT` | Nem | Adatbázis-port; alapértelmezés: `5432` |
| `TELEGRAM_BOT_TOKEN` | Igen | A Telegram BotFather tokenje |
| `LOG_LEVEL` | Nem | Naplózási szint; alapértelmezés: `INFO` |

A Compose konfiguráció a `DB_HOST` és `DB_PORT` értékét a belső adatbázis-szolgáltatásra állítja. A `DB_PASSWORD`, `DB_USER` és `DB_NAME` értékeinek egy már inicializált adatbázis-kötetnél egyezniük kell az adatbázisban beállított adatokkal. A `.env` módosítása önmagában nem változtatja meg egy meglévő PostgreSQL-felhasználó jelszavát. Az inicializáló SQL csak új adatbázis-kötet első létrehozásakor fut le.

## Telegram-szűrő beállítása

1. Nyisd meg a bot privát beszélgetését, és küldd el a `/start` parancsot.
2. Küldd el a `/filter` parancsot, majd válaszolj a kérdésekre:
   - város(ok), vesszővel elválasztva, például `Debrecen, Budapest`;
   - jöhet-e távmunka (`igen` vagy `nem`);
   - minimum bruttó órabér forintban, például `2200`;
   - keresett kulcsszavak, vesszővel elválasztva, például `python, tesztelő`.

A `-` válasszal kihagyott feltétel alapértelmezése: minden város, távmunka engedélyezése, minimum bér nélkül, illetve kulcsszó-korlátozás nélkül. Több megadott kulcsszó esetén legalább az egyiknek szerepelnie kell a hirdetés címében vagy leírásában. Egy Telegram-chathez egy aktív szűrő tartozik; a `/filter` paranccsal módosíthatod.

| Parancs | Művelet |
| --- | --- |
| `/start`, `/help` | Súgó megjelenítése |
| `/filter` | Szűrő létrehozása vagy módosítása |
| `/myfilter` | Aktív szűrő megtekintése |
| `/stopfilter` | Értesítések kikapcsolása |
| `/cancel` | Folyamatban lévő szűrőbeállítás megszakítása |

Az alkalmazás az újonnan felismert hirdetéseket küldi ki; a szűrő létrehozásakor az adatbázisban már korábban tárolt állásokhoz nem küld automatikusan visszamenőleges értesítést. Az átmenetileg sikertelen értesítésekkel a rendszer a következő adatgyűjtési ciklusokban újrapróbálkozik.

## Üzemeltetés

Állapot ellenőrzése:

```sh
docker compose ps
```

Alkalmazásnaplók követése:

```sh
docker compose logs -f bot-service
```

Konténerek leállítása az adatbázis megőrzésével:

```sh
docker compose down
```

Az álláshirdetések, szűrők és értesítési naplók a PostgreSQL `pgdata` kötetében maradnak. A projekt nem végez automatikus adatbázis-takarítást. A `docker compose down -v` parancs az adatbázis-kötetet és az abban tárolt adatokat is törli; csak akkor használd, ha valóban el akarod veszíteni ezeket az adatokat.

## Biztonság és ismert korlátok

- A `.env` helyi konfiguráció; ne töltsd fel. A `.env.example` nem tartalmaz valódi jelszót vagy bot tokent.
- A bot jelenleg nem használ külön felhasználói engedélylistát. A botot elérő Telegram-chatek szűrőt állíthatnak be; ezt vedd figyelembe, ha saját példányt üzemeltetsz.
- Egy bot tokent egyszerre csak egy long pollingot használó példányhoz rendelj; párhuzamos polling vagy webhook ütközést okozhat.
- A scrape-elés külső oldalak struktúrájától függ. Egy forrás hibája naplózásra kerül, de a többi forrás lekérése ettől még folytatódik.
- A projekt 1.0 előtti fejlesztési állapotú; nincs garantált rendelkezésre állás vagy visszafelé kompatibilis adatbázis-migráció.

## GitHub Actions

A CI minden push és pull request esetén ellenőrzi a Python-szintaxist és a verziójelölést, Gitleaks segítségével titokmintákat keres, validálja a Compose-konfigurációt, és felépíti a Docker image-et. A titokellenőrző nyílt forrású, rögzített verziójú konténerben fut; a workflow nem használ éles adatbázis- vagy Telegram-hitelesítő adatokat.

## Közreműködés

Hibajelzés vagy fejlesztési javaslat esetén nyiss GitHub issue-t. Biztonsági sérülékenységet ne jelents nyilvános issue-ban; használd a repository privát sérülékenység-jelentési funkcióját, ha az engedélyezett. Pull request előtt ellenőrizd, hogy a módosítás nem tartalmaz tokent, jelszót, helyi konfigurációt vagy adatbázis-dumpot.

## Licenc

A projekt az [MIT licenc](LICENSE) feltételei szerint használható.
