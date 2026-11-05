import html
import logging
from collections.abc import Callable

import httpx
from config import settings
from schemas import JobPostingCreate

logger = logging.getLogger("JobNotifier.Telegram")


class TelegramNotifier:
    def __init__(self, bot_token: str = settings.telegram_bot_token):
        self.bot_token = bot_token
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

    def _format_message(self, job: JobPostingCreate) -> str:
        """HTML-formázott, jól áttekinthető üzenetet épít az álláshirdetésből."""
        title = html.escape(job.title)
        source = html.escape(job.source_code.upper())
        city = html.escape(job.city or "Nincs megadva")
        category = html.escape(job.category or "Általános")

        if job.is_remote:
            city += " (🏠 Távmunka / Home Office)"

        if job.wage_raw_text:
            wage = html.escape(job.wage_raw_text)
        elif job.hourly_wage_min:
            wage = f"Br. {job.hourly_wage_min} Ft/óra"
        else:
            wage = "Nincs megadva"

        lines = [
            f"🔔 <b>Új diákmunka! [{source}]</b>",
            f"",
            f"💼 <b>{title}</b>",
            f"📍 <b>Helyszín:</b> {city}",
            f"💰 <b>Órabér:</b> {wage}",
            f"🏷 <b>Kategória:</b> {category}",
        ]

        if job.description:
            short_desc = html.escape(job.description[:200])
            if len(job.description) > 200:
                short_desc += "..."
            lines.append(f"\n📝 <i>{short_desc}</i>")

        return "\n".join(lines)

    def send_text(self, chat_id: str, message: str) -> bool:
        return self._send_message(chat_id, html.escape(message))

    def send_job_alert(self, job: JobPostingCreate, chat_id: str) -> bool:
        """Elküldi az álláshirdetést Telegramra kattintható gombbal."""
        payload = {
            "chat_id": chat_id,
            "text": self._format_message(job),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": "🔗 Hirdetés megnyitása", "url": job.url}]
                ]
            }
        }
        return self._post("sendMessage", payload)

    def send_job_digest(
        self,
        chat_id: str,
        items: list[tuple[int, JobPostingCreate]],
        on_chunk_result: Callable[[list[int], bool], None] | None = None,
    ) -> list[tuple[list[int], bool]]:
        header = "📬 <b>Napi diákmunkák</b>\n\n"
        chunks: list[tuple[list[int], str]] = []
        current_ids: list[int] = []
        current_message = header

        for job_id, job in items:
            block = self._format_digest_job(job)
            if current_ids and self._telegram_length(current_message + block) > 3500:
                chunks.append((current_ids, current_message.rstrip()))
                current_ids = []
                current_message = header
            current_ids.append(job_id)
            current_message += block

        if current_ids:
            chunks.append((current_ids, current_message.rstrip()))

        results = []
        for job_ids, message in chunks:
            success = self._post(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            results.append((job_ids, success))
            if on_chunk_result is not None:
                on_chunk_result(job_ids, success)
        return results

    def _format_digest_job(self, job: JobPostingCreate) -> str:
        title = html.escape(job.title[:150], quote=True)
        url = html.escape(job.url, quote=True)
        title_markup = (
            f"<a href=\"{url}\"><b>{title}</b></a>"
            if len(url) <= 500
            else f"<b>{title}</b>"
        )
        source = html.escape(job.source_code[:50].upper())
        city = html.escape((job.city or "Nincs megadva")[:60])
        category = html.escape((job.category or "Általános")[:50])
        wage = (
            html.escape(job.wage_raw_text[:80])
            if job.wage_raw_text
            else f"Br. {job.hourly_wage_min} Ft/óra"
            if job.hourly_wage_min
            else "Nincs megadva"
        )
        if job.is_remote:
            city += " (🏠 távmunka)"
        description = ""
        if job.description:
            description = f"\n  <i>{html.escape(job.description[:100])}</i>"

        return (
            f"• {title_markup} [{source}]\n"
            f"  📍 {city} · 💰 {wage} · 🏷 {category}"
            f"{description}\n\n"
        )

    @staticmethod
    def _telegram_length(message: str) -> int:
        return len(message.encode("utf-16-le")) // 2

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        payload: dict[str, object] = {
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        response_data = self._post_for_data(
            "getUpdates",
            payload,
            request_timeout=timeout + 10,
        )
        result = response_data.get("result")
        if not isinstance(result, list):
            raise RuntimeError("Telegram API returned an invalid update list.")
        return [update for update in result if isinstance(update, dict)]

    def set_commands(self) -> bool:
        commands = [
            {"command": "start", "description": "A bot elindítása"},
            {"command": "filter", "description": "Értesítési szűrő beállítása"},
            {"command": "myfilter", "description": "Aktív szűrő megtekintése"},
            {"command": "stopfilter", "description": "Értesítések kikapcsolása"},
            {"command": "notify", "description": "Értesítési mód beállítása"},
            {"command": "cancel", "description": "Beállítás megszakítása"},
            {"command": "help", "description": "Súgó"},
        ]
        return self._post("setMyCommands", {"commands": commands})

    def delete_webhook(self) -> bool:
        return self._post("deleteWebhook", {"drop_pending_updates": False})

    def _send_message(self, chat_id: str, message: str) -> bool:
        return self._post(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )

    def _post(self, method: str, payload: dict[str, object]) -> bool:
        try:
            self._post_for_data(method, payload)
            return True
        except httpx.HTTPStatusError as exc:
            logger.error("Telegram %s failed with HTTP %s.", method, exc.response.status_code)
            return False
        except Exception as exc:
            logger.error("Telegram %s failed (%s).", method, type(exc).__name__)
            return False

    def _post_for_data(
        self,
        method: str,
        payload: dict[str, object],
        request_timeout: float = 15.0,
    ) -> dict:
        with httpx.Client(timeout=request_timeout) as client:
            response = client.post(f"{self.api_url}/{method}", json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise RuntimeError("Telegram API rejected the request.")
        return data