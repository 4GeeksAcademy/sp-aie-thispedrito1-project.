"""Evaluación técnica del modelo de predicción de ingresos de HealthCore.

Uso (desde la raíz del repo, con el venv de la API):

    services/api/.venv/bin/python scripts/evaluate_sales_forecast.py
    services/api/.venv/bin/python scripts/evaluate_sales_forecast.py --output-dir /tmp/eval

Pasos:
1. Carga data/raw/healthcore_sales.csv y separa 2016-2023 / 2024-2025, con
   las mismas funciones que scripts/train_sales_forecast.py.
2. Validación cruzada temporal (TimeSeriesSplit, 5 pliegues) SOLO sobre
   2016-2023: MAE y RMSE de entrenamiento y validación, media ± desviación.
3. Curva de aprendizaje con ventanas móviles de 24 a 84 meses.
4. Diagnóstico: bien ajustado / underfitting / overfitting.
5. Repite 2-4 con los dos modelos de comparación, malos a propósito
   (data/process/sales_forecast_comparison.py), para mostrar cómo se ven de
   verdad un underfitting y un overfitting con estos datos.
6. Escribe en --output-dir: learning_curve.png, fit_diagnosis_map.png y
   sales_forecast_evaluation.json.

El reporte razonado está en data/eval/evaluation_report.md.
Solo cifras agregadas mensuales: ningún dato de pacientes (CONTEXT, sección 1).
Códigos de salida: 0 correcto; 1 datos inválidos o error al escribir.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import matplotlib  # noqa: E402

matplotlib.use("Agg")  # sin ventana: el script también corre en terminal/CI

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import patheffects  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from data.process.sales_forecast import (  # noqa: E402
    SalesDataError,
    load_sales_data,
    split_train_test,
)
from data.process.sales_forecast_comparison import COMPARISON_MODELS  # noqa: E402
from data.process.sales_forecast_evaluation import (  # noqa: E402
    CURRENT_MODEL,
    OVERFIT_GAP_RATIO,
    PRIMARY_METRIC,
    cross_validate_forecast,
    diagnose_cross_validation,
    learning_curve,
)

DEFAULT_DATA_PATH = ROOT_DIR / "data" / "raw" / "healthcore_sales.csv"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "data" / "eval"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def _series(curve: list, split: str, metric: str):
    """Medias y desviaciones de una métrica a lo largo de la curva.

    Un punto con una sola ventana tiene std None: se dibuja sin franja.
    """
    means = np.array([point["summary"][split][metric]["mean"] for point in curve])
    stds = np.array(
        [point["summary"][split][metric]["std"] or 0.0 for point in curve]
    )
    return means, stds


def plot_learning_curve(curve: list, diagnosis: str, path: Path) -> None:
    sizes = [point["train_months"] for point in curve]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2), sharey=True)

    panels = (
        ("rmse_pct", "RMSE (métrica principal)"),
        ("mae_pct", "MAE"),
    )
    for axis, (metric, title) in zip(axes, panels):
        for split, label, color in (
            ("train", "Entrenamiento", "#2563eb"),
            ("validation", "Validación (12 meses siguientes)", "#ea580c"),
        ):
            means, stds = _series(curve, split, metric)
            axis.plot(sizes, means, marker="o", color=color, lw=2, label=label)
            axis.fill_between(sizes, means - stds, means + stds, color=color, alpha=0.15)

        baseline, _ = _series(curve, "seasonal_naive_validation", metric)
        axis.plot(
            sizes, baseline, color="#6b7280", ls="--", lw=1.4,
            label="Referencia: mismo mes del año anterior",
        )
        axis.set_title(title)
        axis.set_xlabel("Meses de historia usados para entrenar")
        axis.set_xticks(sizes)
        axis.grid(alpha=0.3)

    axes[0].set_ylabel("Error, % del ingreso mensual medio")
    axes[0].set_ylim(bottom=0)
    axes[0].legend(loc="upper right", fontsize=9)
    windows = ", ".join(f"{point['train_months']}m×{point['windows']}" for point in curve)
    fig.suptitle(
        f"Curva de aprendizaje del modelo de ingresos (2016-2023) | diagnóstico: {diagnosis}\n"
        f"Franja = ± 1 desviación entre ventanas móviles ({windows})",
        fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _mean_std(stat: dict, decimals: int = 2) -> str:
    std = "n/d" if stat["std"] is None else f"{stat['std']:.{decimals}f}"
    return f"{stat['mean']:.{decimals}f} ± {std}"


def print_report(cv: dict, curve: list, diagnosis: str) -> None:
    print(f"Validación cruzada: {cv['strategy']} sobre 2016-2023")
    print(f"{'Pliegue':<8}{'Entrena':<20}{'Valida':<20}{'RMSE% tr':>9}{'RMSE% va':>9}{'MAE% tr':>9}{'MAE% va':>9}{'Sesgo% va':>10}{'Ingenua%':>9}")
    for fold in cv["folds"]:
        print(
            f"{fold['fold']:<8}{fold['train_period']:<20}{fold['validation_period']:<20}"
            f"{fold['train']['rmse_pct']:>9.2f}{fold['validation']['rmse_pct']:>9.2f}"
            f"{fold['train']['mae_pct']:>9.2f}{fold['validation']['mae_pct']:>9.2f}"
            f"{fold['validation']['bias_pct']:>+10.2f}{fold['seasonal_naive_validation']['rmse_pct']:>9.2f}"
        )

    summary = cv["summary"]
    print("\nMedia ± desviación estándar entre pliegues (% del ingreso mensual medio | USD)")
    for split, label in (("train", "Entrenamiento"), ("validation", "Validación")):
        print(
            f"  {label:<14} RMSE {_mean_std(summary[split]['rmse_pct'])} % | {_mean_std(summary[split]['rmse_usd'], 0)} USD"
            f"   MAE {_mean_std(summary[split]['mae_pct'])} % | {_mean_std(summary[split]['mae_usd'], 0)} USD"
        )
    print(f"  {'Ref. ingenua':<14} RMSE {_mean_std(summary['seasonal_naive_validation']['rmse_pct'])} %")

    print("\nCurva de aprendizaje (RMSE %, media ± desviación entre ventanas)")
    for point in curve:
        s = point["summary"]
        print(
            f"  {point['train_months']:>2} meses ({point['windows']} ventanas): "
            f"entrenamiento {_mean_std(s['train']['rmse_pct'])} | validación {_mean_std(s['validation']['rmse_pct'])}"
        )

    print(f"\nMétrica principal: {PRIMARY_METRIC}")
    print(f"Diagnóstico: {diagnosis}")


# Mismos colores que la paleta validada de las gráficas del proyecto: azul y
# naranja para las dos series; verde, amarillo y salmón solo para las zonas
# del diagnóstico, siempre con su nombre escrito.
INK = "#121412"
INK_2 = "#4b4e49"
MUTED = "#7d8079"
TRAIN_COLOR = "#2a78d6"
VALIDATION_COLOR = "#eb6834"
ZONE_COLORS = {"bien ajustado": "#0ca30c", "overfitting": "#fab219", "underfitting": "#ec835a"}
MARKERS = {"Nuestro modelo": "o", "Solo tendencia": "s", "Bosque que memoriza": "^"}
HALO = [patheffects.withStroke(linewidth=4, foreground="white")]


def _es(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def evaluate_forecaster(train, forecaster) -> dict:
    cv = cross_validate_forecast(train, forecaster)
    return {
        "name": forecaster.name,
        "diagnosis": diagnose_cross_validation(cv),
        "cross_validation": cv,
        "learning_curve": learning_curve(train, forecaster=forecaster),
    }


def plot_fit_diagnosis_map(evaluations: list, path: Path) -> None:
    """Arriba: mapa de diagnóstico con las tres zonas. Abajo: las tres curvas."""
    baseline = evaluations[0]["cross_validation"]["summary"]["seasonal_naive_validation"]["rmse_pct"]["mean"]
    limit = 12.0
    kink = baseline / OVERFIT_GAP_RATIO  # donde la línea 1,5× alcanza la referencia

    fig = plt.figure(figsize=(13, 12.5))
    grid = fig.add_gridspec(2, 3, height_ratios=[1.45, 1], hspace=0.34, wspace=0.14)
    ax = fig.add_subplot(grid[0, :])

    ax.fill([baseline, limit, limit, baseline], [0, 0, limit, limit], color=ZONE_COLORS["underfitting"], alpha=0.16, lw=0)
    ax.fill([0, baseline, baseline, kink], [0, 0, baseline, baseline], color=ZONE_COLORS["bien ajustado"], alpha=0.16, lw=0)
    ax.fill([0, kink, baseline, baseline, 0], [0, baseline, baseline, limit, limit], color=ZONE_COLORS["overfitting"], alpha=0.2, lw=0)
    ax.plot([0, limit], [0, limit], color=MUTED, lw=1, ls=(0, (1, 3)))
    ax.plot([0, kink, baseline], [0, baseline, baseline], color=INK_2, lw=1.5)
    ax.axvline(baseline, color=INK_2, lw=1.5)

    line_text = dict(fontsize=9, color=MUTED, rotation_mode="anchor", transform_rotates_text=True, path_effects=HALO)
    ax.text(0.3, 0.45 + 0.25, f"{OVERFIT_GAP_RATIO:g} × entrenamiento".replace(".", ","), rotation=np.degrees(np.arctan(OVERFIT_GAP_RATIO)), **line_text)
    ax.text(6.6, 6.6 - 0.45, "validación = entrenamiento", rotation=45, **line_text)
    ax.text(baseline + 0.12, 0.3, f"{_es(baseline)} % referencia ingenua", fontsize=9, color=MUTED, path_effects=HALO)

    zone_text = dict(fontsize=14, fontweight="bold", color=INK, path_effects=HALO)
    ax.text(0.3, 11.2, "OVERFITTING", **zone_text)
    ax.text(baseline + 0.3, 11.2, "UNDERFITTING", **zone_text)
    ax.text(baseline - 0.2, 0.3, "BIEN AJUSTADO", ha="right", **zone_text)

    label_offsets = {"Nuestro modelo": (14, 2, "left"), "Bosque que memoriza": (14, 2, "left"), "Solo tendencia": (-16, -34, "right")}
    for evaluation in evaluations:
        name = evaluation["name"]
        cv = evaluation["cross_validation"]
        for fold in cv["folds"]:
            ax.plot(fold["train"]["rmse_pct"], fold["validation"]["rmse_pct"], "o", ms=5, color=INK_2, alpha=0.45, mec="white", mew=0.8, zorder=3)
        summary = cv["summary"]
        x, x_std = summary["train"]["rmse_pct"]["mean"], summary["train"]["rmse_pct"]["std"]
        y, y_std = summary["validation"]["rmse_pct"]["mean"], summary["validation"]["rmse_pct"]["std"]
        ax.errorbar(x, y, xerr=x_std, yerr=y_std, fmt=MARKERS[name], ms=11, color=INK, mec="white", mew=2, ecolor=INK, elinewidth=1.4, capsize=4, zorder=4)
        dx, dy, align = label_offsets[name]
        ax.annotate(
            f"{name} · {evaluation['diagnosis']}\n{_es(x)} % → {_es(y)} %",
            (x, y), xytext=(dx, dy), textcoords="offset points", ha=align, va="center",
            fontsize=10.5, color=INK, fontweight="bold", path_effects=HALO, zorder=5,
        )

    ax.set_xlim(0, limit)
    ax.set_ylim(0, limit)
    ax.set_xticks(range(0, 13, 2))
    ax.set_yticks(range(0, 13, 2))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f} %"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f} %"))
    ax.set_xlabel("Error de entrenamiento: meses que ya vio (RMSE % del ingreso mensual medio)")
    ax.set_ylabel("Error de validación: año siguiente")
    ax.set_title(
        "Mapa de diagnóstico · media de los 5 años de validación (± 1 desviación); puntos pequeños = cada año",
        loc="left", fontsize=11, color=INK_2,
    )
    ax.grid(alpha=0.25)

    sizes = [point["train_months"] for point in evaluations[0]["learning_curve"]]
    for column, evaluation in enumerate(evaluations):
        panel = fig.add_subplot(grid[1, column], sharey=fig.axes[1] if column else None)
        curve = evaluation["learning_curve"]
        for split, color, label in (("validation", VALIDATION_COLOR, "Validación (12 meses siguientes)"), ("train", TRAIN_COLOR, "Entrenamiento")):
            means, stds = _series(curve, split, "rmse_pct")
            panel.fill_between(sizes, means - stds, means + stds, color=color, alpha=0.16, lw=0)
            panel.plot(sizes, means, color=color, lw=2, marker="o", ms=4, label=label)
        baseline_curve, _ = _series(curve, "seasonal_naive_validation", "rmse_pct")
        panel.plot(sizes, baseline_curve, color=MUTED, lw=1.3, ls="--", label="Referencia: mismo mes del año anterior")
        panel.set_ylim(0, limit)
        panel.set_xticks(sizes)
        panel.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f} %"))
        panel.set_xlabel("Meses de historia usados para entrenar")
        panel.grid(alpha=0.25)
        panel.set_title(evaluation["name"], loc="left", fontsize=12, fontweight="bold", color=INK)
        panel.text(
            1.0, 1.035, evaluation["diagnosis"], transform=panel.transAxes, ha="right", va="bottom", fontsize=10, color=INK,
            bbox=dict(boxstyle="round,pad=0.3", facecolor=ZONE_COLORS[evaluation["diagnosis"]], alpha=0.35, lw=0),
        )
        if column:
            panel.tick_params(labelleft=False)
        else:
            panel.set_ylabel("RMSE % del ingreso mensual medio")

    handles, labels = fig.axes[1].get_legend_handles_labels()  # dibujadas: validación, entrenamiento, referencia
    order = [1, 0, 2]
    fig.legend([handles[i] for i in order], [labels[i] for i in order], loc="lower center", ncol=3, frameon=False, fontsize=10)
    fig.suptitle(
        "Cómo se ven los tres casos con los datos de HealthCore (2016-2023)\n"
        "Bien ajustado: juntas y abajo · Underfitting: juntas pero arriba · Overfitting: separadas",
        fontsize=13, x=0.06, ha="left",
    )
    fig.subplots_adjust(left=0.06, right=0.98, top=0.92, bottom=0.08)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> int:
    args = parse_args()
    try:
        sales = load_sales_data(args.data)
        train, _test = split_train_test(sales)  # la prueba no se toca aquí
        evaluations = [evaluate_forecaster(train, forecaster) for forecaster in (CURRENT_MODEL,) + COMPARISON_MODELS]
    except (OSError, SalesDataError) as error:
        print(f"Error con los datos de ventas: {error}", file=sys.stderr)
        return 1

    current = evaluations[0]
    cv, curve, diagnosis = current["cross_validation"], current["learning_curve"], current["diagnosis"]

    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        plot_learning_curve(curve, diagnosis, args.output_dir / "learning_curve.png")
        plot_fit_diagnosis_map(evaluations, args.output_dir / "fit_diagnosis_map.png")
        payload = {
            "primary_metric": PRIMARY_METRIC,
            "diagnosis": diagnosis,
            "cross_validation": cv,
            "learning_curve": curve,
            "comparison_models": evaluations[1:],
        }
        (args.output_dir / "sales_forecast_evaluation.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError as error:
        print(f"No se pudieron escribir los resultados en {args.output_dir}: {error}", file=sys.stderr)
        return 1

    print_report(cv, curve, diagnosis)
    print("\nModelos de comparación (malos a propósito), RMSE % media ± desviación entre pliegues")
    for evaluation in evaluations[1:]:
        summary = evaluation["cross_validation"]["summary"]
        print(
            f"  {evaluation['name']:<20} entrenamiento {_mean_std(summary['train']['rmse_pct'])} | "
            f"validación {_mean_std(summary['validation']['rmse_pct'])} -> {evaluation['diagnosis']}"
        )
    print(f"\nResultados en {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
