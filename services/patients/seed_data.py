"""Pacientes 100 % sintéticos (BRIEFING §6). Nombres, fechas y diagnósticos
inventados; los nombres llevan "(sintético)" para que nadie los confunda con
datos reales en una captura.

Las referencias son deterministas (`uuid5` del índice): sembrar dos veces da
las mismas `patient_ref` y no duplica pacientes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from services.audit import catalog
from services.patients.models import InsuranceClaim, PatientRecord

_NAMESPACE = uuid.UUID("0b8a4d36-61f4-4a9e-9d61-5d1c7e2f9a01")


def patient_ref_for(index: int) -> str:
    return "PT-" + uuid.uuid5(_NAMESPACE, f"patient:{index}").hex[:8].upper()


def claim_ref_for(index: int, claim: int) -> str:
    return "CL-" + uuid.uuid5(_NAMESPACE, f"claim:{index}:{claim}").hex[:8].upper()


@dataclass(frozen=True)
class SyntheticPatient:
    index: int
    department: str
    country: str
    full_name: str
    date_of_birth: str
    diagnosis: str
    clinical_notes: str
    insurance_plan: str
    balance_cents: int
    claims_cents: tuple[int, ...]


PATIENTS = (
    SyntheticPatient(1, "clinical_operations", "US", "Jordan Testwell (sintético)", "1971-03-14",
                     "Hipertensión esencial en seguimiento", "Control trimestral. Buena adherencia al tratamiento.",
                     "BlueCross PPO (ficticio)", 12000, (18000,)),
    SyntheticPatient(2, "clinical_operations", "US", "Riley Mockford (sintético)", "1985-09-02",
                     "Diabetes tipo 2", "Pendiente de revisión de analítica en la próxima visita.",
                     "Aetna HMO (ficticio)", 0, (25000, 9000)),
    SyntheticPatient(3, "clinical_operations", "UK", "Casey Placeholder (sintético)", "1990-12-21",
                     "Asma leve intermitente", "Inhalador de rescate. Sin crisis en el último trimestre.",
                     "NHS (ficticio)", 0, ()),
    SyntheticPatient(4, "clinical_operations", "UK", "Morgan Sampleton (sintético)", "1962-06-30",
                     "Artrosis de rodilla", "Derivación a fisioterapia pendiente de confirmar.",
                     "Bupa (ficticio)", 4500, (32000,)),
    SyntheticPatient(5, "clinical_operations", "US", "Taylor Fakesley (sintético)", "1958-01-09",
                     "Seguimiento postoperatorio", "Herida limpia. Retirar puntos en la próxima cita.",
                     "Medicare (ficticio)", 0, (41000,)),
    SyntheticPatient(6, "patient_experience", "US", "Avery Dummyson (sintético)", "1999-04-17",
                     "Revisión preventiva anual", "Paciente reprograma con frecuencia; recordar por SMS.",
                     "Cigna (ficticio)", 0, ()),
    SyntheticPatient(7, "patient_experience", "UK", "Quinn Notreal (sintético)", "1977-08-05",
                     "Consulta de dermatología", "Solicita cita en horario ampliado.",
                     "AXA (ficticio)", 7000, (15000,)),
    SyntheticPatient(8, "patient_experience", "US", "Rowan Synthia (sintético)", "1983-11-26",
                     "Vacunación de viaje", "Pendiente de segunda dosis.",
                     "UnitedHealthcare (ficticio)", 0, ()),
)


def build_patients(assignments: Optional[dict[int, str]] = None) -> list[tuple[PatientRecord, list[InsuranceClaim]]]:
    """Fichas y reclamaciones listas para `session.add`. `assignments`:
    índice de paciente → id del empleado asignado."""
    assignments = assignments or {}
    built = []
    for item in PATIENTS:
        currency = "USD" if item.country == "US" else "GBP"
        record = PatientRecord(
            id=str(uuid.uuid5(_NAMESPACE, f"record:{item.index}")),
            patient_ref=patient_ref_for(item.index),
            department_id=catalog.department_id(item.department),
            assigned_user_id=assignments.get(item.index),
            country=item.country,
            full_name=item.full_name,
            date_of_birth=item.date_of_birth,
            diagnosis=item.diagnosis,
            clinical_notes=item.clinical_notes,
            insurance_plan=item.insurance_plan,
            outstanding_balance_cents=item.balance_cents,
            currency=currency,
        )
        claims = [
            InsuranceClaim(
                id=str(uuid.uuid5(_NAMESPACE, f"claim-row:{item.index}:{n}")),
                claim_ref=claim_ref_for(item.index, n),
                patient_record_id=record.id,
                amount_cents=amount,
                currency=currency,
            )
            for n, amount in enumerate(item.claims_cents, start=1)
        ]
        built.append((record, claims))
    return built
