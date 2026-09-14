"""Tests del modelo de predicción de ventas (data/process/sales_forecast.py).

Desde la raíz del repo, con el venv de la API:

    services/api/.venv/bin/python -m pytest tests/pipelines/test_sales_forecast.py

Usan el CSV real provisto (data/raw/healthcore_sales.csv, solo lectura) y
DataFrames escritos a mano. No abren conexiones ni escriben archivos.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from data.process.sales_forecast import (
    SalesDataError,
    build_features,
    clean_sales_data,
    error_metrics,
    fit_forecast_model,
    load_sales_data,
    normalized_gini,
    population_stability_index,
    predict,
    split_train_test,
    validate_sales_data,
)

SALES_CSV = Path(__file__).resolve().parents[2] / "data" / "raw" / "healthcore_sales.csv"


@pytest.fixture(scope="module")
def sales() -> pd.DataFrame:
    return load_sales_data(SALES_CSV)


@pytest.fixture(scope="module")
def split(sales):
    return split_train_test(sales)


def _monthly_frame(start: str, periods: int) -> pd.DataFrame:
    months = pd.date_range(start, periods=periods, freq="MS")
    visits = np.full(periods, 1000)
    avg = np.full(periods, 150.0)
    return pd.DataFrame(
        {
            "month": months.strftime("%Y-%m-%d"),
            "revenue_usd": visits * avg,
            "visits_count": visits,
            "avg_revenue_per_visit_usd": avg,
            "region": "consolidated",
        }
    )


# --- Dataset provisto -------------------------------------------------------


def test_provided_dataset_matches_context(sales):
    assert len(sales) == 120
    assert sales["month"].min() == pd.Timestamp("2016-01-01")
    assert sales["month"].max() == pd.Timestamp("2025-12-01")
    assert (sales["revenue_usd"] > 0).all()
    assert set(sales["region"]) == {"consolidated"}


# --- Split 8 años / 2 años y fuga de datos -----------------------------------


def test_split_uses_first_8_years_for_training_and_last_2_for_test(split):
    train, test = split
    assert sorted(train["month"].dt.year.unique()) == list(range(2016, 2024))
    assert sorted(test["month"].dt.year.unique()) == [2024, 2025]
    assert len(train) == 96
    assert len(test) == 24


def test_split_has_no_data_leakage_between_sets(sales, split):
    train, test = split
    # Ningún mes aparece en los dos conjuntos...
    assert set(train["month"]).isdisjoint(set(test["month"]))
    # ...todo el entrenamiento es anterior a toda la prueba...
    assert train["month"].max() < test["month"].min()
    # ...y entre los dos cubren el histórico completo, sin perder meses.
    assert len(train) + len(test) == len(sales)


def test_split_does_not_depend_on_row_order(sales):
    shuffled = sales.sample(frac=1.0, random_state=0)
    train, test = split_train_test(shuffled)
    assert sorted(train["month"].dt.year.unique()) == list(range(2016, 2024))
    assert train["month"].max() < test["month"].min()


def test_split_rejects_a_history_that_is_not_10_years():
    nine_years = clean_sales_data(_monthly_frame("2016-01-01", 108))
    with pytest.raises(SalesDataError, match="10 años"):
        split_train_test(nine_years)


def test_model_never_sees_test_values(split):
    """La prueba más fuerte de no-fuga: alterar la prueba no cambia el modelo."""
    train, test = split
    baseline = predict(fit_forecast_model(train), test)

    tampered_test = test.copy()
    tampered_test["revenue_usd"] = tampered_test["revenue_usd"] * 10
    tampered_test["visits_count"] = 1
    tampered = predict(fit_forecast_model(train), tampered_test)

    pd.testing.assert_frame_equal(baseline, tampered)


def test_test_features_are_measured_from_the_training_origin(split):
    train, test = split
    features = build_features(test, origin=train["month"].min())
    assert features["years_since_start"].iloc[0] == pytest.approx(8.0)
    assert features["month_of_year"].tolist()[:3] == [1, 2, 3]


def test_training_is_reproducible(split):
    train, test = split
    first = predict(fit_forecast_model(train), test)
    second = predict(fit_forecast_model(train), test)
    pd.testing.assert_frame_equal(first, second)


# --- Limpieza y validación ---------------------------------------------------


def test_missing_revenue_is_rebuilt_from_visits_and_average():
    frame = _monthly_frame("2016-01-01", 3)
    frame.loc[1, "revenue_usd"] = None
    cleaned = clean_sales_data(frame)
    assert cleaned.loc[1, "revenue_usd"] == pytest.approx(150_000.0)


def test_unrecoverable_null_leaves_a_gap_that_validation_rejects():
    frame = _monthly_frame("2016-01-01", 3)
    frame.loc[1, ["revenue_usd", "visits_count"]] = None
    cleaned = clean_sales_data(frame)
    with pytest.raises(SalesDataError, match="Faltan meses"):
        validate_sales_data(cleaned)


def test_non_positive_revenue_is_rejected():
    frame = clean_sales_data(_monthly_frame("2016-01-01", 3))
    frame.loc[2, "revenue_usd"] = 0
    with pytest.raises(SalesDataError, match="positivos"):
        validate_sales_data(frame)


def test_missing_context_column_is_rejected(tmp_path):
    path = tmp_path / "sales.csv"
    _monthly_frame("2016-01-01", 3).drop(columns=["visits_count"]).to_csv(path, index=False)
    with pytest.raises(SalesDataError, match="visits_count"):
        load_sales_data(path)


# --- Métricas ----------------------------------------------------------------


def test_error_metrics_translate_mse_into_percentage_of_mean_revenue():
    metrics = error_metrics(np.array([100.0, 300.0]), np.array([110.0, 290.0]))
    assert metrics["mse_usd2"] == pytest.approx(100.0)
    assert metrics["rmse_usd"] == pytest.approx(10.0)
    assert metrics["rmse_pct_of_mean_monthly_revenue"] == pytest.approx(5.0)


def test_gini_is_one_for_perfect_ranking_and_negative_for_inverted():
    actual = np.array([10.0, 20.0, 30.0, 40.0])
    # Solo importa el orden, no la escala de la predicción.
    assert normalized_gini(actual, actual * 3 + 7) == pytest.approx(1.0)
    assert normalized_gini(actual, -actual) == pytest.approx(-1.0)


def test_psi_is_zero_for_identical_distributions_and_grows_with_shift():
    reference = np.linspace(0, 100, 50)
    assert population_stability_index(reference, reference) == pytest.approx(0.0)
    assert population_stability_index(reference, reference + 60) > 0.25
