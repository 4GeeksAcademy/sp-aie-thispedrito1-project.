from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response, status

from models import Supplier, SupplierCreate, SupplierRateUpdate, SupplierStatusUpdate
from repository import SupplierRepository

router = APIRouter(prefix="/suppliers", tags=["suppliers"])
repo = SupplierRepository()


@router.post("", response_model=Supplier, status_code=status.HTTP_201_CREATED)
def create_supplier(payload: SupplierCreate) -> Supplier:
    created = repo.create(payload.model_dump(mode="json", exclude_none=True))
    return Supplier.model_validate(created)


@router.get("", response_model=list[Supplier])
def list_suppliers(
    country: str | None = Query(default=None),
    category: str | None = Query(default=None),
) -> list[Supplier]:
    results = repo.list(country=country, category=category)
    return [Supplier.model_validate(item) for item in results]


@router.get("/{supplier_id}", response_model=Supplier)
def get_supplier(supplier_id: int) -> Supplier:
    found = repo.get_by_id(supplier_id)
    if not found:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return Supplier.model_validate(found)


@router.patch("/{supplier_id}/rate", response_model=Supplier)
def patch_supplier_rate(supplier_id: int, payload: SupplierRateUpdate) -> Supplier:
    updated = repo.update_rate(supplier_id, payload.monthly_rate)
    if not updated:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return Supplier.model_validate(updated)


@router.patch("/{supplier_id}/status", response_model=Supplier)
def patch_supplier_status(supplier_id: int, payload: SupplierStatusUpdate) -> Supplier:
    updated = repo.update_status(supplier_id, payload.status.value)
    if not updated:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return Supplier.model_validate(updated)


@router.delete("/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_supplier(supplier_id: int) -> Response:
    deleted = repo.delete(supplier_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
