# services/supplier_contracts — proveedores y contratos (supervisor + especialistas)

Supervisor + especialista de precios + especialista de contratos. Stakeholder Dr. Marcus Reid. Detalle en `docs/supplier-contracts/supplier-contracts.md`.

```bash
services/api/.venv/bin/python scripts/seed_supplier_contracts.py     # CSV + 4 PDF con fecha de hoy, ingesta en sc_*, cuentas y alertas
services/api/.venv/bin/python -m pytest tests/supplier_contracts     # sin red
(cd services/api && .venv/bin/python -m pytest tests/test_supplier_contracts_api.py -o addopts="")
```

Cuentas (Marcus, Claire, Sandra) en `data/supplier_contracts/demo-users.db.json` (API con `SUPPLIERS_DB_PATH`). Pantalla `/supplier-contracts`. `SUPPLIER_CONTRACTS_USE_MODEL=0` = plan por reglas y borradores de plantilla.

Mapa: `catalog.py` (BRIEFING como datos: 7 proveedores, 4 contratos, umbrales, aprobadores, IPC 2,9 %, 1 GBP = 1,27 USD, `AGENT_TOOLS`, `SENSITIVE_BILLING`), `seed_data.py`, `ingestion.py` (CSV con fila de origen; PDF con pdfplumber), `calculations.py` (Decimal, puras), `sensitive.py`, `runtime.py` (contratos E/S, `ActionLog`, `AgentRuntime` con lista cerrada → `ToolNotAllowed`), `pricing.py`, `contracts.py`, `supervisor.py` (tope de 8 consultas), `alerts.py`, `brief.py`, `communications.py`, `repository.py`, `models.py` (11 tablas `sc_*`), `router.py`.

## Reglas que no hay que romper

- El modelo planifica (function calling) con respaldo por reglas; la extracción de contratos es determinista.
- **El modelo nunca calcula**: solo elige especialistas/intenciones y redacta. Un borrador con un número que no está en los hechos o con un dato sensible se descarta y sale la plantilla (`validate_draft`).
- Coordinación en código: el % que evalúa contratos lo calcula precios; BAA/DPA primero (una sola consulta por proveedor); subida > 4 % → impacto + permiso automáticos.
- BAA/DPA bloquea brief y borradores (409) hasta que Cumplimiento autoriza (`/compliance-clearances`). Lone Star (sin contrato ni BAA) alerta y el brief la condiciona al BAA.
- La cláusula de facturación nunca se lee ni se cita (`EXCLUDED_CLAUSE_HEADINGS`); `redact` como segunda capa. Preguntas y prompts solo como SHA-256; personas como `human:<seudónimo>`.
- Aprobadores: Marcus siempre; + Claire si hay datos de pacientes; + Sandra si el valor anual > 500.000 USD (GBP × 1,27). "Enviado" = registro, solo Operaciones Clínicas y con todas las aprobaciones.
- `brief.recommend_actions`: ninguna cifra puede salir de fuera de `BriefInputs` (test).

## Gotchas

- pdfplumber parte las cláusulas largas en varias líneas: se reconstruyen antes de extraer. Una mención de pasada no contradice a la cláusula exacta (`_single` se queda con la de mayor confianza).
- Tests sin `from conftest import ...` (con la batería completa Python entrega otro `conftest.py`).
