"""Genera el PDF del caso 4 del BRIEFING: una "RFP" con PHI indebida.

    services/api/.venv/bin/python scripts/make_phi_test_rfp.py [ruta.pdf]

Por defecto escribe data/raw/rfp/samples/healthcore-request-4-phi.pdf (en
.gitignore). Todos los datos del paciente son FICTICIOS y lo dicen en el
propio documento. Se sube por la UI de /rfps para comprobar que el ticket
queda `descartado` con `phi_detected`, marcado para Compliance, y que ni el
nombre ni el diagnóstico aparecen en la base de datos, los logs ni la UI.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "services" / "api"
for path in (str(ROOT_DIR), str(API_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from data.pipelines.rfp_intake.sample_pdf import write_pdf  # noqa: E402

DEFAULT_OUTPUT = ROOT_DIR / "data" / "raw" / "rfp" / "samples" / "healthcore-request-4-phi.pdf"

LINES = [
    "REQUEST FOR PROPOSAL (RFP) - TEST DOCUMENT, ALL PERSONAL DATA IS FICTITIOUS",
    "Employee Health Program - Northwind Logistics LLC (Dallas, Texas)",
    "",
    "Northwind Logistics requests a proposal for an on-site employee health program",
    "covering 450 warehouse employees, 12-month contract, proposals due September 30, 2026.",
    "The vendor must sign a Business Associate Agreement (BAA) under HIPAA.",
    "",
    "Attachment A - Clinical summary of the employee who triggered this request",
    "Patient: Daniel Fakename",
    "Date of birth: 04/12/1979",
    "MRN: 00482913",
    "Diagnosis: type 2 diabetes with uncontrolled blood pressure, metformin 500 mg twice daily.",
    "",
    "Contact: benefits@northwind-logistics.example",
]


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    try:
        write_pdf(LINES, output)
    except OSError as exc:
        print(f"No se pudo escribir {output}: {exc}", file=sys.stderr)
        return 1
    print(f"PDF de prueba (PHI ficticia) escrito en {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
