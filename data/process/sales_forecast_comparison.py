"""Modelos de comparación, malos A PROPÓSITO, para ilustrar la evaluación.

No son candidatos a producción. Existen para que el reporte de evaluación
(data/eval/evaluation_report.md) muestre con estos mismos datos cómo se ven
de verdad un underfitting y un overfitting, al lado del modelo real, en vez
de describirlos con curvas de libro inventadas:

- TREND_ONLY ("Solo tendencia"): la recta de crecimiento del modelo real, sin
  el Random Forest estacional. No sabe que octubre-diciembre suben y
  julio-agosto bajan, así que falla igual en lo visto y en lo nuevo
  (underfitting).
- MEMORIZING_FOREST ("Bosque que memoriza"): un Random Forest sobre el ingreso
  en USD con el mes concreto como variable, sin separar la tendencia. Cada mes
  de entrenamiento es un valor único que el bosque se aprende de memoria,
  ruido incluido, y no puede extrapolar el crecimiento a un año nuevo
  (overfitting).

Usan exactamente las mismas variables de calendario que el modelo real
(build_features) y ninguna otra, para que la comparación sea justa.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression

from data.process.sales_forecast import (
    N_ESTIMATORS,
    RANDOM_STATE,
    SEASONAL_FEATURES,
    TARGET_COLUMN,
    TREND_FEATURES,
    build_features,
)
from data.process.sales_forecast_evaluation import Forecaster


@dataclass
class _FittedModel:
    origin: pd.Timestamp
    estimator: object


def fit_trend_only(train: pd.DataFrame) -> _FittedModel:
    origin = train["month"].min()
    features = build_features(train, origin)
    estimator = LinearRegression().fit(features[TREND_FEATURES], np.log(train[TARGET_COLUMN]))
    return _FittedModel(origin, estimator)


def predict_trend_only(model: _FittedModel, df: pd.DataFrame) -> pd.DataFrame:
    features = build_features(df, model.origin)
    return pd.DataFrame({"predicted_usd": np.exp(model.estimator.predict(features[TREND_FEATURES]))})


def fit_memorizing_forest(train: pd.DataFrame) -> _FittedModel:
    origin = train["month"].min()
    features = build_features(train, origin)[TREND_FEATURES + SEASONAL_FEATURES]
    estimator = RandomForestRegressor(n_estimators=N_ESTIMATORS, random_state=RANDOM_STATE, n_jobs=1)
    estimator.fit(features, train[TARGET_COLUMN])
    return _FittedModel(origin, estimator)


def predict_memorizing_forest(model: _FittedModel, df: pd.DataFrame) -> pd.DataFrame:
    features = build_features(df, model.origin)[TREND_FEATURES + SEASONAL_FEATURES]
    return pd.DataFrame({"predicted_usd": model.estimator.predict(features)})


TREND_ONLY = Forecaster("Solo tendencia", fit_trend_only, predict_trend_only)
MEMORIZING_FOREST = Forecaster("Bosque que memoriza", fit_memorizing_forest, predict_memorizing_forest)
COMPARISON_MODELS = (TREND_ONLY, MEMORIZING_FOREST)
