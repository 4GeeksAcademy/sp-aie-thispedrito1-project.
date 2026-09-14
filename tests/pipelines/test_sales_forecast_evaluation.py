"""Tests de la evaluación del modelo de ingresos (data/process/sales_forecast_evaluation.py).

Desde la raíz del repo, con el venv de la API:

    services/api/.venv/bin/python -m pytest tests/pipelines/test_sales_forecast_evaluation.py

Usan el CSV real provisto (solo lectura) y DataFrames escritos a mano. No
abren conexiones ni escriben archivos.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.model_selection import KFold

from data.process.sales_forecast import SalesDataError, load_sales_data, split_train_test
from data.process.sales_forecast_comparison import MEMORIZING_FOREST, TREND_ONLY
from data.process.sales_forecast_evaluation import (
    CV_SPLITS,
    LEARNING_CURVE_SIZES,
    FIT_OVERFITTING,
    FIT_UNDERFITTING,
    FIT_WELL,
    VALIDATION_MONTHS,
    check_chronological_folds,
    cross_validate_forecast,
    diagnose_cross_validation,
    diagnose_fit,
    evaluate_window,
    learning_curve_windows,
    seasonal_naive_predictions,
    summarize_windows,
    temporal_cv_folds,
    window_errors,
)

SALES_CSV = Path(__file__).resolve().parents[2] / "data" / "raw" / "healthcore_sales.csv"


@pytest.fixture(scope="module")
def train() -> pd.DataFrame:
    train, _test = split_train_test(load_sales_data(SALES_CSV))
    return train


# --- Orden cronológico de los pliegues (test que pide la rúbrica) -------------


def test_temporal_cv_preserves_chronological_order_in_every_fold(train):
    folds = temporal_cv_folds(train)
    months = train["month"]
    assert len(folds) == CV_SPLITS >= 5

    previous_validation = None
    for train_idx, val_idx in folds:
        # Dentro de cada pliegue los índices van en orden y sin saltos...
        assert np.all(np.diff(train_idx) == 1)
        assert np.all(np.diff(val_idx) == 1)
        # ...todo el entrenamiento es anterior a toda la validación...
        assert train_idx.max() < val_idx.min()
        assert months.iloc[train_idx].max() < months.iloc[val_idx].min()
        # ...y ningún índice de este pliegue aparece antes que uno del anterior.
        if previous_validation is not None:
            assert val_idx.min() > previous_validation.max()
            assert months.iloc[val_idx].min() > months.iloc[previous_validation].max()
        previous_validation = val_idx


def test_temporal_cv_expands_training_and_validates_the_following_year(train):
    folds = temporal_cv_folds(train)
    assert [len(train_idx) for train_idx, _ in folds] == [36, 48, 60, 72, 84]
    for (train_idx, val_idx), year in zip(folds, range(2019, 2024)):
        assert train_idx[0] == 0
        assert val_idx[0] == train_idx[-1] + 1
        assert len(val_idx) == VALIDATION_MONTHS
        assert set(train.iloc[val_idx]["month"].dt.year) == {year}


def test_temporal_cv_never_reaches_the_test_years(train):
    folds = temporal_cv_folds(train)
    used = np.concatenate([np.concatenate(fold) for fold in folds])
    assert train.iloc[used]["month"].max() == pd.Timestamp("2023-12-01")


def test_unsorted_data_is_rejected_instead_of_silently_reordered(train):
    shuffled = train.sample(frac=1.0, random_state=0)
    with pytest.raises(SalesDataError, match="ordenados"):
        temporal_cv_folds(shuffled)


def test_check_detects_shuffled_folds(train):
    shuffled_folds = list(KFold(n_splits=5, shuffle=True, random_state=0).split(train))
    with pytest.raises(SalesDataError, match="consecutivos"):
        check_chronological_folds(shuffled_folds, train["month"])


def test_check_detects_validation_before_training(train):
    # KFold sin barajar: el primer pliegue valida 2016 y entrena con 2017-2023.
    unshuffled_folds = list(KFold(n_splits=5, shuffle=False).split(train))
    with pytest.raises(SalesDataError, match="entrenamiento"):
        check_chronological_folds(unshuffled_folds, train["month"])


def test_check_detects_folds_listed_out_of_order(train):
    folds = temporal_cv_folds(train)
    with pytest.raises(SalesDataError, match="pliegue anterior"):
        check_chronological_folds(list(reversed(folds)), train["month"])


# --- Curva de aprendizaje ----------------------------------------------------


def test_learning_curve_windows_are_chronological_and_inside_training(train):
    windows = learning_curve_windows(len(train))
    assert sorted(windows) == list(LEARNING_CURVE_SIZES)
    # 24 meses caben en 6 posiciones anuales; 84 meses solo en una.
    assert len(windows[24]) == 6
    assert len(windows[84]) == 1
    for size, folds in windows.items():
        check_chronological_folds(folds, train["month"])
        for train_idx, val_idx in folds:
            assert len(train_idx) == size
            assert val_idx.max() < len(train)


def test_learning_curve_rejects_a_size_that_does_not_fit():
    with pytest.raises(SalesDataError, match="No caben"):
        learning_curve_windows(96, sizes=(90,))


# --- Métricas y referencia ----------------------------------------------------


def test_window_errors_by_hand():
    # Errores de +10 y -30 sobre una media real de 200.
    errors = window_errors(np.array([100.0, 300.0]), np.array([110.0, 270.0]))
    assert errors["mae_usd"] == pytest.approx(20.0)
    assert errors["rmse_usd"] == pytest.approx(np.sqrt((100 + 900) / 2))
    assert errors["mae_pct"] == pytest.approx(10.0)
    assert errors["bias_pct"] == pytest.approx(-5.0)  # negativo = subestima


def test_rmse_is_never_below_mae_and_grows_with_one_big_miss():
    actual = np.full(12, 100.0)
    even = window_errors(actual, actual + 5)            # doce errores de 5
    one_big = window_errors(actual, actual + np.r_[60.0, np.zeros(11)])  # un error de 60
    assert even["mae_usd"] == pytest.approx(one_big["mae_usd"])
    assert one_big["rmse_usd"] > even["rmse_usd"] >= even["mae_usd"]


def test_seasonal_naive_uses_same_month_of_previous_year():
    months = pd.date_range("2016-01-01", periods=24, freq="MS")
    frame = pd.DataFrame({"month": months, "revenue_usd": np.arange(24, dtype=float)})
    predictions = seasonal_naive_predictions(frame.iloc[:12], frame.iloc[12:])
    assert predictions.tolist() == list(range(12))


def test_seasonal_naive_refuses_a_month_without_previous_year():
    months = pd.date_range("2016-01-01", periods=18, freq="MS")
    frame = pd.DataFrame({"month": months, "revenue_usd": np.ones(18)})
    with pytest.raises(SalesDataError, match="año anterior"):
        seasonal_naive_predictions(frame.iloc[:6], frame.iloc[6:])


def test_summary_reports_mean_and_sample_std_across_folds():
    def window(value):
        errors = {"rmse_pct": value, "mae_pct": value / 2}
        return {"train": errors, "validation": errors, "seasonal_naive_validation": errors}

    summary = summarize_windows([window(2.0), window(4.0), window(6.0)])
    assert summary["validation"]["rmse_pct"]["mean"] == pytest.approx(4.0)
    assert summary["validation"]["rmse_pct"]["std"] == pytest.approx(2.0)  # ddof=1
    assert summary["validation"]["rmse_pct"]["n"] == 3

    single = summarize_windows([window(3.0)])
    assert single["validation"]["rmse_pct"]["std"] is None


# --- Diagnóstico -------------------------------------------------------------


@pytest.mark.parametrize(
    "train_pct, validation_pct, baseline_pct, expected",
    [
        (2.0, 2.4, 6.0, FIT_WELL),           # errores bajos y cercanos
        (1.0, 3.0, 6.0, FIT_OVERFITTING),    # validación 3 veces peor
        (1.0, 6.5, 6.0, FIT_OVERFITTING),    # fuera de muestra no mejora la referencia
        (6.5, 6.8, 6.0, FIT_UNDERFITTING),   # ni en entrenamiento mejora la referencia
    ],
)
def test_diagnose_fit(train_pct, validation_pct, baseline_pct, expected):
    assert diagnose_fit(train_pct, validation_pct, baseline_pct) == expected


def test_diagnose_checks_underfitting_before_the_gap():
    # Brecha pequeña (cociente 1,1) pero falla en todo: no es "bien ajustado".
    assert diagnose_fit(9.0, 9.9, 6.0) == FIT_UNDERFITTING


# --- Modelos de comparación (malos a propósito) -------------------------------


def test_evaluation_uses_the_given_forecaster(train):
    folds = temporal_cv_folds(train)
    train_idx, val_idx = folds[-1]
    current = evaluate_window(train.iloc[train_idx], train.iloc[val_idx])
    trend_only = evaluate_window(train.iloc[train_idx], train.iloc[val_idx], TREND_ONLY)
    # Mismos meses y misma referencia, distinto modelo.
    assert trend_only["seasonal_naive_validation"] == current["seasonal_naive_validation"]
    assert trend_only["train"]["rmse_pct"] > current["train"]["rmse_pct"]


def test_diagnosis_recognises_real_underfitting_and_overfitting(train):
    """La regla no solo aprueba nuestro modelo: detecta los dos fallos reales."""
    assert diagnose_cross_validation(cross_validate_forecast(train, TREND_ONLY)) == FIT_UNDERFITTING
    assert diagnose_cross_validation(cross_validate_forecast(train, MEMORIZING_FOREST)) == FIT_OVERFITTING
    assert diagnose_cross_validation(cross_validate_forecast(train)) == FIT_WELL
