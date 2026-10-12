"""Отправка писем (коды подтверждения и сброса пароля) через SMTP."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from ..settings import MailSettings

logger = logging.getLogger("netschoolbot.mail")


class Mailer:
    def __init__(self, settings: MailSettings) -> None:
        self._settings = settings
        # Для тестов: письма складываются сюда вместо SMTP.
        self.outbox: list[EmailMessage] | None = None

    @property
    def configured(self) -> bool:
        return self.outbox is not None or self._settings.configured

    async def send(self, to: str, subject: str, text: str) -> None:
        message = EmailMessage()
        message["From"] = self._settings.sender or "noreply@localhost"
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        if self.outbox is not None:
            self.outbox.append(message)
            return
        if not self._settings.configured:
            raise RuntimeError("Почта не настроена")
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailMessage) -> None:
        settings = self._settings
        context = ssl.create_default_context()
        if settings.security == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(settings.host, settings.port, context=context, timeout=20)
        else:
            smtp = smtplib.SMTP(settings.host, settings.port, timeout=20)
        try:
            if settings.security == "starttls":
                smtp.starttls(context=context)
            if settings.user:
                smtp.login(settings.user, settings.password)
            smtp.send_message(message)
        finally:
            try:
                smtp.quit()
            except smtplib.SMTPException:
                pass
