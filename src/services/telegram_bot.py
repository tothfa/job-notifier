import logging
import threading
from typing import Any

from database import (
    disable_chat_filter,
    get_chat_filter,
    get_update_offset,
    save_chat_filter,
    save_update_offset,
)
from services.notifier import TelegramNotifier


logger = logging.getLogger("JobNotifier.TelegramBot")


class TelegramBot:
    def __init__(self, notifier: TelegramNotifier | None = None):
        self.notifier = notifier or TelegramNotifier()
        self._wizard_state: dict[str, dict[str, Any]] = {}

    def poll_updates(self, stop_event: threading.Event) -> None:
        offset: int | None = None
        offset_loaded = False
        retry_delay = 1
        if not self.notifier.delete_webhook():
            logger.warning("Nem sikerült kikapcsolni a Telegram webhookot; a polling ettől még elindul.")
        if not self.notifier.set_commands():
            logger.warning("Nem sikerült frissíteni a Telegram parancsmenüt.")
        logger.info("Telegram parancsfigyelés elindult.")

        while not stop_event.is_set():
            try:
                if not offset_loaded:
                    offset = get_update_offset()
                    offset_loaded = True
                updates = self.notifier.get_updates(offset=offset, timeout=25)
                retry_delay = 1
                for update in updates:
                    update_id = update.get("update_id")
                    try:
                        self.handle_update(update)
                    except Exception as exc:
                        logger.error(
                            "Telegram update feldolgozása sikertelen (%s).",
                            type(exc).__name__,
                        )
                        message = update.get("message") or {}
                        chat = message.get("chat") or {}
                        if chat.get("id") is not None:
                            self.notifier.send_text(
                                str(chat["id"]),
                                "Hiba történt a kérés feldolgozásakor. Próbáld újra később, vagy használd a /help parancsot.",
                            )
                    if isinstance(update_id, int):
                        next_offset = update_id + 1
                        save_update_offset(next_offset)
                        offset = next_offset
            except Exception as exc:
                logger.error(
                    "Telegram polling hiba (%s); újrapróbálkozás %s másodperc múlva.",
                    type(exc).__name__,
                    retry_delay,
                )
                stop_event.wait(retry_delay)
                retry_delay = min(retry_delay * 2, 30)

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("text"), str):
            return

        chat = message.get("chat") or {}
        if chat.get("id") is None:
            return
        chat_id = str(chat["id"])
        text = message["text"].strip()
        if not text:
            return

        if text.startswith("/"):
            command, _, _arguments = text.partition(" ")
            command = command.split("@", 1)[0].lower()
            if command in {"/start", "/help"}:
                self._send_help(chat_id)
            elif command == "/filter":
                self._wizard_state[chat_id] = {"step": "cities"}
                self._reply(
                    chat_id,
                    "Melyik városok érdekelnek? Írd be vesszővel elválasztva (például: Debrecen, Budapest), vagy `-` minden városhoz.",
                )
            elif command == "/myfilter":
                self._show_filter(chat_id)
            elif command == "/stopfilter":
                self._wizard_state.pop(chat_id, None)
                if disable_chat_filter(chat_id):
                    self._reply(chat_id, "Az értesítéseket kikapcsoltam. Új szűrőt a /filter paranccsal állíthatsz be.")
                else:
                    self._reply(chat_id, "Nincs aktív szűrőd. Újat a /filter paranccsal állíthatsz be.")
            elif command == "/cancel":
                self._wizard_state.pop(chat_id, None)
                self._reply(chat_id, "A szűrő beállítását megszakítottam.")
            else:
                self._reply(chat_id, "Ismeretlen parancs. A lehetőségekért használd a /help parancsot.")
            return

        if chat_id in self._wizard_state:
            self._consume_filter_answer(chat_id, text)
        else:
            self._reply(chat_id, "Szűrő beállításához küldd a /filter parancsot. A súgó: /help.")

    def _consume_filter_answer(self, chat_id: str, answer: str) -> None:
        state = self._wizard_state[chat_id]
        step = state["step"]
        normalized = answer.strip().lower()

        if step == "cities":
            cities = [] if normalized in {"-", "mind", "minden"} else [
                city.strip() for city in answer.split(",") if city.strip()
            ]
            if len(cities) > 20 or any(len(city) > 100 for city in cities):
                self._reply(chat_id, "Legfeljebb 20, egyenként maximum 100 karakteres várost adj meg.")
                return
            state["cities"] = cities or None
            state["step"] = "remote"
            self._reply(chat_id, "Jöhet távmunka is? Válaszolj: igen vagy nem. `-` esetén igen.")
            return

        if step == "remote":
            if normalized in {"igen", "i", "yes", "y", "1", "-"}:
                state["include_remote"] = True
            elif normalized in {"nem", "n", "no", "0"}:
                state["include_remote"] = False
            else:
                self._reply(chat_id, "Kérlek, `igen` vagy `nem` választ adj. `-` esetén igen.")
                return
            state["step"] = "wage"
            self._reply(chat_id, "Mi legyen a minimum bruttó órabér forintban? Adj meg egy egész számot, vagy `-`, ha nincs minimum.")
            return

        if step == "wage":
            if normalized in {"-", "nincs", "0"}:
                state["min_hourly_wage"] = 0
            else:
                try:
                    wage = int(normalized.replace(" ", "").replace("\xa0", ""))
                except ValueError:
                    self._reply(chat_id, "Az órabér egész szám legyen, például `2200`, vagy `-` minimum nélkül.")
                    return
                if not 0 <= wage <= 1_000_000:
                    self._reply(chat_id, "Az órabér 0 és 1 000 000 közötti egész szám legyen.")
                    return
                state["min_hourly_wage"] = wage
            state["step"] = "keywords"
            self._reply(chat_id, "Milyen kulcsszavak szerepeljenek a hirdetésben? Vesszővel válaszd el őket, vagy `-`, ha mindegy.")
            return

        if step == "keywords":
            keywords = [] if normalized in {"-", "mind", "bármi"} else [
                keyword.strip() for keyword in answer.split(",") if keyword.strip()
            ]
            if len(keywords) > 20 or any(len(keyword) > 100 for keyword in keywords):
                self._reply(chat_id, "Legfeljebb 20, egyenként maximum 100 karakteres kulcsszót adj meg.")
                return

            save_chat_filter(
                chat_id=chat_id,
                cities=state["cities"],
                include_remote=state["include_remote"],
                min_hourly_wage=state["min_hourly_wage"],
                keywords_include=keywords or None,
            )
            self._wizard_state.pop(chat_id, None)
            city_summary = ", ".join(state["cities"]) if state["cities"] else "minden város"
            wage_summary = f"{state['min_hourly_wage']} Ft/óra" if state["min_hourly_wage"] else "nincs minimum"
            keyword_summary = ", ".join(keywords) if keywords else "nincs kulcsszó-korlátozás"
            remote_summary = "igen" if state["include_remote"] else "nem"
            self._reply(
                chat_id,
                "Elmentettem a szűrődet:\n"
                f"• Város: {city_summary}\n"
                f"• Távmunka: {remote_summary}\n"
                f"• Minimum bér: {wage_summary}\n"
                f"• Kulcsszavak: {keyword_summary}\n\n"
                "Módosításhoz: /filter | Megtekintéshez: /myfilter | Kikapcsoláshoz: /stopfilter",
            )

    def _show_filter(self, chat_id: str) -> None:
        filter_data = get_chat_filter(chat_id)
        if filter_data is None:
            self._reply(chat_id, "Még nincs aktív szűrőd. Beállításhoz használd a /filter parancsot.")
            return
        cities = filter_data["cities"] or []
        keywords = filter_data["keywords_include"] or []
        self._reply(
            chat_id,
            "Aktív szűrőd:\n"
            f"• Város: {', '.join(cities) if cities else 'minden város'}\n"
            f"• Távmunka: {'igen' if filter_data['include_remote'] else 'nem'}\n"
            f"• Minimum bér: {filter_data['min_hourly_wage'] or 'nincs'} Ft/óra\n"
            f"• Kulcsszavak: {', '.join(keywords) if keywords else 'nincs korlátozás'}\n\n"
            "Módosításhoz: /filter | Kikapcsoláshoz: /stopfilter",
        )

    def _send_help(self, chat_id: str) -> None:
        self._reply(
            chat_id,
            "Diákmunka-értesítő súgó\n\n"
            "/filter – szűrő beállítása vagy módosítása\n"
            "/myfilter – aktív szűrő megtekintése\n"
            "/stopfilter – értesítések kikapcsolása\n"
            "/cancel – folyamatban lévő beállítás megszakítása\n"
            "/help – súgó\n\n"
            "A /filter lépéseinél a `-` alapértelmezést jelent: minden város, távmunka engedélyezve, "
            "minimum bér és kulcsszó-korlátozás nélkül. Az értesítéseket a rendszer 10 percenként ellenőrzi.",
        )

    def _reply(self, chat_id: str, message: str) -> None:
        if not self.notifier.send_text(chat_id, message):
            logger.error("Nem sikerült Telegram választ küldeni (chat ID maszkolva).")
