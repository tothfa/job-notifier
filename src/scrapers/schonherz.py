import re
from typing import List, Set
from urllib.parse import urljoin, urlsplit

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
        ("budapest", "Budapest"),
    ]

    def _get_category_urls(self, client, city_slug: str) -> Set[str]:
        city_url = f"{self.base_url}/diakmunkak/{city_slug}"
        response = client.get(city_url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")
        pattern = re.compile(rf"^/?diakmunkak/{re.escape(city_slug)}/([a-z0-9-]+)$", re.IGNORECASE)
        category_urls: Set[str] = set()

        for link in soup.find_all("a", href=True):
            href = link["href"].strip().split("?")[0].rstrip("/")
            parts = urlsplit(href)
            rel_href = parts.path if parts.scheme else href
            if pattern.match(rel_href):
                category_urls.add(urljoin(self.base_url, rel_href))

        return category_urls

    def _parse_category_page(
        self, html_text: str, city_name: str, category_url: str, seen_ids: Set[str]
    ) -> List[JobPostingCreate]:
        soup = BeautifulSoup(html_text, "lxml")
        category_slug = category_url.rstrip("/").split("/")[-1]
        category_name = category_slug.replace("-", " ").title()
        job_pattern = re.compile(
            r"^/?diakmunka/[^/]+/[^/]+/(\d+)-([a-z0-9-]+)$",
            re.IGNORECASE,
        )
        links_by_id: dict[str, list[Tag]] = {}

        for link in soup.find_all("a", href=True):
            href = link["href"].strip()
            parts = urlsplit(href)
            rel_href = (parts.path if parts.scheme else href).split("?")[0].rstrip("/")
            match = job_pattern.match(rel_href)
            if match:
                links_by_id.setdefault(match.group(1), []).append(link)

        jobs: List[JobPostingCreate] = []
        for job_id, links in links_by_id.items():
            external_id = f"SCH-{job_id}"
            if external_id in seen_ids:
                continue

            first_link = links[0]
            href = first_link["href"].strip()
            parts = urlsplit(href)
            rel_href = (parts.path if parts.scheme else href).split("?")[0].rstrip("/")
            match = job_pattern.match(rel_href)
            if match is None:
                continue

            slug = match.group(2)
            full_url = urljoin(self.base_url, rel_href)
            card = first_link.find_parent("div", class_="col-md-8")
            if card is None:
                card = first_link.find_parent(["article", "div", "li"]) or first_link

            title_element = card.find(["h1", "h2", "h3", "h4"])
            title = title_element.get_text(" ", strip=True) if title_element else ""
            if not title or len(title) < 4:
                title = slug.replace("-", " ").capitalize()

            description_element = card.select_one(".text span")
            description = (
                description_element.get_text(" ", strip=True)
                if description_element
                else card.get_text(" ", strip=True)
            )
            card_text = card.get_text(" ", strip=True)
            wage_text = " ".join(link.get_text(" ", strip=True) for link in links)
            wage_match = re.search(
                r"((?:br\.?|bruttó|gross)?\s*\d[\d\s\.,-]*\s*ft"
                r"(?:\s*/\s*(?:óra|ó|hour))?)",
                wage_text or card_text,
                re.IGNORECASE,
            )
            raw_wage = wage_match.group(1).strip() if wage_match else None
            wage_min, wage_max = JobNormalizer.parse_wage(raw_wage)
            if description_element is None:
                description = description.replace(title, "", 1).strip(" |")
            if raw_wage:
                description = description.replace(raw_wage, "", 1).strip(" |")

            is_remote = JobNormalizer.detect_remote(
                title=title,
                city=city_name,
                description=description,
            )
            seen_ids.add(external_id)
            jobs.append(
                JobPostingCreate(
                    source_code=self.source_code,
                    external_id=external_id,
                    title=title,
                    category=category_name,
                    city=JobNormalizer.clean_city(city_name),
                    is_remote=is_remote,
                    hourly_wage_min=wage_min,
                    hourly_wage_max=wage_max,
                    wage_raw_text=raw_wage,
                    description=description[:300] or None,
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
                try:
                    category_urls = self._get_category_urls(client, city_slug)
                except Exception as exc:
                    print(f"[SCHÖNHERZ HIBA] Város oldal nem érhető el ({city_slug}): {exc}")
                    continue

                for category_url in sorted(category_urls):
                    try:
                        response = client.get(category_url)
                        response.raise_for_status()
                        all_jobs.extend(
                            self._parse_category_page(
                                response.text,
                                city_name,
                                category_url,
                                seen_ids,
                            )
                        )
                    except Exception as exc:
                        print(f"[SCHÖNHERZ HIBA] ({category_url}): {exc}")

        print(f"[SCRAPER OK] {self.source_name}: {len(all_jobs)} db hirdetés kinyerve.")
        return all_jobs
