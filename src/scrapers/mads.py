import json
import re
import time
from typing import Any, List
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper
from schemas import JobPostingCreate
from services.normalizer import JobNormalizer


class MadsScraper(BaseScraper):
    source_code = "mads"
    source_name = "MADS Iskolaszövetkezet"
    base_url = "https://mads.hu"
    jobs_url = "https://mads.hu/munkak/diak"
    max_pages = 100
    request_delay_seconds = 0.2

    @staticmethod
    def _job_posting_data(soup: BeautifulSoup) -> tuple[dict[str, Any] | None, str | None]:
        posting = None
        breadcrumb_location = None

        def visit(value: Any) -> None:
            nonlocal posting, breadcrumb_location
            if isinstance(value, list):
                for entry in value:
                    visit(entry)
                return
            if not isinstance(value, dict):
                return
            item_type = value.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if "JobPosting" in types:
                posting = value
            elif "BreadcrumbList" in types:
                for entry in value.get("itemListElement", []):
                    if isinstance(entry, dict) and entry.get("position") == 3:
                        breadcrumb_location = entry.get("name")
            graph = value.get("@graph")
            if graph is not None:
                visit(graph)

        for script in soup.select('script[type="application/ld+json"]'):
            try:
                visit(json.loads(script.string or script.get_text()))
            except (json.JSONDecodeError, TypeError):
                continue
        return posting, breadcrumb_location if isinstance(breadcrumb_location, str) else None

    @staticmethod
    def _posting_location(posting: dict[str, Any] | None) -> str | None:
        if posting is None:
            return None
        locations = posting.get("jobLocation")
        if isinstance(locations, dict):
            locations = [locations]
        if not isinstance(locations, list):
            return None
        for location in locations:
            if not isinstance(location, dict):
                continue
            address = location.get("address")
            if isinstance(address, dict):
                locality = address.get("addressLocality")
                if isinstance(locality, str) and locality:
                    return locality
        return None

    @staticmethod
    def _posting_wage(description: str) -> str | None:
        patterns = (
            r"((?:bruttó\s+)?(?:órabér|óránkénti\s+bér)[^0-9]{0,40}"
            r"\d[\d\s\xa0.,]*\s*ft(?:\s*/\s*(?:óra|ó))?)",
            r"(\d[\d\s\xa0.,]*\s*ft\s*/\s*(?:óra|ó))",
        )
        for pattern in patterns:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _get_next_page_url(self, soup: BeautifulSoup, current_url: str) -> str | None:
        next_link = soup.find(
            "a",
            rel=lambda value: value and "next" in (
                value if isinstance(value, list) else value.split()
            ),
        )
        if next_link is None:
            next_link = next(
                (
                    link for link in soup.find_all("a", href=True)
                    if "következő oldal" in link.get_text(" ", strip=True).lower()
                ),
                None,
            )
        if next_link is None:
            return None

        next_url = urljoin(current_url, next_link["href"])
        parts = urlsplit(next_url)
        if parts.hostname != "mads.hu" or parts.port not in (None, 443):
            raise ValueError("A MADS oldal nem megbízható lapozó URL-t adott vissza.")
        return next_url

    def _enrich_from_detail(
        self, client, url: str
    ) -> tuple[str | None, str | None, str | None, bool]:
        time.sleep(self.request_delay_seconds)
        response = client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        posting, breadcrumb_location = self._job_posting_data(soup)

        posting_description = ""
        if posting and isinstance(posting.get("description"), str):
            posting_description = BeautifulSoup(
                posting["description"], "lxml"
            ).get_text(" ", strip=True)
        page_text = soup.get_text(" ", strip=True)
        searchable_text = posting_description or page_text
        wage_text = self._posting_wage(searchable_text)

        city = self._posting_location(posting)
        city = city or breadcrumb_location
        if not city:
            city_match = re.search(
                r"\b((?:Budapest|Debrecen|Nyíregyháza|Szeged|Pécs|Győr|"
                r"Miskolc|Budaörs|Törökbálint|Székesfehérvár)"
                r"(?:,\s*\d+\.\s*kerület)?)\b",
                searchable_text,
                re.IGNORECASE,
            )
            city = city_match.group(1) if city_match else None

        job_location_type = posting.get("jobLocationType") if posting else None
        is_remote = isinstance(job_location_type, str) and job_location_type.upper() == "TELECOMMUTE"
        return wage_text, city, posting_description or None, is_remote

    def fetch_jobs(self) -> List[JobPostingCreate]:
        print(f"[SCRAPER] {self.source_name} letöltése: {self.jobs_url} ...")
        jobs: List[JobPostingCreate] = []
        seen_ids: set[str] = set()
        visited_pages: set[str] = set()
        current_url: str | None = self.jobs_url
        job_link_pattern = re.compile(
            r"^/munkak/diak/([^/]+)/([a-z0-9-]+-(\d+))/?$",
            re.IGNORECASE,
        )

        with self.get_http_client() as client:
            for page_number in range(1, self.max_pages + 1):
                if current_url is None:
                    break
                if current_url in visited_pages:
                    raise ValueError("A MADS lapozása ismétlődő URL-re jutott.")
                visited_pages.add(current_url)
                if page_number > 1:
                    time.sleep(self.request_delay_seconds)

                response = client.get(current_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "lxml")

                for link in soup.find_all("a", href=True):
                    href = link["href"].strip()
                    match = job_link_pattern.match(href)
                    if not match:
                        continue

                    category = match.group(1).replace("-", " ").title()
                    slug = match.group(2)
                    external_id = f"MADS-{match.group(3)}"
                    if external_id in seen_ids:
                        continue

                    full_url = urljoin(self.base_url, href)
                    card = link.find_parent(["article", "div", "li"]) or link
                    card_text = card.get_text(" ", strip=True)
                    title_element = card.find(["h1", "h2", "h3", "h4", "strong"])
                    title = title_element.get_text(" ", strip=True) if title_element else ""
                    if not title or len(title) < 4 or "jelentkez" in title.lower():
                        title = re.sub(r"-\d+$", "", slug).replace("-", " ").capitalize()

                    wage_match = re.search(
                        r"((?:br\.?|bruttó)?\s*\d[\d\s\xa0.,-]*\s*ft"
                        r"(?:\s*/\s*(?:óra|ó))?)",
                        card_text,
                        re.IGNORECASE,
                    )
                    raw_wage = wage_match.group(1).strip() if wage_match else None
                    wage_min, wage_max = JobNormalizer.parse_wage(raw_wage)

                    city_match = re.search(
                        r"\b(Debrecen|Nyíregyháza|Budapest|Szeged|Pécs|Győr|"
                        r"Miskolc|Tatabánya|Maglód|Soroksár|Törökbálint|"
                        r"Budaörs|Székesfehérvár|Kecskemét|Sopron|Gyöngyös)\b",
                        f"{card_text} {slug.replace('-', ' ')}",
                        re.IGNORECASE,
                    )
                    city = JobNormalizer.clean_city(city_match.group(1)) if city_match else None
                    description = card_text[:300]
                    is_remote = JobNormalizer.detect_remote(
                        title=title,
                        city=city or "",
                        description=card_text,
                    )

                    if wage_min is None or city is None:
                        try:
                            detail_wage, detail_city, detail_description, detail_remote = (
                                self._enrich_from_detail(client, full_url)
                            )
                            if wage_min is None and detail_wage:
                                raw_wage = detail_wage
                                wage_min, wage_max = JobNormalizer.parse_wage(detail_wage)
                            if city is None and detail_city:
                                city = JobNormalizer.clean_city(detail_city)
                            if detail_description:
                                description = detail_description[:300]
                            is_remote = is_remote or detail_remote or JobNormalizer.detect_remote(
                                title=title,
                                city=city or "",
                                description=description,
                            )
                        except Exception as exc:
                            print(f"[MADS HIBA] Részletoldal nem olvasható ({full_url}): {exc}")

                    seen_ids.add(external_id)
                    jobs.append(
                        JobPostingCreate(
                            source_code=self.source_code,
                            external_id=external_id,
                            title=title,
                            category=category,
                            city=city,
                            is_remote=is_remote,
                            hourly_wage_min=wage_min,
                            hourly_wage_max=wage_max,
                            wage_raw_text=raw_wage,
                            description=description or None,
                            url=full_url,
                        )
                    )

                current_url = self._get_next_page_url(soup, current_url)
            else:
                if current_url is not None:
                    raise ValueError(
                        f"A MADS lapozása túllépte a {self.max_pages} oldalas korlátot."
                    )

        print(f"[SCRAPER OK] {self.source_name}: {len(jobs)} db hirdetés kinyerve.")
        return jobs
