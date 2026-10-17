"""Blocking transports, called in a worker thread. No automatic SMTP retry.

Only definite rejection permits retry. A disconnect/timeout during DATA is ambiguous:
the server may already have accepted the message, so the reservation stays pending.
"""

import base64
import json
import smtplib
import ssl
from contextlib import suppress
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Protocol
from uuid import uuid4

from app.config import Settings
from app.mail.validate import address


class MailError(RuntimeError):
    """A definite failure; the advisor may retry."""


class DeliveryUncertain(MailError):
    """Acceptance is unknown; keep pending and require manual resolution."""


class MailConfigError(MailError):
    """The configured provider cannot send (missing account settings); nothing is reserved."""


class MailProvider(Protocol):
    def send(self, recipient: str, subject: str, body: str) -> str: ...


class LogProvider:
    def __init__(self, settings: Settings):
        self.directory = settings.data_dir / "mail"

    def send(self, recipient: str, subject: str, body: str) -> str:
        message_id = f"log-{uuid4().hex}"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            # No sender account, password or config dump in this local test artifact.
            (self.directory / f"{message_id}.json").write_text(
                json.dumps(
                    {
                        "message_id": message_id,
                        "recipient": recipient,
                        "subject": subject,
                        "body": body,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "utf-8",
            )
        except OSError:
            raise MailError("local mail log could not be written") from None
        return message_id


class SMTPProvider:
    def __init__(self, settings: Settings):
        # Validate configuration before the service creates a pending row.
        try:
            address(settings.gmail_address)
        except ValueError:
            raise MailConfigError("MAIL_MODE=smtp needs a valid GMAIL_ADDRESS") from None
        if not settings.gmail_app_password or not settings.gmail_app_password.get_secret_value():
            raise MailConfigError("MAIL_MODE=smtp needs GMAIL_APP_PASSWORD")
        if any(c in settings.sender_name for c in "\r\n\x00"):
            raise MailConfigError("SENDER_NAME must be one line")
        self.settings = settings

    def send(self, recipient: str, subject: str, body: str) -> str:
        config = self.settings
        message = EmailMessage()
        message["From"] = formataddr((config.sender_name, config.gmail_address))
        message["To"], message["Subject"] = recipient, subject
        message["Message-ID"] = make_msgid(domain="dd-monitor.local")
        message.set_content(body, charset="utf-8")
        # set_content normally appends a newline. Encode the exact confirmed text instead.
        message.set_payload(base64.encodebytes(body.encode("utf-8")).decode("ascii"))
        message.replace_header("Content-Transfer-Encoding", "base64")
        smtp = None
        sending = False
        try:
            smtp = smtplib.SMTP("smtp.gmail.com", 587, timeout=config.smtp_timeout_s)
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(config.gmail_address, config.gmail_app_password.get_secret_value())
            sending = True
            refused = smtp.send_message(
                message, from_addr=config.gmail_address, to_addrs=[recipient]
            )
            if refused:
                raise MailError("SMTP rejected the recipient")
            return str(message["Message-ID"])
        except MailError:
            raise
        except (smtplib.SMTPResponseException, smtplib.SMTPRecipientsRefused):
            # Server explicitly rejected auth, envelope or DATA; never store its reply
            # (a reply can echo credentials or message content).
            raise MailError("SMTP rejected the message; check account and recipient") from None
        except Exception:
            if sending:
                raise DeliveryUncertain(
                    "SMTP acceptance unknown; check Gmail Sent and resolve the pending outreach"
                ) from None
            raise MailError("SMTP connection or authentication failed") from None
        finally:
            if smtp is not None:
                # QUIT failure after acceptance must never turn success into a retry.
                with suppress(Exception):
                    smtp.quit()
                with suppress(Exception):
                    smtp.close()


def make_provider(settings: Settings) -> MailProvider:
    return SMTPProvider(settings) if settings.mail_mode == "smtp" else LogProvider(settings)
