"""Evaluación técnica del modelo de predicción de ingresos (ticket de staging).

Responde a las tres preguntas del ticket de la clase "Evaluación de un Modelo
de Regresión" sobre el modelo de data/process/sales_forecast.py, SIN
modificarlo:

1. ¿Underfitting, overfitting o bien ajustado? -> curva de aprendizaje.
2. ¿Qué tan estable es si cambia la porción de datos? -> validación cruzada
   temporal (media ± desviación estándar entre pliegues).
3. ¿Qué acción correctiva? -> la redacta data/eval/evaluation_report.md con
   estas cifras como evidencia.

Todo se calcula SOLO con el conjunto de entrenamiento (2016-2023). La prueba
2024-2025 sigue reservada como examen final: si se usara aquí para decidir
algo, las métricas de docs/sales-forecast.md dejarían de ser honestas.

Lógica pura, sin gráficos ni escritura de archivos: la orquesta
scripts/evaluate_sales_forecast.py.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit

from data.process.sales_forecast import (
    TARGET_COLUMN,
    SalesDataError,
    fit_forecast_model,
    predict,
)

CV_SPLITS = 5
VALIDATION_MONTHS = 12
# Tamaños de la curva en años completos: así cada mes del año aparece el mismo
# número de veces y el bosque estacional no queda cojo en ningún mes.
LEARNING_CURVE_SIZES = (24, 36, 48, 60, 72, 84)
SEASON_LENGTH = 12

# Métrica principal elegida para el negocio (justificación en el reporte).
PRIMARY_METRIC = "rmse_pct"

FIT_WELL = "bien ajustado"
FIT_UNDERFITTING = "underfitting"
FIT_OVERFITTING = "overfitting"
# Brecha "amplia" = validación al menos un 50 % peor que entrenamiento. No se
# exige igualdad: validar es predecir un año que el modelo no vio, y eso añade
# el error de extrapolar la tendencia 12 meses, que no es memorización. El
# reporte muestra además cuánto margen queda hasta el umbral, para que el
# veredicto no dependa de un redondeo.
OVERFIT_GAP_RATIO = 1.5

Fold = Tuple[np.ndarray, np.ndarray]


# ---------------------------------------------------------------------------
# Pliegues temporales y su verificación
# ---------------------------------------------------------------------------


def ensure_chronological(df: pd.DataFrame) -> None:
    """Los pliegues se definen por posición: el frame debe venir en orden.

    No se reordena en silencio. Si llegara desordenado, las posiciones 0-35
    ya no serían "los primeros 36 meses" y la validación vería el futuro sin
    que nada fallara. Mejor rechazarlo con un mensaje claro.
    """
    months = df["month"]
    if months.duplicated().any() or not months.is_monotonic_increasing:
        raise SalesDataError(
            "Los datos deben venir ordenados por mes y sin meses repetidos "
            "antes de crear pliegues temporales"
        )


def temporal_cv_folds(
    train: pd.DataFrame,
    n_splits: int = CV_SPLITS,
    validation_months: int = VALIDATION_MONTHS,
) -> List[Fold]:
    """Pliegues de TimeSeriesSplit: entrenar con el pasado, validar el año siguiente.

    Con 96 meses, 5 pliegues y validación de 12 meses, los entrenamientos
    miden 36, 48, 60, 72 y 84 meses, y cada uno valida el año inmediatamente
    posterior (2019, 2020, 2021, 2022 y 2023). TimeSeriesSplit nunca baraja,
    pero se comprueba igualmente con check_chronological_folds.
    """
    ensure_chronological(train)
    splitter = TimeSeriesSplit(n_splits=n_splits, test_size=validation_months)
    folds = [(train_idx, val_idx) for train_idx, val_idx in splitter.split(train)]
    check_chronological_folds(folds, train["month"])
    return folds


def check_chronological_folds(folds: Sequence[Fold], months: pd.Series) -> None:
    """Verificación explícita de que ningún pliegue mezcla el orden del tiempo.

    Para cada pliegue exige:
    - entrenamiento y validación son bloques de meses consecutivos (un índice
      barajado o con saltos rompe la diferencia de 1 entre posiciones);
    - todo el entrenamiento es anterior a toda la validación;
    - la validación empieza después de que terminara la del pliegue anterior.
    """
    months = pd.Series(months).reset_index(drop=True)
    previous_validation_end: Optional[pd.Timestamp] = None

    for number, (train_idx, val_idx) in enumerate(folds, start=1):
        for label, positions in (("entrenamiento", train_idx), ("validación", val_idx)):
            if len(positions) == 0:
                raise SalesDataError(f"Pliegue {number}: {label} vacío")
            if np.any(np.diff(positions) != 1):
                raise SalesDataError(
                    f"Pliegue {number}: el {label} no es un bloque de meses "
                    "consecutivos en orden (¿datos barajados?)"
                )

        train_end = months.iloc[train_idx].max()
        validation_start = months.iloc[val_idx].min()
        if train_end >= validation_start:
            raise SalesDataError(
                f"Pliegue {number}: el entrenamiento llega a {train_end:%Y-%m} "
                f"y la validación empieza en {validation_start:%Y-%m}"
            )
        if previous_validation_end is not None and validation_start <= previous_validation_end:
            raise SalesDataError(
                f"Pliegue {number}: su validación empieza en {validation_start:%Y-%m}, "
                f"antes de que terminara la del pliegue anterior ({previous_validation_end:%Y-%m})"
            )
        previous_validation_end = months.iloc[val_idx].max()


def learning_curve_windows(
    n_months: int,
    sizes: Sequence[int] = LEARNING_CURVE_SIZES,
    validation_months: int = VALIDATION_MONTHS,
    step: int = SEASON_LENGTH,
) -> Dict[int, List[Fold]]:
    """Ventanas móviles de la curva de aprendizaje, agrupadas por tamaño.

    Para cada tamaño se usan TODAS las ventanas de ese largo que caben en el
    entrenamiento, desplazadas de año en año, y cada una valida los 12 meses
    siguientes. Así el tamaño no se confunde con la distancia: con una sola
    ventana anclada en 2016, "24 meses" validaría 2018 y "84 meses" 2023, y
    la curva mezclaría el efecto de tener más datos con el de mirar otro año.
    """
    windows: Dict[int, List[Fold]] = {}
    for size in sizes:
        starts = range(0, n_months - size - validation_months + 1, step)
        windows[size] = [
            (np.arange(start, start + size), np.arange(start + size, start + size + validation_months))
            for start in starts
        ]
        if not windows[size]:
            raise SalesDataError(
                f"No caben {size} meses de entrenamiento más {validation_months} "
                f"de validación en {n_months} meses"
            )
    return windows


# ---------------------------------------------------------------------------
# Errores de una ventana
# ---------------------------------------------------------------------------


def window_errors(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    """MAE y RMSE en USD y en % del ingreso mensual medio de esa ventana.

    El porcentaje se calcula sobre la media de la propia ventana porque el
    ingreso crece ~4 % al año: 90.000 USD de error en 2016 pesan más que en
    2023, y la desviación estándar en USD saldría inflada por el crecimiento.

    `bias_pct` es el error con signo: positivo = el modelo sobreestima.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mean_revenue = float(y_true.mean())
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    return {
        "mae_usd": mae,
        "rmse_usd": rmse,
        "mae_pct": mae / mean_revenue * 100.0,
        "rmse_pct": rmse / mean_revenue * 100.0,
        "bias_pct": float(np.mean(y_pred - y_true)) / mean_revenue * 100.0,
    }


