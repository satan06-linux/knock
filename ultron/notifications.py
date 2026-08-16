"""
notifications.py - P4.3: Notification System.
NotificationManager with ConsoleNotifier + EmailNotifier.
All content passes through SecretRedactor before sending.
Config stored in ~/.ultron/settings.json (never in project files or git).
"""
import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import List, Optional, Dict, Any

from ultron.secret_redactor import SecretRedactor
from ultron.event_bus import get_bus, BusEvent

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".ultron", "settings.json")

# Events that trigger notifications
NOTIFICATION_EVENTS = {
    "security.scope_violation": "🚨 Scope Violation",
    BusEvent.REPAIR_EXHAUSTED:  "⚠ Repair Exhausted",
    "ultron.crashed":           "❌ Ultron Crashed",
    "security.injection_detected": "🔐 Injection Attempt Detected",
    BusEvent.MODEL_DEGRADED:    "⚠ Model Degraded",
}


def load_settings() -> Dict[str, Any]:
    if os.path.isfile(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_settings(data: Dict[str, Any]):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    try:
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, SETTINGS_PATH)
    except Exception:
        pass


class ConsoleNotifier:
    """Always active — prints important events to terminal."""

    def __init__(self, console=None):
        self.console = console
        self._redactor = SecretRedactor()

    def notify(self, title: str, body: str, severity: str = "warn"):
        safe_title = self._redactor.redact_for_terminal(title)
        safe_body = self._redactor.redact_for_terminal(body)
        color = {"warn": "yellow", "error": "red", "info": "cyan"}.get(severity, "white")
        msg = f"[{color}][NOTIFICATION] {safe_title}[/{color}]\n{safe_body}"
        if self.console:
            self.console.print(msg)
        else:
            print(f"[NOTIFICATION] {safe_title}\n{safe_body}")


class EmailNotifier:
    """
    Opt-in email notifications via SMTP.
    Config in ~/.ultron/settings.json under 'email' key.
    NEVER sends raw secrets — all content through SecretRedactor.
    """

    def __init__(self):
        self._redactor = SecretRedactor()

    def _get_config(self) -> Optional[Dict]:
        settings = load_settings()
        return settings.get("email")

    def is_configured(self) -> bool:
        cfg = self._get_config()
        return bool(cfg and cfg.get("to") and cfg.get("smtp_host"))

    def notify(self, title: str, body: str, severity: str = "warn") -> bool:
        if not self.is_configured():
            return False
        cfg = self._get_config()

        # Redact all content before sending
        safe_body = self._redactor.redact_for_email(body)
        safe_title = self._redactor.redact_for_email(title)

        try:
            msg = MIMEMultipart()
            msg["From"] = cfg.get("from", "ultron@localhost")
            msg["To"] = cfg["to"]
            msg["Subject"] = f"[Ultron {severity.upper()}] {safe_title}"
            msg.attach(MIMEText(
                f"{safe_body}\n\n---\nSent by Ultron CLI at {datetime.now().isoformat()}",
                "plain"
            ))

            with smtplib.SMTP(cfg["smtp_host"], cfg.get("smtp_port", 587), timeout=10) as server:
                if cfg.get("use_tls", True):
                    server.starttls()
                if cfg.get("username") and cfg.get("password"):
                    server.login(cfg["username"], cfg["password"])
                server.send_message(msg)
            return True
        except Exception:
            return False

    @staticmethod
    def configure(to: str, smtp_host: str, smtp_port: int = 587,
                  username: str = "", password: str = "",
                  from_addr: str = "ultron@localhost", use_tls: bool = True):
        """Save email config to ~/.ultron/settings.json."""
        settings = load_settings()
        settings["email"] = {
            "to": to,
            "from": from_addr,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "username": username,
            "password": password,  # stored locally, never in git
            "use_tls": use_tls,
        }
        save_settings(settings)


class NotificationManager:
    """
    Central notification hub. Subscribes to EventBus for important events.
    Routes to all active notifiers after secret redaction.
    """

    def __init__(self, console=None):
        self._console_notifier = ConsoleNotifier(console)
        self._email_notifier = EmailNotifier()
        self._redactor = SecretRedactor()
        self._register_bus_handlers()

    def _register_bus_handlers(self):
        bus = get_bus()
        for event_type in NOTIFICATION_EVENTS:
            bus.subscribe(event_type, self._handle_event)

    def _handle_event(self, data: Dict[str, Any]):
        event_type = data.get("_event_type", "unknown")
        title = NOTIFICATION_EVENTS.get(event_type, f"Event: {event_type}")

        # Build body from event data (redact everything)
        body_parts = []
        for k, v in data.items():
            if k.startswith("_"):
                continue
            safe_v = self._redactor.redact_for_terminal(str(v))
            body_parts.append(f"{k}: {safe_v}")
        body = "\n".join(body_parts)

        severity = "error" if "crash" in event_type or "violation" in event_type else "warn"
        self.notify(title, body, severity)

    def notify(self, title: str, body: str, severity: str = "warn"):
        """Send notification to all active notifiers."""
        self._console_notifier.notify(title, body, severity)
        if self._email_notifier.is_configured():
            self._email_notifier.notify(title, body, severity)

    @staticmethod
    def configure_email(to: str, smtp_host: str, **kwargs):
        EmailNotifier.configure(to, smtp_host, **kwargs)
