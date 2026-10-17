"""Отправка писем (коды подтверждения и сброса пароля) через SMTP."""

from __future__ import annotations

import asyncio
import html
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr

from ..settings import MailSettings

logger = logging.getLogger("netschoolbot.mail")


class _SMTPSSL(smtplib.SMTP_SSL):
    """SMTP_SSL, сверяющий сертификат с tls_name, а не с адресом подключения."""

    def __init__(self, *args, tls_name: str = "", **kwargs) -> None:
        self._tls_name = tls_name
        super().__init__(*args, **kwargs)

    def _get_socket(self, host, port, timeout):
        self._host = self._tls_name or host
        return super()._get_socket(host, port, timeout)


class Mailer:
    def __init__(self, settings: MailSettings, site: str = "") -> None:
        self._settings = settings
        # Адрес сайта: логотип и ссылка в подвале писем.
        self.site = site.rstrip("/")
        # Для тестов: письма складываются сюда вместо SMTP.
        self.outbox: list[EmailMessage] | None = None

    @property
    def configured(self) -> bool:
        return self.outbox is not None or self._settings.configured

    async def send(self, to: str, subject: str, text: str, *, html_body: str | None = None) -> None:
        message = EmailMessage()
        name, address = parseaddr(self._settings.sender or "noreply@localhost")
        message["From"] = formataddr((name or "Дневник", address))
        message["To"] = to
        message["Subject"] = subject
        # Без даты и Message-ID почтовики охотнее кладут письмо в спам.
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=address.rpartition("@")[2] or None)
        message.set_content(text)
        if html_body:
            message.add_alternative(html_body, subtype="html")
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
            smtp: smtplib.SMTP = _SMTPSSL(settings.host, settings.port, context=context, timeout=20,
                                          tls_name=settings.tls_name)
        else:
            smtp = smtplib.SMTP(settings.host, settings.port, timeout=20)
        try:
            if settings.security == "starttls":
                if settings.tls_name:
                    smtp._host = settings.tls_name  # starttls сверяет сертификат с _host
                smtp.starttls(context=context)
            if settings.user:
                smtp.login(settings.user, settings.password)
            smtp.send_message(message)
        finally:
            try:
                smtp.quit()
            except smtplib.SMTPException:
                pass


def letter(title: str, lead: str, *, code: str = "", note: str = "", site: str = "") -> tuple[str, str]:
    """Письмо в двух видах: (простой текст, HTML). Код — крупно посередине.
    site — адрес сайта: сверху иконка приложения, как на домашнем экране."""
    text = "\n\n".join(part for part in (f"{lead} {code}".strip(), note) if part)
    esc = html.escape
    code_html = (
        f'<div style="margin:24px 0;padding:18px 0;border-radius:16px;background:#eef2ff;'
        f'font:700 34px/1 ui-monospace,Menlo,monospace;letter-spacing:10px;color:#3730a3">{esc(code)}</div>'
        if code else ""
    )
    note_html = f'<p style="margin:0;color:#6b7280;font-size:14px;line-height:1.5">{esc(note)}</p>' if note else ""
    logo_html = (
        f'<img src="{esc(site)}/static/icon-192.png" width="56" height="56" alt="Дневник" '
        f'style="display:block;margin:0 auto;border-radius:14px">'
        if site else ""
    )
    footer = esc(site.split("//")[-1]) if site else "Дневник"
    body = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{esc(title)}</title></head>
<body style="margin:0;padding:24px 12px;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif">
<div style="max-width:440px;margin:0 auto;background:#fff;border-radius:20px;padding:32px 28px;text-align:center;color:#111827">
{logo_html}
<h1 style="margin:12px 0 8px;font-size:22px">{esc(title)}</h1>
<p style="margin:0 0 4px;font-size:16px;line-height:1.5">{esc(lead)}</p>
{code_html}{note_html}
</div>
<p style="text-align:center;color:#9ca3af;font-size:12px;margin:16px 0 0">Дневник · {footer}</p>
</body></html>"""
    return text, body