def seasonal_naive_predictions(history: pd.DataFrame, target: pd.DataFrame) -> np.ndarray:
    """Referencia ingenua: cada mes vale lo mismo que ese mes del año anterior.

    Sirve para responder "¿un error del 3 % es alto?" con un punto de
    comparación y no a ojo. Solo mira `history`: ningún mes objetivo se usa
    para predecirse a sí mismo.
    """
    lookup = history.set_index("month")[TARGET_COLUMN]
    previous_year = target["month"] - pd.DateOffset(years=1)
    values = lookup.reindex(previous_year.to_numpy())
    if values.isna().any():
        raise SalesDataError(
            "La referencia ingenua necesita el mismo mes del año anterior dentro "
            "del historial de entrenamiento"
        )
    return values.to_numpy()


def evaluate_window(train_part: pd.DataFrame, validation_part: pd.DataFrame) -> Dict[str, object]:
    """Entrena con `train_part` y mide el error en entrenamiento y en validación."""
    model = fit_forecast_model(train_part)
    train_true = train_part[TARGET_COLUMN].to_numpy()
    validation_true = validation_part[TARGET_COLUMN].to_numpy()
    return {
        "train_period": _period(train_part),
        "validation_period": _period(validation_part),
        "train_months": int(len(train_part)),
        "train": window_errors(train_true, predict(model, train_part)["predicted_usd"]),
        "validation": window_errors(validation_true, predict(model, validation_part)["predicted_usd"]),
        "seasonal_naive_validation": window_errors(
            validation_true, seasonal_naive_predictions(train_part, validation_part)
        ),
    }


