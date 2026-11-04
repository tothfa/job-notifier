import re
from typing import List
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper
from schemas import JobPostingCreate
from services.normalizer import JobNormalizer


class MindDiakScraper(BaseScraper):
    source_code = "minddiak"
    source_name = "Mind-Diák Szövetkezet"
    base_url = "https://minddiak.hu"
    jobs_url = "https://minddiak.hu/diakmunka-226"

    # Magyar nagyvárosok a gyors URL és szöveg alapú felismeréshez
    KNOWN_CITIES = [
        "Debrecen", "Nyíregyháza", "Budapest", "Szeged", "Pécs", "Győr",
        "Miskolc", "Kecskemét", "Székesfehérvár", "Szombathely", "Veszprém",
        "Eger", "Zalaegerszeg", "Tatabánya", "Kaposvár", "Békéscsaba", "Szolnok"
    ]

    def _detect_city(self, slug: str, card_text: str) -> str | None:
        """Kinyeri a várost a kártya szövegéből vagy az URL slugból."""
        slug_clean = slug.lower()
        # Gyakori ékezet nélküli városok az URL-ben
        url_city_map = {
            "debrecen": "Debrecen",
            "nyiregyhaza": "Nyíregyháza",
            "budapest": "Budapest",
            "szeged": "Szeged",
            "pecs": "Pécs",
            "gyor": "Győr",
            "miskolc": "Miskolc",
            "kecskemet": "Kecskemét",
            "szekesfehervar": "Székesfehérvár",
        }
        for key, city_name in url_city_map.items():
            if key in slug_clean:
                return city_name

        for city in self.KNOWN_CITIES:
            if re.search(rf"\b{city}\b", card_text, re.IGNORECASE):
                return city

        return None

    def fetch_jobs(self) -> List[JobPostingCreate]:
        print(f"[SCRAPER] {self.source_name} letöltése: {self.jobs_url} ...")
        jobs: List[JobPostingCreate] = []
        seen_ids = set()

        try:
            with self.get_http_client() as client:
                response = client.get(self.jobs_url)
                response.raise_for_status()
        except Exception as e:
            print(f"[SCRAPER HIBA] {self.source_name} letöltési hiba: {e}")
            return []

        soup = BeautifulSoup(response.text, "lxml")

        # Pontosan a '/diakmunka/{slug}-{szam}' mintájú linkeket keressük
        job_link_pattern = re.compile(r"^/diakmunka/([a-z0-9-]+-(\d+))$")

        for link in soup.find_all("a", href=True):
            href = link["href"].strip()
            match = job_link_pattern.match(href)
            if not match:
                continue

            slug = match.group(1)
            job_num_id = match.group(2)
            external_id = f"MDK-{job_num_id}"

            if external_id in seen_ids:
                continue

            full_url = urljoin(self.base_url, href)
            card = link.find_parent(["article", "div", "li"]) or link
            card_text = card.get_text(" | ", strip=True)

            # Cím kinyerése címsorból, vagy a link szövegéből, végső esetben a slugból
            title_el = card.find(["h2", "h3", "h4", "strong"])
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            if not title or len(title) < 4 or title.lower() in ("részletek", "tovább", "jelentkezem"):
                # Ha a gomb szövege csak "Részletek", az URL-ből képezünk olvasható címet
                title = re.sub(r"-\d+$", "", slug).replace("-", " ").capitalize()

            # Órabér keresése a kártyán
            wage_match = re.search(
                r"((?:br\.?|bruttó)?\s*\d[\d\s\.,-]*\s*ft(?:\s*/\s*(?:óra|ó))?)",
                card_text,
                re.IGNORECASE
            )
            raw_wage = wage_match.group(1).strip() if wage_match else None
            wage_min, wage_max = JobNormalizer.parse_wage(raw_wage)

            raw_city = self._detect_city(slug, card_text)
            clean_city = JobNormalizer.clean_city(raw_city)
            is_remote = JobNormalizer.detect_remote(title=title, city=raw_city or "", description=card_text)

            seen_ids.add(external_id)
            jobs.append(
                JobPostingCreate(
                    source_code=self.source_code,
                    external_id=external_id,
                    title=title,
                    category="Diákmunka",
                    city=clean_city,
                    is_remote=is_remote,
                    hourly_wage_min=wage_min,
                    hourly_wage_max=wage_max,
                    wage_raw_text=raw_wage,
                    description=card_text[:300],
                    url=full_url
                )
            )

        print(f"[SCRAPER OK] {self.source_name}: {len(jobs)} db hirdetés kinyerve.")
        return jobs