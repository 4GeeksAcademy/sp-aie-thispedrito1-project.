"""Ayudantes de los tests del cierre (nombre único: no se importa desde conftest)."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

LOAD_DATE = date(2026, 11, 7)
PERIOD = date(2026, 10, 1)
PATIENT_NAME = "Emily Hartwell"
MEDICARE = "US.medicare_medicaid.patient_services"
LAB_US = "US.n/a.laboratory"


class FakeLLM:
    """Doble del cliente OpenAI: responde por orden a cada llamada.

    `responses`: lista de dicts {"tool_calls": [...]} o {"content": "..."} o
    {"error": Exception}. Si se acaban, repite la última."""

    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._responses = responses
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any):
        self.calls.append(kwargs)
        spec = self._responses[min(len(self.calls), len(self._responses)) - 1]
        if spec.get("error") is not None:
            raise spec["error"]
        tool_calls = [SimpleNamespace(function=SimpleNamespace(name=c["name"], arguments=json.dumps(c["arguments"])))
                      for c in spec.get("tool_calls", [])]
        message = SimpleNamespace(tool_calls=tool_calls or None, content=spec.get("content"))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def prompts_text(self) -> str:
        return json.dumps([c.get("messages") for c in self.calls], ensure_ascii=False)



LAB_US = "US.n/a.laboratory"


def good_plan() -> Dict[str, Any]:
    return {"tool_calls": [{"name": "payer_claims_drivers", "arguments": {"line_key": MEDICARE}},
                           {"name": "unit_cost_drivers", "arguments": {"line_key": LAB_US}}]}


def good_draft(revision_note: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    body = {
        "executive_summary": {
            "en": ["October 2026: result 318,284.19 USD vs 400,468.00 USD comparable budget.",
                   "Two lines exceed the 5% threshold: Medicare / Medicaid and US laboratory."],
            "es": ["Octubre de 2026: resultado de 318.284,19 USD frente a 400.468,00 USD comparables.",
                   "Dos partidas superan el umbral del 5 %: Medicare / Medicaid y laboratorio de EE. UU."],
        },
        "hypotheses": {
            MEDICARE: {"en": "Claims stayed at 4,000 but the denial rate rose from 14.0% to 22.6%, so paid claims "
                             "fell to 3,096.",
                       "es": "Las reclamaciones se mantuvieron en 4.000, pero la denegación subió del 14,0 % al "
                             "22,6 %, y las pagadas bajaron a 3.096."},
            LAB_US: {"en": "Same 1,500 tests; the price per test rose from 30.00 to 32.10 USD.",
                     "es": "Las mismas 1.500 pruebas; el precio por prueba subió de 30,00 a 32,10 USD."},
        },
        "revision_note": revision_note,
    }
    return {"content": json.dumps(body)}
