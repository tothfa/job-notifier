import re
from typing import List, Set
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag

from scrapers.base import BaseScraper
from schemas import JobPostingCreate
from services.normalizer import JobNormalizer


class SchonherzScraper(BaseScraper):
    source_code = "schonherz"
    source_name = "Schönherz Iskolaszövetkezet"
    base_url = "https://schonherz.hu"

    TARGET_CITIES = [
        ("debrecen", "Debrecen"),
        ("nyiregyhaza", "Nyíregyháza"),
        ("tavmunka", "Távmunka"),
        ("budapest", "Budapest"),
    ]

    def _get_category_urls(self, client, city_slug: str) -> Set[str]:
        city_url = f"{self.base_url}/diakmunkak/{city_slug}"
        category_urls: Set[str] = set()

        try:
            resp = client.get(city_url)
            if resp.status_code != 200:
                return category_urls
        except Exception as e:
            print(f"[SCHÖNHERZ] Város oldal nem elérhető ({city_url}): {e}")
            return category_urls

        soup = BeautifulSoup(resp.text, "lxml")
        pattern = re.compile(rf"^/?diakmunkak/{city_slug}/([a-z0-9-]+)$", re.IGNORECASE)

        for a in soup.find_all("a", href=True):
            href = a["href"].strip().split("?")[0].rstrip("/")
            rel_href = href.replace(self.base_url, "")
            if pattern.match(rel_href):
                category_urls.add(urljoin(self.base_url, rel_href))

        return category_urls

    def _parse_category_page(
        self, html_text: str, city_name: str, category_url: str, seen_ids: Set[str]
    ) -> List[JobPostingCreate]:
        soup = BeautifulSoup(html_text, "lxml")
        jobs: List[JobPostingCreate] = []

        category_slug = category_url.rstrip("/").split("/")[-1]
        category_name = category_slug.replace("-", " ").title()

        # Pontos minta: /diakmunka/{city}/{category}/{id}-{slug}
        job_pattern = re.compile(r"^/?diakmunka/[^/]+/[^/]+/(\d+)-([a-z0-9-]+)$", re.IGNORECASE)

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            rel_href = href.replace(self.base_url, "").split("?")[0].rstrip("/")
            match = job_pattern.match(rel_href)

            if not match:
                continue

            job_id = match.group(1)
            slug = match.group(2)
            external_id = f"SCH-{job_id}"

            if external_id in seen_ids:
                continue

            full_url = urljoin(self.base_url, rel_href)
            card: Tag = a.find_parent(["article", "div", "li"]) or a
            card_text = card.get_text(" | ", strip=True)

            title_el = card.find(["h1", "h2", "h3", "h4", "strong"])
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
            if not title or len(title) < 4 or title.lower() in ("részletek", "tovább", "jelentkezés", "megnézem"):
                title = slug.replace("-", " ").capitalize()

            wage_match = re.search(
                r"((?:br\.?|bruttó)?\s*\d[\d\s\.,-]*\s*ft(?:\s*/\s*(?:óra|ó))?)",
                card_text,
                re.IGNORECASE,
            )
            raw_wage = wage_match.group(1).strip() if wage_match else None
            wage_min, wage_max = JobNormalizer.parse_wage(raw_wage)

            is_remote = (
                city_name.lower() == "távmunka"
                or JobNormalizer.detect_remote(title=title, city=city_name, description=card_text)
            )
            final_city = "Távmunka" if city_name.lower() == "távmunka" else JobNormalizer.clean_city(city_name)

            seen_ids.add(external_id)
            jobs.append(
                JobPostingCreate(
                    source_code=self.source_code,
                    external_id=external_id,
                    title=title,
                    category=category_name,
                    city=final_city,
                    is_remote=is_remote,
                    hourly_wage_min=wage_min,
                    hourly_wage_max=wage_max,
                    wage_raw_text=raw_wage,
                    description=card_text[:300],
                    url=full_url,
                )
            )

        return jobs

    def fetch_jobs(self) -> List[JobPostingCreate]:
        print(f"[SCRAPER] {self.source_name} bejárása indul...")
        all_jobs: List[JobPostingCreate] = []
        seen_ids: Set[str] = set()

        with self.get_http_client() as client:
            for city_slug, city_name in self.TARGET_CITIES:
                category_urls = self._get_category_urls(client, city_slug)
                for cat_url in category_urls:
                    try:
                        resp = client.get(cat_url)
                        if resp.status_code == 200:
                            jobs = self._parse_category_page(resp.text, city_name, cat_url, seen_ids)
                            all_jobs.extend(jobs)
                    except Exception as e:
                        print(f"  -> [SCHÖNHERZ HIBA] ({cat_url}): {e}")

        print(f"[SCRAPER OK] {self.source_name}: {len(all_jobs)} db hirdetés kinyerve.")
        return all_jobs