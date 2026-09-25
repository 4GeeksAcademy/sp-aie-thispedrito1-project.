"""Cómo llegan los datos en vivo al modelo y qué se responde cuando no llegan.

`tool_evidence()` convierte cada ToolResult correcto en un elemento de
contexto con la MISMA forma que un chunk del RAG (`source_document`,
`section`, `text`). Así el nodo de generación sigue llamando a
`rag.generate_answer(question, context)` sin tocar `data/pipelines/rag.py`,
con sus reglas de veracidad ("usa exclusivamente el CONTEXTO", "termina con
Fuente:") aplicadas también a los datos operativos.

`fallback_message()` es la respuesta del nodo `tool_fallback`: texto fijo,
sin modelo, que dice qué no se pudo confirmar y por qué, y nunca un estado ni
un stock supuestos.
"""

from __future__ import annotations

from typing import Any, Dict, List

INCIDENTS_DOCUMENT = "gestor-de-incidencias (datos en vivo)"
INVENTORY_DOCUMENT = "gestor-de-inventario (datos en vivo)"


def _incident_line(incident: Dict[str, Any]) -> str:
    updated = incident.get("updated_at") or "sin cambios registrados"
    return (
        f"Incidencia #{incident['id']}: estado {incident['status']} ({incident['status_label_es']}); "
        f"categoría {incident['category']}; origen {incident['origin']}; sede {incident['branch']}; "
        f"creada {incident['created_at']}; última actualización {updated}."
    )


def _incidents_evidence(result: Dict[str, Any]) -> Dict[str, Any]:
    data = result["data"]
    if data["mode"] == "by_id":
        incident = data["incidents"][0]
        return {"source_document": INCIDENTS_DOCUMENT, "section": f"Incidencia #{incident['id']}", "text": _incident_line(incident)}
    filters = ", ".join(f"{key}={value}" for key, value in sorted(result["args"].items()))
    shown = len(data["incidents"])
    lines = [f"Incidencias que cumplen {filters}: {data['total']} en total."]
    if data["total"] > shown:
        lines.append(f"Se muestran las {shown} más recientes.")
    lines.extend(_incident_line(incident) for incident in data["incidents"])
    return {"source_document": INCIDENTS_DOCUMENT, "section": f"Búsqueda ({filters})", "text": "\n".join(lines)}


def _inventory_evidence(result: Dict[str, Any]) -> Dict[str, Any]:
    data = result["data"]
    lines = [f"Insumos que coinciden con «{result['args']['product']}»: {data['total_matches']}."]
    for item in data["items"]:
        expiry = item.get("expiry_date") or "sin fecha de caducidad registrada"
        lines.append(
            f"{item['name']} (SKU {item['sku']}, {item['country']}): stock total en la red "
            f"{item['current_stock']} {item['unit']}; caducidad {expiry}."
        )
    return {"source_document": INVENTORY_DOCUMENT, "section": f"Stock de «{result['args']['product']}»", "text": "\n".join(lines)}


EVIDENCE_BUILDERS = {"lookup_incident": _incidents_evidence, "check_inventory_stock": _inventory_evidence}


def tool_evidence(tool_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Contexto para el modelo a partir de las tools que respondieron bien."""
    return [EVIDENCE_BUILDERS[result["tool"]](result) for result in tool_results if result["status"] == "ok"]


def fallback_message(result: Dict[str, Any]) -> str:
    """Respuesta honesta para una tool que no pudo dar el dato."""
    args = result["args"]
    if result["tool"] == "lookup_incident":
        if result["status"] == "not_found":
            return (
                f"No encuentro la incidencia #{args['ticket_id']} en el gestor de incidencias. "
                "Revisa el número: no des ningún estado al paciente sin confirmarlo."
            )
        target = f"el estado de la incidencia #{args['ticket_id']}" if "ticket_id" in args else "las incidencias"
        return (
            f"No pude confirmar {target} ahora mismo: el gestor de incidencias no respondió. "
            "Inténtalo de nuevo en unos minutos y no des un estado sin confirmarlo."
        )
    if result["status"] == "not_found":
        return (
            f"No encuentro ningún insumo que coincida con «{args['product']}» en el inventario. "
            "Revisa el nombre o el SKU antes de confirmar disponibilidad."
        )
    return (
        f"No pude confirmar el stock de «{args['product']}» ahora mismo: el gestor de inventario no respondió. "
        "No confirmes disponibilidad sin comprobarla."
    )
