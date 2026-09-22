"""Flujo Box-Jenkins del modelo entregado, usando precios de Yahoo Finance."""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.stattools import acf, pacf
from scipy.stats import norm


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
    limite_confianza = norm.ppf(0.975) / np.sqrt(len(retornos_log))
    rezagos = np.arange(1, max_rezagos + 1)
    ljung_box = acorr_ljungbox(retornos_log, lags=rezagos, return_df=True)
    return {
        "acf": pd.DataFrame(
            {
                "rezago": rezagos,
                "acf": valores_acf,
                "limite_confianza": limite_confianza,
            }
        ),
        "pacf": pd.DataFrame(
            {
                "rezago": rezagos,
                "pacf": valores_pacf,
                "limite_confianza": limite_confianza,
            }
        ),
        "ljung_box": ljung_box.reset_index(names="rezago"),
    }


def _ordenes_box_jenkins(
    retornos_log: pd.Series,
    max_p: int,
    max_q: int,
    nivel_significancia: float,
    max_rezagos: int,
) -> list[tuple[int, int]]:
    """Propone p y q usando los cortes significativos de PACF y ACF."""
    rezagos = min(max_rezagos, max(1, len(retornos_log) // 3))
    valores_acf = acf(retornos_log, nlags=rezagos, fft=True)[1:]
    valores_pacf = pacf(retornos_log, nlags=rezagos, method="ywmle")[1:]
    limite_confianza = norm.ppf(1 - nivel_significancia / 2) / np.sqrt(len(retornos_log))

    def orden_por_corte(valores: np.ndarray, maximo: int) -> int | None:
        significativos = np.abs(valores) > limite_confianza
        if not significativos[0]:
            return None
        primer_rezago_no_significativo = np.flatnonzero(~significativos)
        if primer_rezago_no_significativo.size == 0:
            return None
        orden = int(primer_rezago_no_significativo[0])
        significativos_despues_del_corte = significativos[orden:].sum()
        maximos_picos_aislados = max(1, int(np.ceil(0.05 * len(significativos[orden:]))))
        if significativos_despues_del_corte > maximos_picos_aislados:
            return None
        return min(orden, maximo)

    orden_p = orden_por_corte(valores_pacf, max_p)
    orden_q = orden_por_corte(valores_acf, max_q)
    candidatos = {(0, 0)}
    if orden_p is not None and orden_q is not None:
        if orden_p == orden_q:
            candidatos.add((orden_p, orden_q))
        elif orden_p < orden_q:
            candidatos.add((orden_p, 0))
        else:
            candidatos.add((0, orden_q))
    elif orden_p is not None:
        candidatos.add((orden_p, 0))
    elif orden_q is not None:
        candidatos.add((0, orden_q))
    return sorted(candidatos)


def ajustar_modelos(
    retornos_log: pd.Series,
    max_p: int = 3,
    max_q: int = 3,
    max_rezagos_diagnostico: int = 36,
    nivel_significancia: float = 0.05,
    log_precios: pd.Series | None = None,
    tamano_validacion: int | None = None,
) -> tuple[dict[str, object], pd.DataFrame]:
    """Identifica candidatos con ACF/PACF y valida sus residuos y pronósticos."""
    if max_p < 0 or max_q < 0 or max_p + max_q == 0:
        raise ValueError("max_p o max_q deben permitir al menos un modelo.")
    if max_rezagos_diagnostico < 1 or not 0 < nivel_significancia < 1:
        raise ValueError("El número de rezagos debe ser positivo y alpha debe estar entre 0 y 1.")

    ajustes = {}
    filas = []
    serie_modelo = retornos_log.reset_index(drop=True)
    precios_log_modelo = (
        log_precios.reset_index(drop=True)
        if log_precios is not None
        else None
    )
    if precios_log_modelo is not None and len(precios_log_modelo) != len(serie_modelo) + 1:
        raise ValueError("log_precios debe tener una observación más que retornos_log.")
    if tamano_validacion is None:
        tamano_validacion = min(20, max(10, len(serie_modelo) // 5))
    if tamano_validacion < 1 or tamano_validacion >= len(serie_modelo) - 20:
        raise ValueError("El tamaño de validación no permite conservar suficientes datos de entrenamiento.")
    inicio_validacion = len(serie_modelo) - tamano_validacion
    candidatos = _ordenes_box_jenkins(
        serie_modelo,
        max_p,
        max_q,
        nivel_significancia,
        max_rezagos_diagnostico,
    )
    for p, q in candidatos:
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
        rezagos = np.arange(1, min(max_rezagos_diagnostico, len(serie_modelo) // 3) + 1)
        diagnostico = acorr_ljungbox(
            ajuste.resid,
            lags=rezagos,
            model_df=p + q,
            return_df=True,
        )
        p_valores_validos = diagnostico["lb_pvalue"].dropna()
        if p_valores_validos.empty:
            continue
        p_valor_minimo = float(p_valores_validos.min())
        rezago_critico = int(p_valores_validos.idxmin())
        try:
            validacion_rmse_retorno, validacion_rmse_precio = _validar_pronostico_progresivo(
                serie_modelo,
                p,
                q,
                inicio_validacion,
                precios_log_modelo,
            )
        except (ValueError, np.linalg.LinAlgError):
            continue
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
                "p_valor_ljung_box_min": p_valor_minimo,
                "rezago_ljung_box_critico": rezago_critico,
                "residuos_blancos": p_valor_minimo >= nivel_significancia,
                "rmse_validacion_retorno": validacion_rmse_retorno,
                "rmse_validacion_precio": validacion_rmse_precio,
            }
        )
    if not filas:
        raise ValueError("No fue posible ajustar ningún modelo ARIMA candidato.")
    comparacion = pd.DataFrame(filas)
    comparacion = comparacion.sort_values(
        ["rmse_validacion_precio", "residuos_blancos", "aic"],
        ascending=[True, False, True],
    ).reset_index(drop=True)
    return ajustes, comparacion


def _validar_pronostico_progresivo(
    serie: pd.Series,
    p: int,
    q: int,
    inicio_validacion: int,
    log_precios: pd.Series | None,
) -> tuple[float, float]:
    """Calcula errores fuera de muestra con un único ajuste por modelo candidato."""
    entrenamiento = serie.iloc[:inicio_validacion].reset_index(drop=True)
    prueba = serie.iloc[inicio_validacion:].reset_index(drop=True)
    ajuste = ARIMA(
        entrenamiento,
        order=(p, 0, q),
        trend="c",
        enforce_stationarity=True,
        enforce_invertibility=True,
    ).fit()
    predicciones_array = np.asarray(ajuste.forecast(steps=len(prueba)), dtype=float)
    rmse_retorno = float(np.sqrt(np.mean((prueba.to_numpy() - predicciones_array) ** 2)))
    if log_precios is None:
        return rmse_retorno, rmse_retorno

    nivel_inicial = float(log_precios.iloc[inicio_validacion])
    precios_pronosticados = np.exp(nivel_inicial + np.cumsum(predicciones_array))
    precios_reales = np.exp(log_precios.iloc[inicio_validacion + 1 :].to_numpy())
    rmse_precio = float(np.sqrt(np.mean((precios_reales - precios_pronosticados) ** 2)))
    return rmse_retorno, rmse_precio


def analizar_arima(ticker: str, precios: pd.Series) -> ResultadoARIMA:
    """Ejecuta Box-Jenkins y selecciona por Ljung-Box y luego por AIC."""
    log_precios, retornos_log = preparar_serie(precios)
    diagnostico = calcular_diagnostico(retornos_log)
    ajustes, comparacion = ajustar_modelos(retornos_log, log_precios=log_precios)
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