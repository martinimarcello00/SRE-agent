import logging
import os
from logging import Handler
from typing import Optional

import requests


class TelegramNotification:
    """Send Telegram messages and stream log records when credentials are configured."""

    def __init__(self, chat_id: Optional[str] = None, token: Optional[str] = None) -> None:
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.token = token or os.getenv("TELEGRAM_TOKEN")
        self.logger = logging.getLogger(__name__)

    @property
    def enabled(self) -> bool:
        """Return True when both token and chat id are available."""
        return bool(self.chat_id and self.token)

    def _ensure_configured(self) -> None:
        if not self.enabled:
            raise RuntimeError(
                "TelegramNotification requires TELEGRAM_CHAT_ID and TELEGRAM_TOKEN environment variables."
            )

    def send_telegram_message(self, message: str) -> None:
        """Send a text message to the configured chat."""
        self._ensure_configured()

        # Telegram messages are limited to 4096 characters.
        truncated_message = message if len(message) <= 4096 else f"{message[:4050]}…"

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        data = {
            "chat_id": self.chat_id,
            "text": truncated_message,
        }
        try:
            response = requests.post(url, data=data, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            self.logger.error("Failed to send Telegram message: %s", exc)

    def create_log_handler(self, level: int = logging.ERROR) -> Handler:
        """Return a logging handler that forwards records to Telegram."""
        self._ensure_configured()

        class _TelegramLogHandler(logging.Handler):
            def __init__(self, notifier: "TelegramNotification", handler_level: int) -> None:
                super().__init__(handler_level)
                self._notifier = notifier

            def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
                try:
                    msg = self.format(record)
                    self._notifier.send_telegram_message(
                        f"🚨 {record.levelname} | {record.name}\n{msg}"
                    )
                except Exception:  # pragma: no cover - defensive
                    self.handleError(record)

        return _TelegramLogHandler(self, level)

