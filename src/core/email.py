from email.message import EmailMessage
from pathlib import Path

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.config import Settings


class OtpMailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        templates_dir = Path(__file__).resolve().parents[1] / "templates"
        self._templates = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=select_autoescape(["html", "xml"]),
        )

    async def send_otp(self, email: str, code: str, purpose: str) -> None:
        subject = "Complete your Surv registration" if purpose == "register" else "Sign in to Surv"
        message = EmailMessage()
        message["From"] = self._settings.smtp_from_email
        message["To"] = email
        message["Subject"] = subject
        context = {"code": code, "minutes": 15, "purpose": purpose}
        message.set_content(self._templates.get_template("otp.txt.j2").render(**context))
        message.add_alternative(
            self._templates.get_template("otp.html.j2").render(**context), subtype="html"
        )
        await aiosmtplib.send(
            message,
            hostname=self._settings.smtp_host,
            port=self._settings.smtp_port,
            username=self._settings.smtp_username,
            password=self._settings.smtp_password,
            use_tls=self._settings.smtp_use_tls,
            start_tls=self._settings.smtp_start_tls,
            timeout=10,
        )
