import re
from typing import List
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper
from schemas import JobPostingCreate
from services.normalizer import JobNormalizer


class MadsScraper(BaseScraper):
    source_code = "mads"
    source_name = "MADS Iskolaszövetkezet"
    base_url = "https://mads.hu"
    jobs_url = "https://mads.hu/munkak/diak"

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

        # Minta: /munkak/diak/{kategoria}/{slug}-{id} (de NEM /jelentkezes/email a végén!)
        job_link_pattern = re.compile(r"^/munkak/diak/([^/]+)/([a-z0-9-]+-(\d+))/?$")

        for link in soup.find_all("a", href=True):
            href = link["href"].strip()
            match = job_link_pattern.match(href)
            if not match:
                continue

            raw_category = match.group(1).replace("-", " ").title()
            slug = match.group(2)
            job_num_id = match.group(3)
            external_id = f"MADS-{job_num_id}"

            if external_id in seen_ids:
                continue

            full_url = urljoin(self.base_url, href)
            card = link.find_parent(["article", "div", "li"]) or link
            card_text = card.get_text(" | ", strip=True)

            title_el = card.find(["h2", "h3", "h4", "strong"])
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)
            if not title or len(title) < 4 or "jelentkez" in title.lower():
                title = re.sub(r"-\d+$", "", slug).replace("-", " ").capitalize()

            wage_match = re.search(
                r"((?:br\.?|bruttó)?\s*\d[\d\s\.,-]*\s*ft(?:\s*/\s*(?:óra|ó))?)",
                card_text,
                re.IGNORECASE
            )
            raw_wage = wage_match.group(1).strip() if wage_match else None
            wage_min, wage_max = JobNormalizer.parse_wage(raw_wage)

            city_match = re.search(
                r"\b(Debrecen|Nyíregyháza|Budapest|Szeged|Pécs|Győr|Miskolc|Tatabánya|Maglód|Soroksár|Törökbálint|Budaörs)\b",
                card_text + " " + slug.replace("-", " "),
                re.IGNORECASE
            )
            raw_city = city_match.group(1) if city_match else None
            clean_city = JobNormalizer.clean_city(raw_city)
            is_remote = JobNormalizer.detect_remote(title=title, city=raw_city or "", description=card_text)

            seen_ids.add(external_id)
            jobs.append(
                JobPostingCreate(
                    source_code=self.source_code,
                    external_id=external_id,
                    title=title,
                    category=raw_category,
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