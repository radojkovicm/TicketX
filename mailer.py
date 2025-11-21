
import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "y")

def _send_via(host: str, port: int, use_ssl: bool, use_starttls: bool, username: str, password: str,
              from_email: str, from_name: str, to_emails, subject: str, html_body: str) -> bool:
    print(f"[mailer] Connecting to {host}:{port} SSL={use_ssl} STARTTLS={use_starttls}")
    msg = MIMEText(html_body or "", "html", "utf-8")
    msg["Subject"] = subject or ""
    msg["From"] = formataddr((from_name, from_email))
    msg["To"] = ", ".join(to_emails)

    server = None
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=20)
            server.ehlo()
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            server.ehlo()
            if use_starttls:
                server.starttls()
                server.ehlo()

        if username and password:
            server.login(username, password)

        server.sendmail(from_email, to_emails, msg.as_string())
        print(f"[mailer] Sent to {to_emails}")
        return True
    except Exception as e:
        print(f"[mailer] ERROR on {host}:{port} SSL={use_ssl} STARTTLS={use_starttls} -> {e}")
        return False
    finally:
        try:
            if server:
                server.quit()
        except Exception:
            pass

def send_email(to_emails, subject, html_body):
    
    print(f"[debug][mailer:send_email] enter to={to_emails} subject={subject}")
    # Read ENV at call-time to avoid stale values loaded at import.
    if not to_emails:
        print("[mailer] No recipients, skipping.")
        return False
    if isinstance(to_emails, str):
        to_emails = [to_emails]

    host = (os.getenv("SMTP_SERVER") or "").strip()
    port_raw = (os.getenv("SMTP_PORT") or "").strip()
    username = (os.getenv("SMTP_USERNAME") or "").strip()
    password = (os.getenv("SMTP_PASSWORD") or "").strip()
    from_email = (os.getenv("SMTP_SENDER_EMAIL") or username).strip()
    from_name = (os.getenv("SMTP_SENDER_NAME") or "Ticketx System").strip()

    if not host:
        print("[mailer] Missing SMTP_SERVER")
        return False
    try:
        port = int(port_raw) if port_raw else 0
    except ValueError:
        print(f"[mailer] Invalid SMTP_PORT: {port_raw}")
        return False
    if port <= 0:
        print("[mailer] Missing or invalid SMTP_PORT")
        return False

    use_ssl = _get_bool("SMTP_USE_SSL", False)
    use_tls = _get_bool("SMTP_USE_TLS", True)

    # First attempt: exactly what .env says
    if _send_via(host, port, use_ssl=use_ssl, use_starttls=(not use_ssl and use_tls),
                 username=username, password=password,
                 from_email=from_email, from_name=from_name,
                 to_emails=to_emails, subject=subject, html_body=html_body):
        return True

    # Fallbacks for Gmail common setups
    if not (host == "smtp.gmail.com" and port == 587):
        print("[mailer] Fallback: trying Gmail 587 STARTTLS")
        if _send_via("smtp.gmail.com", 587, use_ssl=False, use_starttls=True,
                     username=username, password=password,
                     from_email=from_email, from_name=from_name,
                     to_emails=to_emails, subject=subject, html_body=html_body):
            return True

    print("[mailer] Fallback: trying Gmail 465 SSL")
    if _send_via("smtp.gmail.com", 465, use_ssl=True, use_starttls=False,
                 username=username, password=password,
                 from_email=from_email, from_name=from_name,
                 to_emails=to_emails, subject=subject, html_body=html_body):
        return True

    print("[mailer] All attempts failed.")
    return False