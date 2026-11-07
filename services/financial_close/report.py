"""Informe de cierre: cifras con su origen + texto en inglés y en español.

Estructura (BRIEFING, "Estructura del informe"):
1. Resumen ejecutivo (máx. 5 líneas)       5. Desviaciones relevantes (hipótesis + cifras)
2. Resultado consolidado en USD             6. Datos faltantes o dudosos
3. Ingresos por país y pagador (local)      7. Anexo de trazabilidad (entry_id de cada cifra)
4. Gastos por país (local)

`build_figures` es independiente del idioma: cada cifra tiene un id estable
(`line:<partida>:actual`, `consolidated:revenue:actual`, `driver:<partida>:<hecho>`)
y sus fuentes. Los dos textos se pintan desde las MISMAS cifras; solo cambia el
formato de los números y las palabras. `figures_sha256` es la huella de las
cifras: dos ejecuciones sobre los mismos datos dan la misma.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, Dict, List, Optional

from services.financial_close import formatting as fmt
from services.financial_close.agent import Analysis
from services.financial_close.calculations import STATUS_MISSING, STATUS_UNBUDGETED, pct_display
from services.financial_close.catalog import (
    COUNTRIES,
    COUNTRY_LABELS,
    DEVIATION_THRESHOLD_PCT,
    FX_REFERENCE,
    FX_TO_USD,
    PEOPLE,
    REVIEWER_ROLE,
    line_label,
    metric_label,
)
from services.financial_close.ingestion import ISSUE_LABELS
from services.financial_close.narrative import PREFIX, UNEXPLAINED_PREFIX, month_label

FX_SOURCE = {"kind": "fx", "reference": FX_REFERENCE}


def fx_text(lang: str) -> str:
    """El mismo tipo de cambio escrito en el formato de cada idioma."""
    return f"1 GBP = {fmt.number(FX_TO_USD['GBP'], lang, 2)} USD"


def _ledger_sources(entry_ids) -> List[Dict[str, Any]]:
    return [{"kind": "ledger", "entry_id": eid} for eid in entry_ids]


def _s(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else str(value)


def build_figures(analysis: Analysis) -> Dict[str, Dict[str, Any]]:
    figures: Dict[str, Dict[str, Any]] = {}

    def add(fid: str, value, unit: str, sources: List[Dict[str, Any]], **extra) -> None:
        figures[fid] = {"value": _s(value), "unit": unit, "sources": sources, **extra}

    for c in analysis.comparisons:
        budget_src = [{"kind": "budget", "source_file": "budget.csv", "source_row": c.budget_row}] if c.budget_row else []
        add(f"line:{c.key}:budget", c.budget, c.currency, budget_src, line_key=c.key)
        if c.status == STATUS_MISSING:
            add(f"line:{c.key}:actual", None, c.currency, [], line_key=c.key, status="missing")
        else:
            add(f"line:{c.key}:actual", c.actual, c.currency, _ledger_sources(c.entry_ids), line_key=c.key,
                status=c.status)
        if c.variance_pct is not None:
            add(f"line:{c.key}:variance_pct", pct_display(c.variance_pct), "pct",
                [{"kind": "calculation", "of": [f"line:{c.key}:actual", f"line:{c.key}:budget"]}], line_key=c.key)
    for kind, line in analysis.consolidated.items():
        base = f"consolidated:{kind}"
        add(f"{base}:actual", line.actual, "USD", [*_ledger_sources(line.entry_ids), FX_SOURCE],
            incomplete=line.incomplete)
        add(f"{base}:budget", line.budget, "USD", [{"kind": "calculation", "of": "budget.csv"}, FX_SOURCE])
        add(f"{base}:comparable_budget", line.comparable_budget, "USD",
            [{"kind": "calculation", "of": "budget.csv without lines that have no records"}, FX_SOURCE])
        add(f"{base}:variance_pct", pct_display(line.variance_pct), "pct",
            [{"kind": "calculation", "of": [f"{base}:actual", f"{base}:comparable_budget"]}])
    for dev in analysis.deviations:
        if not dev.driver or dev.driver.status != "ok":
            continue
        for fact in dev.driver.facts:
            sources = []
            for src in fact.sources:
                if src == "calculation":
                    sources.append({"kind": "calculation", "of": dev.driver.tool})
                elif src.startswith("operations.csv row "):
                    sources.append({"kind": "operations", "source_file": "operations.csv",
                                    "source_row": int(src.rsplit(" ", 1)[1])})
                else:
                    sources.append({"kind": "ledger", "entry_id": src})
            add(f"driver:{dev.comparison.key}:{fact.key}", fact.value, fact.unit, sources,
                line_key=dev.comparison.key)
    return dict(sorted(figures.items()))


def figures_sha256(figures: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(figures, sort_keys=True).encode()).hexdigest()


# --- Texto -----------------------------------------------------------------------------

_L = {
    "en": {
        "title": "HealthCore — Monthly financial close, {month}", "version": "Version {v} · status: {status} · reviewer: {reviewer}",
        "draft": "Draft pending review. Not for distribution until approved by {reviewer}.",
        "revision": "Reviewer comments addressed in this version",
        "s1": "1. Executive summary", "s2": "2. Consolidated result (USD)", "s3": "3. Revenue by country and payer (local currency)",
        "s4": "4. Expenses by country (local currency)", "s5": "5. Relevant deviations (≥ {threshold})",
        "s6": "6. Missing or doubtful data", "s7": "7. Traceability annex",
        "cons_head": "| | Budget | Comparable budget | Actual | Variance | Variance % |",
        "revenue": "Revenue", "expense": "Expenses", "result": "Result",
        "cons_note": "Comparable budget excludes budgeted lines with no records ({labels}); those lines are declared "
                     "in section 6 and are not treated as zero. Converted at {fx}.",
        "line_head": "| Line | Budget | Actual | Variance | Variance % |", "no_records": "No records",
        "unbudgeted": "not budgeted",
        "no_dev": "No line reaches the {threshold} threshold.", "support": "Supporting figures",
        "missing_h": "Missing data", "missing_line": "- {label}: budget {budget} {cur}, **no records** in ledger.csv. "
                     "Declared as missing; not treated as 0 nor as a −100% deviation.",
        "missing_file": "- Source file not received: `{name}`.", "none": "- None.",
        "doubtful_h": "Doubtful records (excluded from totals)",
        "doubtful_line": "- `{ref}` ({source} row {row}): {issues}.",
        "unbudgeted_h": "Records without a budget line",
        "unbudgeted_line": "- {label}: {amount} {cur} ({ids}).",
        "trace_head": "| Figure | Value | Sources |", "fx": "exchange rate",
        "budget_row": "budget.csv row {row}", "ops_row": "operations.csv row {row}", "calc": "calculation",
        "basis": "Hypotheses are explanations suggested by the data, not established causes.",
    },
    "es": {
        "title": "HealthCore — Cierre financiero mensual, {month}", "version": "Versión {v} · estado: {status} · revisor: {reviewer}",
        "draft": "Borrador pendiente de revisión. No se distribuye hasta que lo apruebe {reviewer}.",
        "revision": "Comentarios del revisor atendidos en esta versión",
        "s1": "1. Resumen ejecutivo", "s2": "2. Resultado consolidado (USD)", "s3": "3. Ingresos por país y pagador (moneda local)",
        "s4": "4. Gastos por país (moneda local)", "s5": "5. Desviaciones relevantes (≥ {threshold})",
        "s6": "6. Datos faltantes o dudosos", "s7": "7. Anexo de trazabilidad",
        "cons_head": "| | Presupuesto | Presupuesto comparable | Real | Desviación | Desviación % |",
        "revenue": "Ingresos", "expense": "Gastos", "result": "Resultado",
        "cons_note": "El presupuesto comparable excluye las partidas presupuestadas sin registros ({labels}); se "
                     "declaran en la sección 6 y no se tratan como cero. Conversión: {fx}.",
        "line_head": "| Partida | Presupuesto | Real | Desviación | Desviación % |", "no_records": "Sin registros",
        "unbudgeted": "sin presupuesto",
        "no_dev": "Ninguna partida alcanza el umbral del {threshold}.", "support": "Cifras que la sostienen",
        "missing_h": "Datos faltantes", "missing_line": "- {label}: presupuesto {budget} {cur}, **sin registros** en "
                     "ledger.csv. Declarado como faltante; no se trata como 0 ni como una desviación del −100 %.",
        "missing_file": "- Archivo de origen no recibido: `{name}`.", "none": "- Ninguno.",
        "doubtful_h": "Registros dudosos (excluidos de los totales)",
        "doubtful_line": "- `{ref}` ({source} fila {row}): {issues}.",
        "unbudgeted_h": "Registros sin partida presupuestada",
        "unbudgeted_line": "- {label}: {amount} {cur} ({ids}).",
        "trace_head": "| Cifra | Valor | Origen |", "fx": "tipo de cambio",
        "budget_row": "budget.csv fila {row}", "ops_row": "operations.csv fila {row}", "calc": "cálculo",
        "basis": "Las hipótesis son explicaciones que sugieren los datos, no causas demostradas.",
    },
}
STATUS_LABELS = {
    "en": {"pending_review": "pending review", "approved": "approved", "rejected": "rejected",
           "distributed": "distributed"},
    "es": {"pending_review": "pendiente de revisión", "approved": "aprobado", "rejected": "rechazado",
           "distributed": "distribuido"},
}


def _value(fig: Dict[str, Any], lang: str) -> str:
    if fig["value"] is None:
        return _L[lang]["no_records"]
    value = Decimal(fig["value"])
    if fig["unit"] == "pct":
        return fmt.pct(value, lang)
    if fig["unit"] == "rate":
        return fmt.pct(value, lang, signed=False)
    if fig["unit"] in ("count",):
        return fmt.count(value, lang)
    if fig["unit"] == "pp":
        return fmt.number(value, lang, 1) + (" pp" if lang == "en" else " p. p.")
    return f"{fmt.money(value, lang)} {fig['unit']}"


def _sources_text(fig: Dict[str, Any], lang: str) -> str:
    L = _L[lang]
    parts, ids = [], []
    for src in fig["sources"]:
        kind = src["kind"]
        if kind == "ledger":
            ids.append(src["entry_id"])
        elif kind == "budget":
            parts.append(L["budget_row"].format(row=src["source_row"]))
        elif kind == "operations":
            parts.append(L["ops_row"].format(row=src["source_row"]))
        elif kind == "fx":
            parts.append(f"{L['fx']} ({fx_text(lang)})")
        else:
            parts.append(L["calc"])
    if ids:
        shown = ", ".join(f"`{i}`" for i in ids)
        parts.insert(0, shown)
    return "; ".join(parts) or "—"


def render(analysis: Analysis, figures: Dict[str, Dict[str, Any]], *, lang: str, version: int, status: str) -> str:
    L = _L[lang]
    reviewer = PEOPLE[REVIEWER_ROLE]
    threshold = fmt.threshold(DEVIATION_THRESHOLD_PCT, lang)
    out: List[str] = [f"# {L['title'].format(month=month_label(analysis.period, lang))}", "",
                      f"_{L['version'].format(v=version, status=STATUS_LABELS[lang][status], reviewer=reviewer)}_", ""]
    if status == "pending_review":
        out += [f"> {L['draft'].format(reviewer=reviewer)}", ""]
    if analysis.comments:
        out += [f"## {L['revision']}", "", (analysis.narrative.revision_note or {}).get(lang, ""), ""]
        out += [f"> {line}" for line in analysis.comments.splitlines() if line.strip()] + [""]

    # 1
    out += [f"## {L['s1']}", ""] + [f"- {line}" for line in analysis.narrative.summary[lang]] + [""]

    # 2
    out += [f"## {L['s2']}", "", L["cons_head"], "|---|---:|---:|---:|---:|---:|"]
    for kind in ("revenue", "expense", "result"):
        line = analysis.consolidated[kind]
        mark = " *" if line.incomplete else ""
        out.append(f"| {L[kind]}{mark} | {fmt.money(line.budget, lang)} | {fmt.money(line.comparable_budget, lang)} | "
                   f"{fmt.money(line.actual, lang)} | {fmt.money(line.variance, lang)} | "
                   f"{fmt.pct(pct_display(line.variance_pct), lang)} |")
    missing = [m for m in analysis.quality["missing_lines"]]
    if missing:
        labels = ", ".join(line_label(m["line_key"], lang) for m in missing)
        out += ["", f"\\* {L['cons_note'].format(labels=labels, fx=fx_text(lang))}"]
    out.append("")

    # 3 y 4
    for section, kind in (("s3", "revenue"), ("s4", "expense")):
        out += [f"## {L[section]}", ""]
        for country in COUNTRIES:
            rows = [c for c in analysis.comparisons if c.kind == kind and c.key.startswith(country + ".")]
            if not rows:
                continue
            out += [f"**{COUNTRY_LABELS[lang][country]} ({rows[0].currency})**", "", L["line_head"],
                    "|---|---:|---:|---:|---:|"]
            for c in rows:
                actual = L["no_records"] if c.status == STATUS_MISSING else fmt.money(c.actual, lang)
                label = line_label(c.key, lang)
                if c.status == STATUS_UNBUDGETED:
                    label += f" ({L['unbudgeted']})"
                out.append(f"| {label} | {fmt.money(c.budget, lang)} | {actual} | {fmt.money(c.variance, lang)} | "
                           f"{fmt.pct(c.variance_pct_display, lang)} |")
            out.append("")

    # 5
    out += [f"## {L['s5'].format(threshold=threshold)}", "", f"_{L['basis']}_", ""]
    if not analysis.deviations:
        out += [L["no_dev"].format(threshold=threshold), ""]
    for dev in analysis.deviations:
        key = dev.comparison.key
        out += [f"### {line_label(key, lang)} — {fmt.pct(dev.comparison.variance_pct_display, lang)}", "",
                (PREFIX if dev.driver is not None and dev.driver.status == "ok" else UNEXPLAINED_PREFIX)[lang]
                + analysis.narrative.hypotheses[key][lang], ""]
        support = [fid for fid in figures if fid.startswith(f"driver:{key}:")] + \
                  [f"line:{key}:budget", f"line:{key}:actual", f"line:{key}:variance_pct"]
        out.append(f"{L['support']}:")
        for fid in support:
            fig = figures[fid]
            metric = metric_label(fid.split(":")[-1], lang)
            out.append(f"- `{fid}` ({metric}): {_value(fig, lang)} — {_sources_text(fig, lang)}")
        out.append("")

    # 6
    q = analysis.quality
    out += [f"## {L['s6']}", "", f"**{L['missing_h']}**", ""]
    lines = [L["missing_line"].format(label=line_label(m["line_key"], lang), budget=fmt.money(Decimal(m["budget"]), lang),
                                      cur=m["currency"]) for m in q["missing_lines"]]
    lines += [L["missing_file"].format(name=name) for name in q["missing_files"]]
    for dev in analysis.deviations:
        if dev.driver is not None and dev.driver.status != "ok":
            names = ", ".join(dev.driver.missing)
            lines.append(f"- {line_label(dev.comparison.key, lang)}: operations.csv — {names}.")
    out += (lines or [L["none"]]) + ["", f"**{L['doubtful_h']}**", ""]
    doubtful = []
    for d in q["doubtful_records"]:
        issues = "; ".join(ISSUE_LABELS[lang].get(i, ISSUE_LABELS[lang].get(i.split(":")[0], i)) for i in d["issues"])
        ref = d["entry_id"] or f"{d['source']}#{d['source_row']}"
        doubtful.append(L["doubtful_line"].format(ref=ref, source=d["source"], row=d["source_row"], issues=issues))
    out += (doubtful or [L["none"]]) + [""]
    if q["unbudgeted_lines"]:
        out += [f"**{L['unbudgeted_h']}**", ""]
        out += [L["unbudgeted_line"].format(label=line_label(u["line_key"], lang),
                                            amount=fmt.money(Decimal(u["actual"]), lang), cur=u["currency"],
                                            ids=", ".join(u["entry_ids"])) for u in q["unbudgeted_lines"]] + [""]

    # 7
    out += [f"## {L['s7']}", "", L["trace_head"], "|---|---:|---|"]
    for fid, fig in figures.items():
        out.append(f"| `{fid}` | {_value(fig, lang)} | {_sources_text(fig, lang)} |")
    out.append("")
    return "\n".join(out)
