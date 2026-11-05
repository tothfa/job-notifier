import re
import time
from typing import Any, List
from urllib.parse import urljoin, urlsplit, urlunsplit

from scrapers.base import BaseScraper
from schemas import JobPostingCreate
from services.normalizer import JobNormalizer


class MeloDiakScraper(BaseScraper):
    source_code = "melodiak"
    source_name = "Meló-Diák Iskolaszövetkezet"
    base_url = "https://www.melodiak.hu"
    api_url = "https://web-api.melodiak.hu/v1/job-advertisement"
    max_pages = 100
    page_delay_seconds = 0.2

    def _normalize_next_url(self, next_url: str, current_url: str) -> str:
        candidate = urljoin(current_url, next_url)
        parts = urlsplit(candidate)
        if parts.hostname != "web-api.melodiak.hu" or parts.port not in (None, 443):
            raise ValueError("A Meló-Diák API nem megbízható lapozó URL-t adott vissza.")
        return urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))

    @staticmethod
    def _get_page_data(data: Any) -> tuple[list[dict[str, Any]], str | None]:
        if not isinstance(data, dict) or data.get("success") is not True:
            raise ValueError("A Meló-Diák API válasza érvénytelen.")

        payload = data.get("data")
        if not isinstance(payload, dict):
            raise ValueError("A Meló-Diák API válaszából hiányzik a data objektum.")
        items = payload.get("resource")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("A Meló-Diák API data.resource mezője nem hirdetéslista.")

        pagination = payload.get("pagination")
        links = pagination.get("links") if isinstance(pagination, dict) else None
        if not isinstance(links, dict) or "next" not in links:
            raise ValueError("A Meló-Diák API válaszából hiányzik a lapozási információ.")
        next_url = links["next"]
        if next_url is not None and not isinstance(next_url, str):
            raise ValueError("A Meló-Diák API lapozó hivatkozása érvénytelen.")
        return items, next_url

    @staticmethod
    def _to_job(item: dict[str, Any]) -> JobPostingCreate:
        slug = item.get("slug")
        title = item.get("title")
        if not isinstance(slug, str) or not slug or not isinstance(title, str) or not title:
            raise ValueError("A Meló-Diák API hirdetése nem tartalmaz slugot vagy címet.")

        raw_wage = item.get("payment")
        raw_city = item.get("city_name")
        category = item.get("category")
        category_name = category.get("name") if isinstance(category, dict) else None
        description = item.get("work_text") or ""
        if not isinstance(description, str):
            description = str(description)

        searchable_text = " ".join(
            value for value in (description, str(item.get("labels") or "")) if value
        )
        wage_text = raw_wage if isinstance(raw_wage, str) else None
        wage_min, wage_max = JobNormalizer.parse_wage(wage_text)
        numeric_id = re.search(r"-(\d+)$", slug)
        return JobPostingCreate(
            source_code="melodiak",
            external_id=f"MD-{numeric_id.group(1) if numeric_id else slug}",
            title=title.strip(),
            category=category_name if isinstance(category_name, str) else "Általános",
            city=JobNormalizer.clean_city(raw_city if isinstance(raw_city, str) else None),
            is_remote=JobNormalizer.detect_remote(
                title=title,
                city=raw_city if isinstance(raw_city, str) else "",
                description=searchable_text,
            ),
            hourly_wage_min=wage_min,
            hourly_wage_max=wage_max,
            wage_raw_text=wage_text,
            description=description[:300] or None,
            url=f"https://www.melodiak.hu/diakmunkak/{slug}",
        )

    def fetch_jobs(self) -> List[JobPostingCreate]:
        print(f"[SCRAPER] {self.source_name} API lekérdezése: {self.api_url} ...")
        jobs: List[JobPostingCreate] = []
        seen_urls: set[str] = set()
        current_url: str | None = self.api_url

        with self.get_http_client() as client:
            client.headers.update({
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.melodiak.hu/diakmunkak/",
            })

            for page_number in range(1, self.max_pages + 1):
                if current_url is None:
                    break
                if current_url in seen_urls:
                    raise ValueError("A Meló-Diák API lapozása ismétlődő URL-re jutott.")
                seen_urls.add(current_url)
                if page_number > 1:
                    time.sleep(self.page_delay_seconds)

                response = client.get(current_url)
                response.raise_for_status()
                items, next_url = self._get_page_data(response.json())
                jobs.extend(self._to_job(item) for item in items)
                current_url = (
                    self._normalize_next_url(next_url, current_url) if next_url else None
                )
            else:
                if current_url is not None:
                    raise ValueError(
                        f"A Meló-Diák API lapozása túllépte a {self.max_pages} oldalas korlátot."
                    )

        print(f"[SCRAPER OK] {self.source_name}: {len(jobs)} db hirdetés kinyerve API-n keresztül.")
        return jobs
