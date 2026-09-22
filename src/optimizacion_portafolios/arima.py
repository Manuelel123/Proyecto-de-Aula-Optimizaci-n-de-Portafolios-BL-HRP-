"""Flujo Box-Jenkins del modelo entregado, usando precios de Yahoo Finance."""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.stattools import acf, pacf


@dataclass
class ResultadoARIMA:
    ticker: str
    precios: pd.Series
    log_precios: pd.Series
    retornos_log: pd.Series
    acf: pd.DataFrame
    pacf: pd.DataFrame
    ljung_box: pd.DataFrame
    comparacion_modelos: pd.DataFrame
    modelo_seleccionado: str
    ajuste: object


def descargar_precios_yahoo(
    ticker: str,
    fecha_inicio: date,
    fecha_fin: date | None = None,
) -> pd.Series:
    """Descarga el precio de cierre de un ticker desde Yahoo Finance."""
    fecha_fin = fecha_fin or date.today()
    datos = yf.download(
        ticker,
        start=fecha_inicio,
        end=fecha_fin + timedelta(days=1),
        interval="1d",
        auto_adjust=False,
        progress=False,
    )
    if datos.empty or "Close" not in datos:
        raise ValueError(f"Yahoo Finance no devolvió precios para {ticker}.")
    precios = datos["Close"]
    if isinstance(precios, pd.DataFrame):
        precios = precios.iloc[:, 0]
    precios = precios.rename("precio").astype(float).dropna()
    if len(precios) < 120:
        raise ValueError("Se requieren al menos 120 precios diarios para el modelo.")
    precios.index.name = "Fecha"
    return precios


def preparar_serie(precios: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Aplica logaritmo natural y primera diferencia como en el script original."""
    precios = precios.astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    if (precios <= 0).any():
        raise ValueError("Los precios deben ser positivos para aplicar logaritmos.")
    log_precios = np.log(precios).rename("log_precio")
    retornos_log = log_precios.diff().dropna().rename("retorno_log")
    return log_precios, retornos_log


def calcular_diagnostico(retornos_log: pd.Series, max_rezagos: int = 36) -> dict[str, pd.DataFrame]:
    """Calcula ACF, PACF y Ljung-Box para la etapa de identificación."""
    max_rezagos = min(max_rezagos, max(1, len(retornos_log) // 3))
    valores_acf = acf(retornos_log, nlags=max_rezagos, fft=True)[1:]
    valores_pacf = pacf(retornos_log, nlags=max_rezagos, method="ywmle")[1:]
    rezagos = np.arange(1, max_rezagos + 1)
    ljung_box = acorr_ljungbox(retornos_log, lags=rezagos, return_df=True)
    return {
        "acf": pd.DataFrame({"rezago": rezagos, "acf": valores_acf}),
        "pacf": pd.DataFrame({"rezago": rezagos, "pacf": valores_pacf}),
        "ljung_box": ljung_box.reset_index(names="rezago"),
    }


def ajustar_modelos(
    retornos_log: pd.Series,
    max_p: int = 5,
    max_q: int = 5,
) -> tuple[dict[str, object], pd.DataFrame]:
    """Busca el mejor AR, MA o ARMA sin fijar órdenes predeterminados."""
    if max_p < 0 or max_q < 0 or max_p + max_q == 0:
        raise ValueError("max_p o max_q deben permitir al menos un modelo.")

    ajustes = {}
    filas = []
    serie_modelo = retornos_log.reset_index(drop=True)
    for p in range(max_p + 1):
        for q in range(max_q + 1):
            if p == 0 and q == 0:
                continue
            nombre = f"ARIMA({p},0,{q})"
            try:
                ajuste = ARIMA(
                    serie_modelo,
                    order=(p, 0, q),
                    trend="c",
                    enforce_stationarity=True,
                    enforce_invertibility=True,
                ).fit()
            except (ValueError, np.linalg.LinAlgError):
                continue
            ajustes[nombre] = ajuste
            filas.append(
                {
                    "modelo": nombre,
                    "p": p,
                    "d": 0,
                    "q": q,
                    "tipo": "AR" if q == 0 else "MA" if p == 0 else "ARMA",
                    "aic": ajuste.aic,
                    "bic": ajuste.bic,
                    "hqic": ajuste.hqic,
                    "sse": float(np.sum(ajuste.resid**2)),
                }
            )
    if not filas:
        raise ValueError("No fue posible ajustar ningún modelo ARIMA candidato.")
    comparacion = pd.DataFrame(filas).sort_values("aic").reset_index(drop=True)
    return ajustes, comparacion


def analizar_arima(ticker: str, precios: pd.Series) -> ResultadoARIMA:
    """Ejecuta Box-Jenkins y selecciona por AIC entre AR, MA y ARMA."""
    log_precios, retornos_log = preparar_serie(precios)
    diagnostico = calcular_diagnostico(retornos_log)
    ajustes, comparacion = ajustar_modelos(retornos_log)
    modelo_seleccionado = str(comparacion.iloc[0]["modelo"])
    return ResultadoARIMA(
        ticker=ticker,
        precios=precios,
        log_precios=log_precios,
        retornos_log=retornos_log,
        acf=diagnostico["acf"],
        pacf=diagnostico["pacf"],
        ljung_box=diagnostico["ljung_box"],
        comparacion_modelos=comparacion,
        modelo_seleccionado=modelo_seleccionado,
        ajuste=ajustes[modelo_seleccionado],
    )


def pronosticar_precios(resultado: ResultadoARIMA, horizonte: int = 10) -> pd.DataFrame:
    """Pronostica retornos y reconstruye precios futuros como en el script original."""
    if horizonte < 1:
        raise ValueError("El horizonte debe ser positivo.")
    prediccion = resultado.ajuste.get_prediction(
        start=len(resultado.retornos_log),
        end=len(resultado.retornos_log) + horizonte - 1,
        dynamic=False,
    )
    retornos = np.asarray(prediccion.predicted_mean, dtype=float)
    precios = resultado.precios.iloc[-1] * np.exp(np.cumsum(retornos))
    indice = pd.bdate_range(
        resultado.precios.index[-1] + pd.Timedelta(days=1), periods=horizonte
    )
    return pd.DataFrame(
        {"retorno_log_pronosticado": retornos, "precio_pronosticado": precios},
        index=indice,
    )


def backtest_arima(precios: pd.Series, n_test: int = 10) -> pd.DataFrame:
    """Replica el pronóstico fuera de muestra de los últimos n_test precios."""
    if n_test < 1 or len(precios) <= n_test + 30:
        raise ValueError("No hay suficientes datos para separar entrenamiento y prueba.")
    train = precios.iloc[:-n_test]
    test = precios.iloc[-n_test:]
    resultado = analizar_arima("backtest", train)
    pronosticos = pronosticar_precios(resultado, n_test)
    comparacion = pd.DataFrame(
        {
            "precio_real": test.to_numpy(),
            "precio_pronosticado": pronosticos["precio_pronosticado"].to_numpy(),
        },
        index=test.index,
    )
    comparacion["error_porcentual"] = (
        (comparacion["precio_real"] - comparacion["precio_pronosticado"])
        / comparacion["precio_real"]
        * 100
    )
    return comparacion