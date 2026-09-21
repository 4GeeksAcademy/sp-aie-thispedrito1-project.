"""Modelo de predicción de ingresos mensuales de HealthCore (regresión).

Lógica pura, sin gráficos ni escritura de archivos, para poder testearla:
el script scripts/train_sales_forecast.py la orquesta y dibuja el resultado.
Sigue el CONTEXT de la clase "Predicción de Ventas con un Modelo de
Regresión" (data/raw/healthcore_sales.csv, target `revenue_usd` de la fila
`consolidated`, 2016-01 a 2025-12).

Diseño del modelo (justificación completa en docs/sales-forecast.md):

    ingreso_mes = tendencia(mes) × patrón_estacional(mes_del_año)

- La tendencia es una regresión lineal sobre log(ingreso): captura el
  crecimiento anual (~4 %). Hace falta porque un Random Forest no sabe
  predecir por encima del máximo que vio al entrenar, y 2024-2025 superan
  a todo 2016-2023.
- El patrón estacional lo aprende el Random Forest a partir del mes del año
  (alza oct-dic, caída jul-ago).
- La franja de variabilidad sale de los errores out-of-bag del bosque, que
  se calculan solo con meses de entrenamiento.

`visits_count` y `avg_revenue_per_visit_usd` NO son variables del modelo:
en este dataset ingreso = visitas × ingreso medio exactamente, y las visitas
de un mes futuro no se conocen al predecir. Usarlas sería fuga de datos.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple, Union

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

EXPECTED_COLUMNS = (
    "month",
    "revenue_usd",
    "visits_count",
    "avg_revenue_per_visit_usd",
    "region",
)
TARGET_COLUMN = "revenue_usd"
PRIMARY_REGION = "consolidated"

TRAIN_YEARS = 8
TEST_YEARS = 2
RANDOM_STATE = 42

TREND_FEATURES = ["years_since_start"]
SEASONAL_FEATURES = ["month_of_year"]
N_ESTIMATORS = 500
# Franja del 80 %: entre el percentil 10 y el 90 de los errores out-of-bag.
BAND_QUANTILES = (0.10, 0.90)

# Con 24 meses de prueba, 10 cubetas dejarían ~2 meses por cubeta y el PSI
# sería puro ruido. 5 cubetas por cuantiles dejan ~5 por cubeta.
PSI_BINS = 5
PSI_STABLE_BELOW = 0.10
PSI_SIGNIFICANT_FROM = 0.25


class SalesDataError(ValueError):
    """El dataset no cumple el formato o las restricciones del CONTEXT."""


# ---------------------------------------------------------------------------
# Carga, limpieza y validación
# ---------------------------------------------------------------------------


def load_sales_data(path: Union[str, Path]) -> pd.DataFrame:
    """Lee el CSV, se queda con la fila `consolidated`, limpia y valida."""
    raw = pd.read_csv(path)
    missing = [column for column in EXPECTED_COLUMNS if column not in raw.columns]
    if missing:
        raise SalesDataError(
            f"Faltan columnas del CONTEXT en {path}: {', '.join(missing)}"
        )

    consolidated = raw.loc[raw["region"] == PRIMARY_REGION, list(EXPECTED_COLUMNS)]
    if consolidated.empty:
        raise SalesDataError(f"No hay filas con region='{PRIMARY_REGION}' en {path}")

    cleaned = clean_sales_data(consolidated)
    validate_sales_data(cleaned)
    return cleaned


def clean_sales_data(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza tipos y trata los valores nulos o vacíos.

    - `month` y los numéricos se convierten con `errors="coerce"`: un valor
      ilegible pasa a nulo y recibe el mismo tratamiento que uno vacío.
    - Un `revenue_usd` nulo se reconstruye como visits_count ×
      avg_revenue_per_visit_usd cuando ambos existen. No es una estimación:
      en este dataset la identidad se cumple exactamente.
    - Una fila sin mes o con un ingreso imposible de reconstruir se descarta.
      No se interpola: si eso deja un hueco, validate_sales_data lo rechaza
      en vez de entrenar con un mes inventado.
    """
    out = df.copy()
    out["month"] = pd.to_datetime(out["month"], format="%Y-%m-%d", errors="coerce")
    for column in ("revenue_usd", "visits_count", "avg_revenue_per_visit_usd"):
        out[column] = pd.to_numeric(out[column], errors="coerce")

    reconstructed = out["visits_count"] * out["avg_revenue_per_visit_usd"]
    out[TARGET_COLUMN] = out[TARGET_COLUMN].fillna(reconstructed.round(2))

    out = out.dropna(subset=["month", TARGET_COLUMN])
    return out.sort_values("month").reset_index(drop=True)


