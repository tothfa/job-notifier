import re
from typing import Optional, Tuple


class JobNormalizer:
    # Gyakori szavak, amik távmunkára utalnak a címben, városban vagy leírásban
    REMOTE_KEYWORDS = [
        "home office", "home-office", "távmunka", "remote", "otthonról",
        "otthoni munka", "online végezhető",
    ]

    @staticmethod
    def parse_wage(raw_wage: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
        """
        Kinyeri a minimum és maximum órabért a nyers szövegből.
        Példák:
          "Br. 2 200 Ft/óra" -> (2200, 2200)
          "2100 - 2650 Ft/ó" -> (2100, 2650)
          "1.950 Ft"         -> (1950, 1950)
        """
        if not raw_wage:
            return None, None

        # Eltávolítjuk az ezres elválasztó pontokat és szóközöket a számok közül (pl. "2 200" -> "2200", "2.200" -> "2200")
        cleaned = re.sub(r"(?<=\d)[\s\xa0\.](?=\d{3}\b)", "", raw_wage)

        # Megkeressük az összes 3 vagy 4 jegyű számot (reális magyar diákmunka órabérek: 1000 - 9999 Ft)
        matches = re.findall(r"\b([1-9]\d{3})\b", cleaned)
        if not matches:
            return None, None

        wages = [int(m) for m in matches]
        return min(wages), max(wages)

    @staticmethod
    def clean_city(raw_city: Optional[str]) -> Optional[str]:
        """
        Tisztítja a város nevét (levágja a felesleges kerületet, irányítószámot, szóközöket).
        Példa: "4028 Debrecen, Kassai út" -> "Debrecen"
        """
        if not raw_city:
            return None

        # Irányítószám eltávolítása az elejéről (pl. "4028 " vagy "4400 ")
        city = re.sub(r"^\d{4}\s*", "", raw_city.strip())

        # Ha vessző vagy kötőjel van benne (pl. "Debrecen, belváros"), csak az első részt tartjuk meg
        city = re.split(r"[,/|-]", city)[0].strip()

        # Budapest kerületek egységesítése (pl. "Budapest XI. kerület" -> "Budapest")
        if city.lower().startswith("budapest"):
            return "Budapest"

        return city.title() if city else None

    @classmethod
    def detect_remote(cls, title: str = "", city: str = "", description: str = "") -> bool:
        """Eldönti, hogy a hirdetés kínál-e távmunka / home office lehetőséget."""
        combined_text = f"{title} {city} {description}".lower()
        return any(keyword in combined_text for keyword in cls.REMOTE_KEYWORDS)