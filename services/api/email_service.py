from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import quote

import resend
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger(__name__)

DEFAULT_SENDER = "onboarding@resend.dev"


def _frontend_base_url() -> str:
    return os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")


def build_reset_url(token: str) -> str:
    return f"{_frontend_base_url()}/reset-password?token={quote(token, safe='')}"


def _render_html(reset_url: str) -> str:
    return f"""\
<!DOCTYPE html>
<html lang="es">
  <body style="margin:0;padding:0;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:24px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                 style="max-width:480px;background:#ffffff;border:1px solid #e2e8f0;border-radius:16px;padding:32px;">
            <tr><td>
              <h1 style="margin:0 0 12px;font-size:20px;color:#0f172a;">Restablece tu contraseña</h1>
              <p style="margin:0 0 20px;font-size:14px;line-height:22px;color:#475569;">
                Recibimos una solicitud para restablecer la contraseña de tu cuenta de HealthCore.
                Haz clic en el botón para elegir una nueva contraseña. Este enlace caduca pronto y solo puede usarse una vez.
              </p>
              <a href="{reset_url}"
                 style="display:inline-block;background:#1d4ed8;color:#ffffff;text-decoration:none;
                        font-weight:bold;font-size:14px;padding:12px 20px;border-radius:8px;">
                Restablecer contraseña
              </a>
              <p style="margin:20px 0 0;font-size:12px;line-height:20px;color:#94a3b8;word-break:break-all;">
                Si el botón no funciona, copia y pega este enlace en tu navegador:<br />{reset_url}
              </p>
              <p style="margin:20px 0 0;font-size:12px;line-height:20px;color:#94a3b8;">
                Si no solicitaste este cambio, puedes ignorar este correo.
              </p>
            </td></tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def send_password_reset_email(to_email: str, token: str) -> bool:
    """Send the password reset email via Resend. Returns True on success.

    Failures are logged and swallowed so the caller can keep the anti-enumeration
    contract (always respond 200 on /auth/forgot-password).
    """
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        logger.error("RESEND_API_KEY is not configured; cannot send password reset email")
        return False

    sender = os.getenv("EMAIL_FROM", DEFAULT_SENDER)
    reset_url = build_reset_url(token)

    resend.api_key = api_key
    try:
        resend.Emails.send(
            {
                "from": sender,
                "to": [to_email],
                "subject": "Restablece tu contraseña · HealthCore",
                "html": _render_html(reset_url),
            }
        )
        return True
    except Exception:  # noqa: BLE001 - never let email delivery break the endpoint
        logger.exception("Failed to send password reset email to %s", to_email)
        return False
