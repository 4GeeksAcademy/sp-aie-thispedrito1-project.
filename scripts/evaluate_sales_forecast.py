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
5. Escribe en --output-dir: learning_curve.png y sales_forecast_evaluation.json.

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

from data.process.sales_forecast import (  # noqa: E402
    SalesDataError,
    load_sales_data,
    split_train_test,
)
from data.process.sales_forecast_evaluation import (  # noqa: E402
    PRIMARY_METRIC,
    cross_validate_forecast,
    diagnose_fit,
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


def main() -> int:
    args = parse_args()
    try:
        sales = load_sales_data(args.data)
        train, _test = split_train_test(sales)  # la prueba no se toca aquí
        cv = cross_validate_forecast(train)
        curve = learning_curve(train)
    except (OSError, SalesDataError) as error:
        print(f"Error con los datos de ventas: {error}", file=sys.stderr)
        return 1

    summary = cv["summary"]
    diagnosis = diagnose_fit(
        train_rmse_pct=summary["train"]["rmse_pct"]["mean"],
        validation_rmse_pct=summary["validation"]["rmse_pct"]["mean"],
        baseline_rmse_pct=summary["seasonal_naive_validation"]["rmse_pct"]["mean"],
    )

    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        plot_learning_curve(curve, diagnosis, args.output_dir / "learning_curve.png")
        payload = {
            "primary_metric": PRIMARY_METRIC,
            "diagnosis": diagnosis,
            "cross_validation": cv,
            "learning_curve": curve,
        }
        (args.output_dir / "sales_forecast_evaluation.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError as error:
        print(f"No se pudieron escribir los resultados en {args.output_dir}: {error}", file=sys.stderr)
        return 1

    print_report(cv, curve, diagnosis)
    print(f"\nResultados en {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
