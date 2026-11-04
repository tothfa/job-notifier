from typing import List
from scrapers.base import BaseScraper
from schemas import JobPostingCreate
from services.normalizer import JobNormalizer


class MeloDiakScraper(BaseScraper):
    source_code = "melodiak"
    source_name = "Meló-Diák Iskolaszövetkezet"
    base_url = "https://www.melodiak.hu"
    # A felderített háttér JSON API végpont
    api_url = "https://www.melodiak.hu/job-advertisement"

    def fetch_jobs(self) -> List[JobPostingCreate]:
        print(f"[SCRAPER] {self.source_name} API lekérdezése: {self.api_url} ...")
        jobs: List[JobPostingCreate] = []

        headers = {
            **self.headers,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.melodiak.hu/munkak"
        }

        try:
            with self.get_http_client() as client:
                client.headers.update(headers)
                response = client.get(self.api_url)
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            print(f"[SCRAPER HIBA] {self.source_name} API hiba: {e}")
            return []

        # A JSON válasz listaként vagy egy objektum 'data' mezőjeként érkezhet
        items = data if isinstance(data, list) else data.get("data", data.get("items", []))

        for item in items:
            external_id = f"MD-{item.get('id', item.get('slug', 'unknown'))}"
            title = item.get("title", item.get("name", "Diákmunka"))
            slug = item.get("slug", "")
            url = f"{self.base_url}/munkak/{slug}" if slug else self.base_url

            raw_wage = item.get("salary_text", item.get("wage", ""))
            wage_min, wage_max = JobNormalizer.parse_wage(str(raw_wage))

            raw_city = item.get("city", item.get("location", ""))
            clean_city = JobNormalizer.clean_city(str(raw_city) if raw_city else None)
            is_remote = JobNormalizer.detect_remote(title=title, city=str(raw_city or ""), description=str(item.get("description", "")))

            jobs.append(
                JobPostingCreate(
                    source_code=self.source_code,
                    external_id=str(external_id),
                    title=str(title),
                    category=str(item.get("category", "Általános")),
                    city=clean_city,
                    is_remote=is_remote,
                    hourly_wage_min=wage_min,
                    hourly_wage_max=wage_max,
                    wage_raw_text=str(raw_wage) if raw_wage else None,
                    description=str(item.get("description", ""))[:300],
                    url=url
                )
            )

        print(f"[SCRAPER OK] {self.source_name}: {len(jobs)} db hirdetés kinyerve API-n keresztül.")
        return jobs