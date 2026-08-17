"""
test_secret_exfiltration.py - Security tests for secret detection and exfiltration prevention.
"""
import pytest
from ultron.secret_redactor import SecretRedactor


def test_openai_api_key_redacted():
    redactor = SecretRedactor()
    raw = "My key is API_KEY=sk-abcdefghijklmnopqrst1234567890"
    redacted = redactor.redact_for_model(raw)
    assert "sk-abc" not in redacted


def test_pem_private_key_redacted():
    redactor = SecretRedactor()
    raw = "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgw...\n-----END PRIVATE KEY-----"
    redacted = redactor.redact_for_model(raw)
    assert "BEGIN PRIVATE KEY" not in redacted
