import logging
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.utils import formataddr


logger = logging.getLogger(__name__)


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _clean_header(value: str) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def send_email(to_emails, subject, html_body):
    """Send one HTML email using only the explicitly configured SMTP server."""
    if not to_emails:
        return False
    if isinstance(to_emails, str):
        to_emails = [to_emails]

    recipients = [_clean_header(address) for address in to_emails if _clean_header(address)]
    host = (os.getenv("SMTP_SERVER") or "").strip()
    port_raw = (os.getenv("SMTP_PORT") or "").strip()
    username = (os.getenv("SMTP_USERNAME") or "").strip()
    password = (os.getenv("SMTP_PASSWORD") or "").strip()
    from_email = _clean_header(os.getenv("SMTP_SENDER_EMAIL") or username)
    from_name = _clean_header(os.getenv("SMTP_SENDER_NAME") or "TicketX")

    if not host or not recipients or not from_email:
        logger.warning("Email skipped because SMTP configuration or recipients are incomplete.")
        return False

    try:
        port = int(port_raw)
    except ValueError:
        logger.error("SMTP_PORT must be an integer.")
        return False
    if not 1 <= port <= 65535:
        logger.error("SMTP_PORT is outside the valid range.")
        return False

    use_ssl = _get_bool("SMTP_USE_SSL", False)
    use_tls = _get_bool("SMTP_USE_TLS", True)
    if use_ssl and use_tls:
        logger.error("SMTP_USE_SSL and SMTP_USE_TLS cannot both be enabled.")
        return False

    message = MIMEText(html_body or "", "html", "utf-8")
    message["Subject"] = _clean_header(subject)
    message["From"] = formataddr((from_name, from_email))
    message["To"] = ", ".join(recipients)
    tls_context = ssl.create_default_context()

    server = None
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=20, context=tls_context)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            server.ehlo()
            if use_tls:
                server.starttls(context=tls_context)
                server.ehlo()

        if username and password:
            server.login(username, password)
        server.sendmail(from_email, recipients, message.as_string())
        logger.info("Email sent to %d recipient(s).", len(recipients))
        return True
    except (OSError, smtplib.SMTPException):
        logger.exception("SMTP delivery failed for configured server %s:%s.", host, port)
        return False
    finally:
        if server is not None:
            try:
                server.quit()
            except (OSError, smtplib.SMTPException):
                server.close()
