"""Tools de datos operativos en vivo del agente (Parte 2).

Una tool por fuente y una sola responsabilidad cada una: `incidents.py`
(estado de incidencias) e `inventory.py` (stock de insumos). Ambas son de
solo lectura, tienen timeout numérico y devuelven siempre un `ToolResult`
(`base.py`), nunca una excepción."""
