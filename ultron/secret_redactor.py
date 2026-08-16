"""
secret_redactor.py - P0.5: Destination-aware secret redaction.
Applied to ALL tool output before it reaches model/log/terminal/email.
"""
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple


# ---------------------------------------------------------------------------
# Secret classification
# ---------------------------------------------------------------------------

class SecretType(str, Enum):
    API_KEY             = "API_KEY"
    ACCESS_TOKEN        = "ACCESS_TOKEN"
    PASSWORD            = "PASSWORD"
    DATABASE_CREDENTIAL = "DATABASE_CREDENTIAL"
    PRIVATE_KEY         = "PRIVATE_KEY"
    JWT                 = "JWT"
    AUTH_HEADER         = "AUTH_HEADER"
    UNKNOWN_SECRET      = "UNKNOWN_SECRET"


class DetectionConfidence(str, Enum):
    EXACT_PATTERN   = "EXACT_PATTERN"    # known format (sk-..., eyJ...)
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"  # known key name + value
    MEDIUM_CONFIDENCE = "MEDIUM_CONFIDENCE"  # suspicious pattern
    POSSIBLE_SECRET = "POSSIBLE_SECRET"  # entropy signal (future)


@dataclass
class DetectedSecret:
    secret_type: SecretType
    confidence: DetectionConfidence
    key_name: str           # e.g. "API_KEY", "DATABASE_URL"
    redacted_value: str     # what to show (not the real value)
    location: str           # "line N" or "field X"


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

# (pattern, secret_type, confidence, placeholder_for_model)
_PATTERNS: List[Tuple[re.Pattern, SecretType, DetectionConfidence, str]] = [
    # OpenAI API keys
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
     SecretType.API_KEY, DetectionConfidence.EXACT_PATTERN, "[OPENAI_KEY]"),

    # Anthropic API keys
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
     SecretType.API_KEY, DetectionConfidence.EXACT_PATTERN, "[ANTHROPIC_KEY]"),

    # Groq API keys
    (re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
     SecretType.API_KEY, DetectionConfidence.EXACT_PATTERN, "[GROQ_KEY]"),

    # Google API keys
    (re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"),
     SecretType.API_KEY, DetectionConfidence.EXACT_PATTERN, "[GOOGLE_KEY]"),

    # Generic bearer tokens
    (re.compile(r"\bBearer\s+[A-Za-z0-9_.-]{20,}\b", re.IGNORECASE),
     SecretType.AUTH_HEADER, DetectionConfidence.HIGH_CONFIDENCE, "Bearer [TOKEN]"),

    # JWTs (three base64 parts separated by dots)
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
     SecretType.JWT, DetectionConfidence.EXACT_PATTERN, "[JWT_TOKEN]"),

    # PEM private keys
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
                re.DOTALL),
     SecretType.PRIVATE_KEY, DetectionConfidence.EXACT_PATTERN, "[PRIVATE_KEY_BLOCK]"),

    # Database URLs with embedded credentials
    (re.compile(r"((?:mysql|postgresql|postgres|mongodb|redis|mssql|sqlite)://[^:]+:)([^@\s]+)(@[^\s]+)",
                re.IGNORECASE),
     SecretType.DATABASE_CREDENTIAL, DetectionConfidence.EXACT_PATTERN, r"\1[SECRET_DB_PASS]\3"),

    # KEY=value assignments (env-var style)
    (re.compile(r"(?i)((?:API[_-]?KEY|SECRET[_-]?KEY|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|PASSWORD|PASSWD|SECRET|PRIVATE[_-]?KEY)\s*=\s*)([^\s\n\"']{6,})",),
     SecretType.API_KEY, DetectionConfidence.HIGH_CONFIDENCE, r"\1[REDACTED]"),
]

# Separate list for key=value (has a group reference in replacement)
_KV_PATTERN = re.compile(
    r"(?i)((?:API[_-]?KEY|SECRET[_-]?KEY|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|PASSWORD|PASSWD|SECRET|PRIVATE[_-]?KEY)\s*=\s*)([^\s\n\"']{6,})"
)


