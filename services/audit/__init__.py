"""Registro de auditoría append-only con encadenamiento por hash.

Ver docs/audit-log/audit-log.md. Módulos:

- `catalog`: acciones, actores, motivos de acceso y campos permitidos (BRIEFING).
- `models`: tabla `audit_log_entries` y las guardas que impiden UPDATE/DELETE.
- `chain`: huella de cada entrada y verificación de la cadena.
- `writer`: la ÚNICA forma de escribir (`record`).
- `queries`, `anomalies`, `router`: consulta, detección de accesos inusuales y API.
"""
