import logging
import re
import time
from typing import Any, List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper
from schemas import JobPostingCreate
from services.normalizer import JobNormalizer

logger = logging.getLogger("JobNotifier.Scrapers.Muisz")


class MuiszScraper(BaseScraper):
    source_code = "muisz"
    source_name = "MŰISZ Iskolaszövetkezet"
    base_url = "https://muisz.hu"
    jobs_url = "https://muisz.hu/hu/diakmunkaink"
    max_pages = 25
    page_delay_seconds = 0.2

    def fetch_jobs(self) -> List[JobPostingCreate]:
        print(f"[SCRAPER] {self.source_name} bejárása indul: {self.jobs_url} ...")
        jobs: List[JobPostingCreate] = []
        seen_ids = set()

        with self.get_http_client() as client:
            for page in range(1, self.max_pages + 1):
                url = self.jobs_url if page == 1 else f"{self.jobs_url}?page={page}"
                try:
                    response = client.get(url)
                    if response.status_code != 200:
                        logger.warning(
                            "Nem várt HTTP státusz (%s) a MŰISZ %s. oldalán.",
                            response.status_code,
                            page,
                        )
                        break
                except Exception as exc:
                    logger.error(
                        "Hiba a MŰISZ %s. oldal letöltésekor: %s", page, exc, exc_info=True
                    )
                    break

                soup = BeautifulSoup(response.text, "lxml")
                cards = soup.select("app-job-list-item")
                if not cards:
                    # Elértük az utolsó oldalt
                    break

                page_job_count = 0
                for card in cards:
                    try:
                        job = self._parse_card(card)
                        if job and job.external_id not in seen_ids:
                            seen_ids.add(job.external_id)
                            jobs.append(job)
                            page_job_count += 1
                    except Exception as e:
                        logger.warning("Hiba egy MŰISZ kártya feldolgozásakor: %s", e)

                if page_job_count == 0 and page > 1:
                    break

                if self.page_delay_seconds > 0:
                    time.sleep(self.page_delay_seconds)

        print(f"[SCRAPER OK] {self.source_name}: {len(jobs)} db hirdetés kinyerve.")
        return jobs

    def _parse_card(self, card: Any) -> JobPostingCreate | None:
        title_elem = card.select_one("h6")
        title = (title_elem.get_text(" ", strip=True) if title_elem else "Diákmunka")[:300]

        wage_elem = card.select_one(".WageBox")
        raw_wage = wage_elem.get_text(" ", strip=True) if wage_elem else None
        if raw_wage:
            raw_wage = raw_wage[:150]
        wage_min, wage_max = JobNormalizer.parse_wage(raw_wage)

        link_elem = card.select_one("a.link-button, a[href*='/hu/diakmunkaink/']")
        rel_url = link_elem["href"].strip() if link_elem and link_elem.get("href") else "/hu/diakmunkaink"
        job_url = urljoin(self.base_url, rel_url)

        # Azonosító kinyerése a link végéből (--XXXXX) vagy a kártya szövegéből (#XXXXX)
        id_match = re.search(r"--(\d+)$", rel_url)
        card_id_elem = card.find(string=re.compile(r"^#\d+"))

        if id_match:
            clean_id = id_match.group(1)
        elif card_id_elem:
            clean_id = card_id_elem.strip().lstrip("#")
        else:
            return None

        external_id = f"MUISZ-{clean_id}"

        # Tulajdonságok kinyerése a JobInfoRow sorokból
        props: dict[str, Any] = {}
        for row in card.select(".JobInfoRow"):
            k_elem = row.select_one(".JobPropertyTitle")
            v_elem = row.select_one(".JobPropertyValue")
            if k_elem and v_elem:
                key = k_elem.get_text(strip=True).rstrip(":")
                val = v_elem.get_text(strip=True)
                props[key] = val

        raw_city = props.get("Helyszín") or ""
        city = JobNormalizer.clean_city(raw_city)
        if city:
            city = city[:100]

        is_remote = "home office" in raw_city.lower() or JobNormalizer.detect_remote(
            title=title, city=raw_city
        )

        desc_parts = []
        for k, v in props.items():
            if k != "Azonosító":
                desc_parts.append(f"{k}: {v}")
        description = ". ".join(desc_parts)

        return JobPostingCreate(
            source_code=self.source_code,
            external_id=external_id,
            title=title,
            category=None,
            city=city,
            is_remote=is_remote,
            hourly_wage_min=wage_min,
            hourly_wage_max=wage_max,
            wage_raw_text=raw_wage,
            description=description,
            url=job_url,
            raw_metadata=props,
        )
