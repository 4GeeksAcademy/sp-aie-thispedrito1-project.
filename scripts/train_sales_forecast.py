"""Entrena y evalúa el modelo de predicción de ingresos de HealthCore.

Uso (desde la raíz del repo, con el venv de la API):

    services/api/.venv/bin/python scripts/train_sales_forecast.py
    services/api/.venv/bin/python scripts/train_sales_forecast.py --output-dir /tmp/forecast

Pasos:
1. Carga data/raw/healthcore_sales.csv (fila `consolidated`), trata nulos y
   valida las restricciones del CONTEXT.
2. Divide 2016-2023 (entrenamiento) / 2024-2025 (prueba).
3. Entrena tendencia + Random Forest solo con entrenamiento.
4. Calcula MSE, PSI, Gini y K2 Score (R²) sobre la prueba.
5. Escribe en --output-dir: forecast.png, metrics.json y test_predictions.csv.

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
import pandas as pd  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from data.process.sales_forecast import (  # noqa: E402
    SalesDataError,
    evaluate_on_test,
    fit_forecast_model,
    load_sales_data,
    predict,
    split_train_test,
)

DEFAULT_DATA_PATH = ROOT_DIR / "data" / "raw" / "healthcore_sales.csv"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "docs" / "sales-forecast"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def plot_forecast(
    history: pd.DataFrame,
    train_fit: pd.DataFrame,
    predictions: pd.DataFrame,
    metrics: dict,
    path: Path,
) -> None:
    """Arriba: contexto de los 10 años. Abajo: zoom de la prueba con la franja."""
    millions = FuncFormatter(lambda value, _: f"{value / 1e6:.1f} M")
    band = metrics["variability_band"]
    band_label = (
        f"Rango de variabilidad {band['nominal_coverage_pct']:.0f} % "
        f"(cubrió {band['months_inside']}/{len(predictions)} meses reales)"
    )
    split_month = predictions["month"].min()

    fig, (top, bottom) = plt.subplots(2, 1, figsize=(12, 9), height_ratios=[1, 1.2])

    top.plot(history["month"], history["revenue_usd"], color="#6b7280", lw=1.4, label="Ingreso real")
    top.plot(train_fit["month"], train_fit["trend_usd"], color="#2563eb", lw=1, ls=":", label="Tendencia aprendida (2016-2023)")
    top.plot(predictions["month"], predictions["predicted_usd"], color="#ea580c", lw=1.8, label="Predicción 2024-2025")
    top.fill_between(predictions["month"], predictions["lower_usd"], predictions["upper_usd"], color="#ea580c", alpha=0.18)
    top.axvline(split_month, color="#111827", lw=1, ls="--")
    top.text(split_month, 0.96, "  inicio de la prueba", transform=top.get_xaxis_transform(), va="top", fontsize=9)
    top.set_title("Ingresos mensuales consolidados de HealthCore: entrenamiento 2016-2023, prueba 2024-2025")
    top.yaxis.set_major_formatter(millions)
    top.legend(loc="upper left", fontsize=9)
    top.grid(alpha=0.3)

    bottom.fill_between(predictions["month"], predictions["lower_usd"], predictions["upper_usd"], color="#ea580c", alpha=0.18, label=band_label)
    bottom.plot(predictions["month"], predictions["predicted_usd"], color="#ea580c", lw=2, marker="o", ms=4, label="Predicción del modelo")
    bottom.plot(predictions["month"], predictions["actual_usd"], color="#111827", lw=2, marker="s", ms=4, label="Ingreso real (no visto al entrenar)")
    mse = metrics["mse"]
    bottom.set_title(
        "Prueba 2024-2025 | "
        f"error típico (RMSE) {mse['rmse_pct_of_mean_monthly_revenue']:.1f} % del ingreso mensual medio | "
        f"R² {metrics['k2_score_r2']:.2f} | Gini {metrics['gini_normalized']:.2f}"
    )
    bottom.yaxis.set_major_formatter(millions)
    bottom.set_ylabel("USD")
    bottom.legend(loc="upper left", fontsize=9)
    bottom.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def print_report(metrics: dict) -> None:
    mse = metrics["mse"]
    psi = metrics["psi"]
    band = metrics["variability_band"]
    periods = metrics["periods"]
    print(f"Entrenamiento: {periods['train']} ({periods['train_rows']} meses)")
    print(f"Prueba:        {periods['test']} ({periods['test_rows']} meses)")
    print(f"Crecimiento anual aprendido: {metrics['implied_annual_growth_pct']:.2f} %")
    print("\nMétricas sobre la PRUEBA")
    print(f"  MSE:  {mse['mse_usd2']:,.0f} USD²")
    print(f"        RMSE {mse['rmse_usd']:,.0f} USD = {mse['rmse_pct_of_mean_monthly_revenue']:.2f} % del ingreso mensual medio ({mse['mean_monthly_revenue_usd']:,.0f} USD)")
    print(f"        error medio absoluto {mse['mae_usd']:,.0f} USD ({mse['mape_pct']:.2f} %)")
    print(f"  K2 Score (R²): {metrics['k2_score_r2']:.3f}")
    print(f"  Gini normalizado: {metrics['gini_normalized']:.3f}")
    print(
        "  PSI mezcla (ingreso por consulta, entrenamiento vs prueba): "
        f"{psi['avg_revenue_per_visit_train_vs_test']['value']:.3f} ({psi['avg_revenue_per_visit_train_vs_test']['label']})"
    )
    print(
        "  PSI predicción vs real (prueba): "
        f"{psi['predicted_vs_actual_test']['value']:.3f} ({psi['predicted_vs_actual_test']['label']})"
    )
    print(
        f"  Franja {band['nominal_coverage_pct']:.0f} % ({band['lower_error_pct']:+.1f} % / {band['upper_error_pct']:+.1f} %): "
        f"cubrió {band['months_inside']} meses reales ({band['observed_coverage_pct']:.0f} %)"
    )


def main() -> int:
    args = parse_args()
    try:
        sales = load_sales_data(args.data)
        train, test = split_train_test(sales)
        model = fit_forecast_model(train)
        metrics, predictions = evaluate_on_test(model, train, test)
    except (OSError, SalesDataError) as error:
        print(f"Error con los datos de ventas: {error}", file=sys.stderr)
        return 1

    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        plot_forecast(sales, predict(model, train), predictions, metrics, args.output_dir / "forecast.png")
        (args.output_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        predictions.assign(month=predictions["month"].dt.strftime("%Y-%m-%d")).round(2).to_csv(
            args.output_dir / "test_predictions.csv", index=False
        )
    except OSError as error:
        print(f"No se pudieron escribir los resultados en {args.output_dir}: {error}", file=sys.stderr)
        return 1

    print_report(metrics)
    print(f"\nResultados en {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