def summarize_windows(results: Sequence[Dict[str, object]]) -> Dict[str, Dict[str, Dict[str, object]]]:
    """Media ± desviación estándar de cada métrica a través de las ventanas.

    Desviación estándar muestral (ddof=1): con 5 pliegues, la poblacional
    subestimaría la dispersión. Con una sola ventana no hay dispersión que
    medir y `std` queda en None, en vez de un 0 que parecería estabilidad.
    """
    summary: Dict[str, Dict[str, Dict[str, object]]] = {}
    for split in ("train", "validation", "seasonal_naive_validation"):
        summary[split] = {}
        for metric in results[0][split]:
            values = np.array([result[split][metric] for result in results], dtype=float)
            summary[split][metric] = {
                "mean": float(values.mean()),
                "std": float(values.std(ddof=1)) if len(values) > 1 else None,
                "n": int(len(values)),
            }
    return summary


# ---------------------------------------------------------------------------
# Validación cruzada, curva de aprendizaje y diagnóstico
# ---------------------------------------------------------------------------


def cross_validate_forecast(train: pd.DataFrame) -> Dict[str, object]:
    """Validación cruzada temporal con CV_SPLITS pliegues sobre el entrenamiento."""
    folds = temporal_cv_folds(train)
    results = [
        {"fold": number, **evaluate_window(train.iloc[train_idx], train.iloc[val_idx])}
        for number, (train_idx, val_idx) in enumerate(folds, start=1)
    ]
    return {
        "strategy": f"TimeSeriesSplit(n_splits={CV_SPLITS}, test_size={VALIDATION_MONTHS})",
        "folds": results,
        "summary": summarize_windows(results),
    }


def learning_curve(train: pd.DataFrame, sizes: Sequence[int] = LEARNING_CURVE_SIZES) -> List[Dict[str, object]]:
    """Error de entrenamiento y validación según los meses de historia usados."""
    ensure_chronological(train)
    points = []
    for size, windows in learning_curve_windows(len(train), sizes).items():
        check_chronological_folds(windows, train["month"])
        results = [evaluate_window(train.iloc[train_idx], train.iloc[val_idx]) for train_idx, val_idx in windows]
        points.append(
            {
                "train_months": size,
                "windows": len(results),
                "summary": summarize_windows(results),
            }
        )
    return points


def diagnose_fit(
    train_rmse_pct: float,
    validation_rmse_pct: float,
    baseline_rmse_pct: float,
) -> str:
    """Clasifica el ajuste del modelo: FIT_WELL, FIT_UNDERFITTING o FIT_OVERFITTING.

    Recibe la media entre pliegues de la validación cruzada, en % del ingreso
    mensual medio:
    - train_rmse_pct: error del modelo sobre los meses con los que entrenó.
    - validation_rmse_pct: error sobre el año siguiente, que no vio.
    - baseline_rmse_pct: error de la referencia ingenua ("mismo mes del año
      anterior") sobre esa misma validación.
    """
    # 1. Underfitting primero: un modelo que no aprendió nada puede tener poca
    #    brecha (falla igual en todo) y colarse como "bien ajustado". Si ni
    #    siquiera en los meses que ya vio mejora a "copiar el año anterior",
    #    no capturó el patrón.
    if train_rmse_pct >= baseline_rmse_pct:
        return FIT_UNDERFITTING
    # 2. Overfitting: aprende bien lo visto pero no generaliza. O la brecha
    #    es amplia (cociente, independiente de la escala del error), o fuera
    #    de muestra ya no mejora a la referencia ingenua.
    if (
        validation_rmse_pct >= train_rmse_pct * OVERFIT_GAP_RATIO
        or validation_rmse_pct >= baseline_rmse_pct
    ):
        return FIT_OVERFITTING
    return FIT_WELL


def _period(df: pd.DataFrame) -> str:
    return f"{df['month'].min():%Y-%m} a {df['month'].max():%Y-%m}"