def validate_sales_data(df: pd.DataFrame) -> None:
    """Restricciones de negocio de la sección 5 del CONTEXT."""
    if df["month"].duplicated().any():
        repeated = df.loc[df["month"].duplicated(), "month"].dt.strftime("%Y-%m")
        raise SalesDataError(f"Meses repetidos: {', '.join(repeated)}")

    if (df["month"].dt.day != 1).any():
        raise SalesDataError("Todas las fechas de `month` deben ser el día 1 del mes")

    if (df[TARGET_COLUMN] <= 0).any():
        raise SalesDataError("Todos los valores de revenue_usd deben ser positivos")

    expected = pd.date_range(df["month"].min(), df["month"].max(), freq="MS")
    missing = expected.difference(df["month"])
    if len(missing) > 0:
        raise SalesDataError(
            "Faltan meses en el histórico: "
            + ", ".join(missing.strftime("%Y-%m"))
        )


# ---------------------------------------------------------------------------
# Split temporal 8 años / 2 años
# ---------------------------------------------------------------------------


def split_train_test(
    df: pd.DataFrame,
    train_years: int = TRAIN_YEARS,
    test_years: int = TEST_YEARS,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Primeros `train_years` años para entrenar, últimos `test_years` para probar.

    El corte es por año natural y en orden cronológico, nunca aleatorio: un
    split aleatorio mezclaría meses de 2025 en el entrenamiento y el modelo
    "vería el futuro".
    """
    years = sorted(df["month"].dt.year.unique())
    if len(years) != train_years + test_years:
        raise SalesDataError(
            f"Se esperaban {train_years + test_years} años de datos y hay "
            f"{len(years)} ({years[0]}-{years[-1]})"
        )

    # Último año de entrenamiento: la posición train_years - 1 (la lista
    # empieza en 0), es decir 2023. Todo lo anterior o igual entrena; lo
    # posterior se reserva para la prueba.
    cutoff_year = years[train_years - 1]
    row_years = df["month"].dt.year
    train = df[row_years <= cutoff_year].sort_values("month")
    test = df[row_years > cutoff_year].sort_values("month")

    return train.reset_index(drop=True), test.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------


def build_features(df: pd.DataFrame, origin: pd.Timestamp) -> pd.DataFrame:
    """Variables del modelo, calculadas solo a partir de la fecha.

    `origin` es el primer mes del ENTRENAMIENTO y se pasa explícitamente, para
    que el conjunto de prueba se mida con la misma regla que el entrenamiento
    (2024-01 vale 8.0 años) y no empiece de cero.
    """
    months = df["month"]
    elapsed_months = (months.dt.year - origin.year) * 12 + (months.dt.month - origin.month)
    return pd.DataFrame(
        {
            "years_since_start": elapsed_months / 12.0,
            "month_of_year": months.dt.month,
        },
        index=df.index,
    )


@dataclass
class SalesForecastModel:
    origin: pd.Timestamp
    trend: Pipeline
    forest: RandomForestRegressor
    band_lower_ratio: float
    band_upper_ratio: float


def fit_forecast_model(train: pd.DataFrame) -> SalesForecastModel:
    """Entrena tendencia + Random Forest usando ÚNICAMENTE `train`."""
    origin = train["month"].min()
    features = build_features(train, origin)

    # Escalado: el ingreso de 2016 (~2,4 M) y el de 2023 (~3,2 M) no son
    # comparables entre sí, así que el bosque no aprende sobre USD sino sobre
    # el ingreso dividido por su tendencia (≈ 0,83-1,20, sin unidades). El
    # índice temporal pasa además por StandardScaler; para mínimos cuadrados no
    # cambia la recta, pero deja el ajuste en valores centrados. Ambos se
    # ajustan solo con entrenamiento. El mes del año no se escala: los árboles
    # comparan umbrales y no les afecta la magnitud.
    trend = make_pipeline(StandardScaler(), LinearRegression())
    trend.fit(features[TREND_FEATURES], np.log(train[TARGET_COLUMN]))
    trend_values = np.exp(trend.predict(features[TREND_FEATURES]))
    seasonal_ratio = train[TARGET_COLUMN].to_numpy() / trend_values

    forest = RandomForestRegressor(
        n_estimators=N_ESTIMATORS,
        random_state=RANDOM_STATE,
        oob_score=True,
        n_jobs=1,
    )
    forest.fit(features[SEASONAL_FEATURES], seasonal_ratio)

    # Error relativo "de examen" de cada mes de entrenamiento: lo predicen
    # solo los árboles que no lo vieron. La dispersión entre árboles, en
    # cambio, mide la duda sobre la media de cada mes y no el ruido de un mes
    # concreto: cubría 4 de 24 meses reales (ver docs/sales-forecast.md).
    oob_errors = seasonal_ratio / forest.oob_prediction_ - 1.0
    lower, upper = np.quantile(oob_errors, BAND_QUANTILES)

    return SalesForecastModel(
        origin=origin,
        trend=trend,
        forest=forest,
        band_lower_ratio=float(lower),
        band_upper_ratio=float(upper),
    )


def predict(model: SalesForecastModel, df: pd.DataFrame) -> pd.DataFrame:
    """Predicción puntual y franja del 80 % para cada mes de `df`."""
    features = build_features(df, model.origin)
    trend_values = np.exp(model.trend.predict(features[TREND_FEATURES]))
    predicted = trend_values * model.forest.predict(features[SEASONAL_FEATURES])
    return pd.DataFrame(
        {
            "month": df["month"].to_numpy(),
            "trend_usd": trend_values,
            "predicted_usd": predicted,
            "lower_usd": predicted * (1.0 + model.band_lower_ratio),
            "upper_usd": predicted * (1.0 + model.band_upper_ratio),
        }
    )


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------


def error_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    """MSE en USD² y traducido a lenguaje de Finanzas.

    Un MSE en USD² no se puede comparar con un ingreso. Su raíz (RMSE) sí
    está en USD, y dividida por el ingreso mensual medio del periodo da el
    "porcentaje del ingreso mensual promedio" que pide el CONTEXT.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mse = mean_squared_error(y_true, y_pred)
    rmse = float(np.sqrt(mse))
    mean_revenue = float(y_true.mean())
    return {
        "mse_usd2": float(mse),
        "rmse_usd": rmse,
        "rmse_pct_of_mean_monthly_revenue": rmse / mean_revenue * 100.0,
        "mae_usd": float(mean_absolute_error(y_true, y_pred)),
        "mape_pct": float(np.mean(np.abs(y_pred - y_true) / y_true) * 100.0),
        "mean_monthly_revenue_usd": mean_revenue,
    }


def k2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """"K2 Score" del enunciado = R² (coeficiente de determinación).

    Proporción de la variación mes a mes del ingreso real que el modelo
    explica: 1 es perfecto; 0, lo mismo que predecir siempre la media.
    """
    return float(r2_score(y_true, y_pred))


def normalized_gini(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Gini normalizado: ¿ordena el modelo los meses como ocurrieron?

    Se ordenan los meses de mayor a menor ingreso PREDICHO y se mide cuánto
    ingreso real se acumula primero, comparado con el orden perfecto. 1 =
    orden idéntico al real; 0 = orden al azar. No mira el tamaño del error,
    solo el ranking: distingue un agosto bajo "normal" de un mes flojo.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)

    def gini(actual: np.ndarray, ranking: np.ndarray) -> float:
        n = len(actual)
        # Orden descendente por ranking; empate resuelto por posición original.
        order = np.lexsort((np.arange(n), -ranking))
        cumulative_share = np.cumsum(actual[order]) / actual.sum()
        return float(cumulative_share.sum() / n - (n + 1) / (2 * n))

    return gini(y_true, y_pred) / gini(y_true, y_true)


def population_stability_index(
    expected: np.ndarray,
    actual: np.ndarray,
    bins: int = PSI_BINS,
) -> float:
    """PSI entre una distribución de referencia y otra comparada.

    Las cubetas salen de los cuantiles de `expected`, y los extremos quedan
    abiertos para que un valor fuera del rango de referencia caiga en la
    primera o la última cubeta en vez de perderse.
    PSI = Σ (actual% − expected%) × ln(actual% / expected%).
    """
    expected = np.asarray(expected, dtype=float)
    actual = np.asarray(actual, dtype=float)

    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf

    expected_share = np.histogram(expected, edges)[0] / len(expected)
    actual_share = np.histogram(actual, edges)[0] / len(actual)
    # Una cubeta vacía daría ln(0): se sustituye por una proporción mínima.
    expected_share = np.clip(expected_share, 1e-4, None)
    actual_share = np.clip(actual_share, 1e-4, None)

    return float(np.sum((actual_share - expected_share) * np.log(actual_share / expected_share)))


def psi_label(value: float) -> str:
    """Umbrales habituales del sector: < 0,10 estable; 0,10-0,25 moderado."""
    if value < PSI_STABLE_BELOW:
        return "estable"
    if value < PSI_SIGNIFICANT_FROM:
        return "cambio moderado"
    return "cambio significativo"


def evaluate_on_test(
    model: SalesForecastModel,
    train: pd.DataFrame,
    test: pd.DataFrame,
) -> Tuple[Dict[str, object], pd.DataFrame]:
    """Las 4 métricas de la rúbrica calculadas sobre el conjunto de PRUEBA."""
    predictions = predict(model, test)
    y_true = test[TARGET_COLUMN].to_numpy()
    y_pred = predictions["predicted_usd"].to_numpy()
    predictions["actual_usd"] = y_true

    # PSI 1 (el del CONTEXT): ¿cambió la mezcla del negocio entre
    # entrenamiento y prueba? El CSV no trae filas por país, así que se usa
    # el ingreso medio por consulta como indicador indirecto de la mezcla.
    psi_mix = population_stability_index(
        train["avg_revenue_per_visit_usd"], test["avg_revenue_per_visit_usd"]
    )
    # PSI 2: ¿las predicciones se reparten como los ingresos reales de prueba?
    psi_prediction = population_stability_index(y_true, y_pred)

    inside_band = (y_true >= predictions["lower_usd"]) & (y_true <= predictions["upper_usd"])

    metrics: Dict[str, object] = {
        "periods": {
            "train": _period(train),
            "test": _period(test),
            "train_rows": int(len(train)),
            "test_rows": int(len(test)),
        },
        "mse": error_metrics(y_true, y_pred),
        "k2_score_r2": k2_score(y_true, y_pred),
        "gini_normalized": normalized_gini(y_true, y_pred),
        "psi": {
            "avg_revenue_per_visit_train_vs_test": {
                "value": psi_mix,
                "label": psi_label(psi_mix),
            },
            "predicted_vs_actual_test": {
                "value": psi_prediction,
                "label": psi_label(psi_prediction),
            },
            "bins": PSI_BINS,
        },
        "variability_band": {
            "nominal_coverage_pct": (BAND_QUANTILES[1] - BAND_QUANTILES[0]) * 100.0,
            "observed_coverage_pct": float(inside_band.mean() * 100.0),
            "months_inside": int(inside_band.sum()),
            "lower_error_pct": model.band_lower_ratio * 100.0,
            "upper_error_pct": model.band_upper_ratio * 100.0,
        },
        "implied_annual_growth_pct": implied_annual_growth_pct(model),
        "random_state": RANDOM_STATE,
    }
    return metrics, predictions


def implied_annual_growth_pct(model: SalesForecastModel) -> float:
    """Crecimiento anual que ha aprendido la tendencia (CONTEXT: ~4 %)."""
    one_year = pd.DataFrame({"years_since_start": [0.0, 1.0]})
    log_values = model.trend.predict(one_year)
    return float((np.exp(log_values[1] - log_values[0]) - 1.0) * 100.0)


def _period(df: pd.DataFrame) -> str:
    return f"{df['month'].min():%Y-%m} a {df['month'].max():%Y-%m}"
