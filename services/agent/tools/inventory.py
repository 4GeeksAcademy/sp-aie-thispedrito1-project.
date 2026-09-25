"""Tool `check_inventory_stock`: stock en vivo del gestor de inventario (Hito 5).

Lee de Supabase con el mismo repositorio que GET /inventory/products
(`inventory_repository.list_supplies` + `get_current_stock`), en el propio
proceso y con una sesión de solo lectura que nunca hace commit. El stock es
el calculado de siempre (entradas − consumos) y es el TOTAL DE LA RED, igual
que en /inventory/products: no se desglosa por clínica.

Una sola responsabilidad: stock por producto. Las incidencias son otra tool.
Timeout de 5 s (no 3): la primera conexión al pooler de Supabase en frío se
ha medido en ~0,5 s y, dormido el proyecto, tarda mucho más o no responde;
en ese caso el agente cae al fallback, no se queda colgado.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, ContextManager, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.agent.tools.base import ToolNotFound, ToolResult, run_tool

TOOL_NAME = "check_inventory_stock"
TIMEOUT_S = 5.0
MAX_RESULTS = 10


class InventoryLookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=2, max_length=80, description="Nombre o SKU (o parte) del insumo")

    @field_validator("product")
    @classmethod
    def normalized(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("el producto debe tener al menos 2 caracteres")
        return value


class InventoryItem(BaseModel):
    """Los campos de MedicalSupplyRead."""

    id: int
    name: str
    sku: str
    category: str
    unit: str
    country: str
    current_stock: int
    expiry_date: Optional[date] = None


class InventoryLookupOutput(BaseModel):
    total_matches: int
    items: List[InventoryItem]


def _default_session() -> ContextManager[Any]:
    from sqlmodel import Session

    from database import get_inventory_engine

    return Session(get_inventory_engine())


def query_inventory(payload: InventoryLookupInput, session: Any) -> InventoryLookupOutput:
    """Búsqueda por nombre o SKU sin distinguir mayúsculas. Solo lecturas."""
    import inventory_repository

    needle = payload.product.casefold()
    matches = [
        supply
        for supply in inventory_repository.list_supplies(session)
        if needle in supply.name.casefold() or needle in supply.sku.casefold()
    ]
    if not matches:
        raise ToolNotFound(f"product {payload.product!r}")
    items = [
        InventoryItem(
            id=supply.id,
            name=supply.name,
            sku=supply.sku,
            category=supply.category,
            unit=supply.unit,
            country=supply.country,
            current_stock=inventory_repository.get_current_stock(session, supply.id),
            expiry_date=supply.expiry_date,
        )
        for supply in matches[:MAX_RESULTS]
    ]
    return InventoryLookupOutput(total_matches=len(matches), items=items)


def check_inventory_stock(
    payload: InventoryLookupInput,
    *,
    session_factory: Callable[[], ContextManager[Any]] = _default_session,
    timeout_s: float = TIMEOUT_S,
) -> ToolResult:
    """Punto de entrada de la tool: nunca lanza, siempre devuelve un ToolResult."""

    def call() -> InventoryLookupOutput:
        with session_factory() as session:
            return query_inventory(payload, session)

    return run_tool(TOOL_NAME, payload.model_dump(), call, timeout_s=timeout_s)
