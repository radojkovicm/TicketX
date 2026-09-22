import os
import smtplib
import unittest
from unittest.mock import MagicMock, patch

import mailer
import notifications


class NotificationSecurityTests(unittest.TestCase):
    def test_email_template_escapes_ticket_and_user_content(self):
        html = notifications._build_html_email(
            {
                "id": 7,
                "title": "<script>alert(1)</script>",
                "description": "<img src=x onerror=alert(1)>",
                "priority": "high",
                "status": "new",
                "creator_name": "<b>Creator</b>",
                "assigned_name": "IT",
                "updated_at": "2026-01-01 12:00:00",
            },
            "Updated",
            "<p>Trusted internal markup</p>",
            actor_name="<em>Actor</em>",
        )
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("&lt;em&gt;Actor&lt;/em&gt;", html)

    def test_comment_notification_escapes_comment_markup(self):
        with patch.object(notifications, "_notify") as notify:
            notifications.notify_on_comment(1, 2, '<img src=x onerror="alert(1)">')
        message_html = notify.call_args.args[3]
        self.assertNotIn("<img src=x", message_html)
        self.assertIn("&lt;img", message_html)

    @patch.dict(
        os.environ,
        {
            "SMTP_SERVER": "smtp.example.test",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "mailer@example.test",
            "SMTP_PASSWORD": "secret",
            "SMTP_SENDER_EMAIL": "mailer@example.test",
            "SMTP_USE_TLS": "true",
            "SMTP_USE_SSL": "false",
        },
        clear=False,
    )
    @patch("mailer.smtplib.SMTP_SSL")
    @patch("mailer.smtplib.SMTP")
    def test_mailer_never_falls_back_to_another_server(self, smtp, smtp_ssl):
        server = MagicMock()
        server.sendmail.side_effect = smtplib.SMTPException("delivery failed")
        smtp.return_value = server

        with self.assertLogs("mailer", level="ERROR"):
            sent = mailer.send_email("user@example.test", "Subject", "<p>Body</p>")

        self.assertFalse(sent)
        smtp.assert_called_once_with("smtp.example.test", 587, timeout=20)
        smtp_ssl.assert_not_called()
