"""Enmascarado de errores en logs y DLQ de las tareas (HIPAA / UK GDPR).

Mismo criterio que email_service._redact_emails y run_log.redact: el
diagnóstico se conserva, cualquier dirección de correo se enmascara.
"""

from __future__ import annotations

import logging

from data.pipelines.monthly_clinic_supply_performance.run_log import redact

# Más largo que el de reporting.pipeline_runs (500): la DLQ es el único
# rastro del error final y el ticket pide el mensaje completo.
ERROR_MESSAGE_LIMIT = 4000

# Logger interno con el que Celery anuncia cada reintento y fallo. Repite el
# error crudo en el mensaje ("Retry in 20s: RuntimeError('... ana@x.com')")
# y en el traceback, fuera del control de ObservableTask.
CELERY_TRACE_LOGGER = "celery.app.trace"


def describe_error(error: BaseException) -> str:
    return redact(str(error) or repr(error), limit=ERROR_MESSAGE_LIMIT)


class RedactingFilter(logging.Filter):
    """Enmascara correos en el mensaje y en el traceback de cada registro.

    El traceback no se elimina (sirve para depurar): se formatea aquí, ya
    enmascarado, en `record.exc_text`, que logging.Formatter reutiliza en vez
    de volver a formatear `exc_info`."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), limit=ERROR_MESSAGE_LIMIT)
        record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info), limit=ERROR_MESSAGE_LIMIT)
        return True


def install_celery_log_redaction() -> None:
    """Idempotente: importar el módulo de tareas varias veces no apila filtros."""
    trace_logger = logging.getLogger(CELERY_TRACE_LOGGER)
    if not any(isinstance(existing, RedactingFilter) for existing in trace_logger.filters):
        trace_logger.addFilter(RedactingFilter())
