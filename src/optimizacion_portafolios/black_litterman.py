from dataclasses import dataclass

import numpy as np
import pandas as pd
from pypfopt import black_litterman, risk_models
from pypfopt.black_litterman import BlackLittermanModel
from pypfopt.efficient_frontier import EfficientFrontier


@dataclass(frozen=True)
class ResultadoBlackLitterman:
    pesos: pd.Series
    retornos_prior: pd.Series
    retornos_posteriores: pd.Series
    covarianza_posterior: pd.DataFrame
    retorno_esperado: float
    volatilidad: float
    sharpe: float
    aversion_al_riesgo: float | None


def optimizar_black_litterman(
    precios: pd.DataFrame,
    vistas_absolutas: dict[str, float],
    confianzas: dict[str, float],
    objetivo: str,
    tasa_libre_riesgo: float,
    capitalizaciones: dict[str, float] | None = None,
    precios_mercado: pd.Series | None = None,
) -> ResultadoBlackLitterman:
    precios = precios.dropna(axis="columns", how="all").dropna()
    if precios.shape[1] < 2 or len(precios) < 3:
        raise ValueError("Se requieren al menos dos activos y tres precios válidos.")
    if not np.isfinite(precios.to_numpy()).all():
        raise ValueError("Los precios contienen valores no finitos.")

    tickers = list(precios.columns)
    if set(vistas_absolutas) != set(tickers):
        raise ValueError("Debe especificarse exactamente una view para cada activo.")
    if set(confianzas) != set(tickers):
        raise ValueError("Debe especificarse una confianza para cada activo.")
    if any(not 0 < confianzas[ticker] < 1 for ticker in tickers):
        raise ValueError("Las confianzas deben estar entre 0 y 1, sin incluirlos.")
    if objetivo not in {"max_sharpe", "min_volatility", "model_weights"}:
        raise ValueError(
            "El objetivo debe ser max_sharpe, min_volatility o model_weights."
        )

    covarianza = risk_models.CovarianceShrinkage(
        precios, frequency=252
    ).ledoit_wolf()
    if capitalizaciones is None:
        capitalizaciones = {ticker: 1.0 for ticker in tickers}
    if set(capitalizaciones) != set(tickers):
        raise ValueError("Debe especificarse una capitalización para cada activo.")
    if any(
        not np.isfinite(capitalizaciones[ticker]) or capitalizaciones[ticker] <= 0
        for ticker in tickers
    ):
        raise ValueError("Las capitalizaciones deben ser números positivos.")
    if precios_mercado is None:
        raise ValueError("El prior de equilibrio requiere precios de un benchmark.")
    precios_mercado = precios_mercado.dropna()
    aversion_al_riesgo = black_litterman.market_implied_risk_aversion(
        precios_mercado,
        frequency=252,
        risk_free_rate=tasa_libre_riesgo,
    )
    if not np.isfinite(aversion_al_riesgo) or aversion_al_riesgo <= 0:
        raise ValueError(
            "No se pudo estimar una aversión al riesgo positiva con el benchmark. "
            "Prueba otro benchmark."
        )
    prior = black_litterman.market_implied_prior_returns(
        capitalizaciones,
        aversion_al_riesgo,
        covarianza,
        risk_free_rate=tasa_libre_riesgo,
    )

    modelo = BlackLittermanModel(
        covarianza,
        pi=prior,
        absolute_views={ticker: vistas_absolutas[ticker] for ticker in tickers},
        omega="idzorek",
        view_confidences=[confianzas[ticker] for ticker in tickers],
        tau=0.05,
        risk_aversion=aversion_al_riesgo,
    )
    retornos_posteriores = modelo.bl_returns()
    covarianza_posterior = modelo.bl_cov()
    retornos_prior = pd.Series(
        np.asarray(modelo.pi).reshape(-1),
        index=tickers,
        dtype=float,
    )

    if objetivo == "model_weights":
        pesos_optimos = pd.Series(
            modelo.bl_weights(risk_aversion=aversion_al_riesgo),
            dtype=float,
        ).reindex(tickers)
        retorno_esperado, volatilidad, sharpe = modelo.portfolio_performance(
            risk_free_rate=tasa_libre_riesgo
        )
    else:
        frontera = EfficientFrontier(
            retornos_posteriores,
            covarianza_posterior,
            weight_bounds=(0, 1),
        )
        if objetivo == "max_sharpe":
            pesos_optimos = frontera.max_sharpe(risk_free_rate=tasa_libre_riesgo)
        else:
            pesos_optimos = frontera.min_volatility()
        retorno_esperado, volatilidad, sharpe = frontera.portfolio_performance(
            risk_free_rate=tasa_libre_riesgo
        )

    return ResultadoBlackLitterman(
        pesos=pd.Series(pesos_optimos, dtype=float).reindex(tickers),
        retornos_prior=retornos_prior,
        retornos_posteriores=retornos_posteriores,
        covarianza_posterior=covarianza_posterior,
        retorno_esperado=float(retorno_esperado),
        volatilidad=float(volatilidad),
        sharpe=float(sharpe),
        aversion_al_riesgo=aversion_al_riesgo,
    )