"""email_service.py — el fallo de envío se loguea con su motivo, pero nunca con
direcciones de correo (HIPAA / UK GDPR: sin datos de usuarios en logs).

Resend se sustituye con monkeypatch: estos tests nunca envían correo real ni
necesitan RESEND_API_KEY válida.
"""

from __future__ import annotations

import logging

import email_service


def _fail_with(message: str):
    def fake_send(_params):
        raise RuntimeError(message)

    return fake_send


def test_send_failure_logs_reason_without_recipient(monkeypatch, caplog) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(email_service.resend.Emails, "send", _fail_with("API key is invalid"))

    with caplog.at_level(logging.ERROR, logger="email_service"):
        sent = email_service.send_password_reset_email("paciente.real@hospital.org", "token-123")

    assert sent is False
    assert "API key is invalid" in caplog.text
    assert "paciente.real@hospital.org" not in caplog.text


def test_send_failure_masks_emails_inside_provider_message(monkeypatch, caplog) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(
        email_service.resend.Emails,
        "send",
        _fail_with("You can only send testing emails to your own email address (owner.name+dev@example.co.uk)"),
    )

    with caplog.at_level(logging.ERROR, logger="email_service"):
        email_service.send_password_reset_email("otra.persona@example.com", "token-123")

    assert "owner.name+dev@example.co.uk" not in caplog.text
    assert "otra.persona@example.com" not in caplog.text
    assert "<email>" in caplog.text
    assert "You can only send testing emails" in caplog.text


def test_send_success_logs_nothing(monkeypatch, caplog) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(email_service.resend.Emails, "send", lambda _params: {"id": "msg_1"})

    with caplog.at_level(logging.DEBUG, logger="email_service"):
        sent = email_service.send_password_reset_email("alguien@example.com", "token-123")

    assert sent is True
    assert "alguien@example.com" not in caplog.text


def test_redact_emails_leaves_text_without_addresses_untouched() -> None:
    assert email_service._redact_emails("API key is invalid") == "API key is invalid"