# ---------------------------------------------------------------------------
# SecretRedactor
# ---------------------------------------------------------------------------

class SecretRedactor:
    """
    Destination-aware secret redaction.
    Different outputs get different treatment:
      model    → structural placeholder (preserves context)
      log      → fully redacted
      terminal → mostly redacted, type shown
      email    → fully redacted
    """

    def detect(self, text: str) -> List[DetectedSecret]:
        """Detect secrets in text. Returns findings (does NOT redact)."""
        findings = []
        seen = set()

        for pattern, stype, confidence, _ in _PATTERNS[:8]:  # skip KV (separate)
            for m in pattern.finditer(text):
                key = m.group(0)[:20]
                if key not in seen:
                    seen.add(key)
                    findings.append(DetectedSecret(
                        secret_type=stype,
                        confidence=confidence,
                        key_name=stype.value,
                        redacted_value="[REDACTED]",
                        location=f"pos {m.start()}",
                    ))

        # KV pattern
        for m in _KV_PATTERN.finditer(text):
            key = m.group(1)[:30]
            if key not in seen:
                seen.add(key)
                findings.append(DetectedSecret(
                    secret_type=SecretType.API_KEY,
                    confidence=DetectionConfidence.HIGH_CONFIDENCE,
                    key_name=m.group(1).strip().rstrip("=").strip(),
                    redacted_value="[REDACTED]",
                    location=f"pos {m.start()}",
                ))

        return findings

    def redact_for_model(self, text: str) -> str:
        """
        Structural redaction — preserves context so model understands structure.
        DB URLs keep host/port, secrets replaced with typed placeholder.
        """
        result = text

        # DB URLs first (has group replacement)
        result = re.sub(
            r"((?:mysql|postgresql|postgres|mongodb|redis|mssql|sqlite)://[^:]+:)([^@\s]+)(@[^\s]+)",
            r"\1[SECRET_DB_PASS]\3",
            result,
            flags=re.IGNORECASE,
        )

        # KV assignments
        result = _KV_PATTERN.sub(r"\1[REDACTED]", result)

        # Bearer tokens
        result = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9_.-]{20,}", r"\1[TOKEN]", result)

        # JWTs
        result = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[JWT_TOKEN]", result)

        # PEM blocks
        result = re.sub(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            "[PRIVATE_KEY_BLOCK]",
            result,
            flags=re.DOTALL,
        )

        # Specific key patterns
        result = re.sub(r"\bsk-[A-Za-z0-9]{20,}\b", "[OPENAI_KEY]", result)
        result = re.sub(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b", "[ANTHROPIC_KEY]", result)
        result = re.sub(r"\bgsk_[A-Za-z0-9]{20,}\b", "[GROQ_KEY]", result)
        result = re.sub(r"\bAIza[A-Za-z0-9_-]{35}\b", "[GOOGLE_KEY]", result)

        return result

    def redact_for_log(self, text: str) -> str:
        """Full redaction for log storage — no structural info preserved."""
        result = self.redact_for_model(text)
        # Additionally collapse all placeholders to [REDACTED]
        result = re.sub(r"\[(?:OPENAI|ANTHROPIC|GROQ|GOOGLE|JWT|SECRET_DB_PASS|TOKEN|PRIVATE_KEY_BLOCK|REDACTED)[^\]]*\]",
                        "[REDACTED]", result)
        return result

    def redact_for_terminal(self, text: str) -> str:
        """Mostly redacted for terminal — shows type hint."""
        result = self.redact_for_model(text)
        return result

    def redact_for_email(self, text: str) -> str:
        """Fully redacted for email — same as log."""
        return self.redact_for_log(text)

    def has_secrets(self, text: str) -> bool:
        """Quick check if text contains any detectable secrets."""
        return len(self.detect(text)) > 0
