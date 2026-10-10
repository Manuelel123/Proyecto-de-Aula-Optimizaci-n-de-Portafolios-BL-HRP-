"""Flujo Box-Jenkins del modelo ARIMA con precios de Yahoo Finance."""

import warnings
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import norm
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.stattools import acf, adfuller, kpss, pacf
from statsmodels.tools.sm_exceptions import ConvergenceWarning, EstimationWarning


_WARNINGS_CONVERGENCIA_ESPERADOS = (ConvergenceWarning, EstimationWarning)


@dataclass
class ResultadoARIMA:
    ticker: str
    precios: pd.Series
    log_precios: pd.Series
    retornos_log: pd.Series
    estacionariedad: pd.DataFrame
    acf: pd.DataFrame
    pacf: pd.DataFrame
    ljung_box: pd.DataFrame
    comparacion_modelos: pd.DataFrame
    modelo_seleccionado: str
    ajuste: object
    advertencias: list[str] = field(default_factory=list)


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
    """Aplica logaritmo natural y primera diferencia a los precios."""
    precios = precios.astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    if (precios <= 0).any():
        raise ValueError("Los precios deben ser positivos para aplicar logaritmos.")
    log_precios = np.log(precios).rename("log_precio")
    retornos_log = log_precios.diff().dropna().rename("retorno_log")
    return log_precios, retornos_log


def probar_estacionariedad(
    serie: pd.Series, nombre: str, alpha: float = 0.05
) -> dict:
    """Contrasta estacionariedad con ADF y KPSS, cuyas hipótesis son opuestas."""
    serie = serie.dropna()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        warnings.simplefilter("ignore", UserWarning)
        estadistico_adf, p_valor_adf, *_ = adfuller(serie, autolag="AIC")
        estadistico_kpss, p_valor_kpss, *_ = kpss(
            serie, regression="c", nlags="auto"
        )

    adf_rechaza_raiz_unitaria = p_valor_adf < alpha
    kpss_no_rechaza_estacionariedad = p_valor_kpss >= alpha
    if adf_rechaza_raiz_unitaria and kpss_no_rechaza_estacionariedad:
        conclusion = "estacionaria"
    elif not adf_rechaza_raiz_unitaria and not kpss_no_rechaza_estacionariedad:
        conclusion = "no estacionaria"
    else:
        conclusion = "ambigua"

    return {
        "serie": nombre,
        "n": len(serie),
        "adf_estadistico": float(estadistico_adf),
        "adf_p_valor": float(p_valor_adf),
        "kpss_estadistico": float(estadistico_kpss),
        "kpss_p_valor": float(p_valor_kpss),
        "conclusion": conclusion,
        "estacionaria": conclusion == "estacionaria",
    }


def calcular_diagnostico(
    retornos_log: pd.Series, max_rezagos: int = 36
) -> dict[str, pd.DataFrame]:
    """Calcula ACF, PACF y Ljung-Box para la identificación."""
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


def ajustar_modelos(
    retornos_log: pd.Series,
    max_p: int = 3,
    max_q: int = 3,
    max_rezagos_diagnostico: int = 36,
    nivel_significancia: float = 0.05,
    log_precios: pd.Series | None = None,
    tamano_validacion: int | None = None,
) -> tuple[dict[str, object], pd.DataFrame, list[str]]:
    """Busca modelos por grilla, exige residuos blancos y valida fuera de muestra."""
    if max_p < 0 or max_q < 0 or max_p + max_q == 0:
        raise ValueError("max_p o max_q deben permitir al menos un modelo.")
    if max_rezagos_diagnostico < 1 or not 0 < nivel_significancia < 1:
        raise ValueError(
            "El número de rezagos debe ser positivo y alpha debe estar entre 0 y 1."
        )

    advertencias = []
    ajustes = {}
    filas = []
    serie_modelo = retornos_log.reset_index(drop=True)
    precios_log_modelo = (
        log_precios.reset_index(drop=True) if log_precios is not None else None
    )
    if precios_log_modelo is not None and len(precios_log_modelo) != len(serie_modelo) + 1:
        raise ValueError("log_precios debe tener una observación más que retornos_log.")
    if tamano_validacion is None:
        tamano_validacion = min(20, max(10, len(serie_modelo) // 5))
    if tamano_validacion < 1 or tamano_validacion >= len(serie_modelo) - 20:
        raise ValueError(
            "El tamaño de validación no permite conservar suficientes datos de entrenamiento."
        )
    inicio_validacion = len(serie_modelo) - tamano_validacion

    for p in range(max_p + 1):
        for q in range(max_q + 1):
            nombre = f"ARIMA({p},0,{q})"
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", _WARNINGS_CONVERGENCIA_ESPERADOS)
                    ajuste = ARIMA(
                        serie_modelo,
                        order=(p, 0, q),
                        trend="c",
                        enforce_stationarity=True,
                        enforce_invertibility=True,
                    ).fit()
            except (ValueError, np.linalg.LinAlgError):
                continue

            rezagos = np.arange(
                1, min(max_rezagos_diagnostico, len(serie_modelo) // 3) + 1
            )
            try:
                diagnostico = acorr_ljungbox(
                    ajuste.resid, lags=rezagos, model_df=p + q, return_df=True
                )
            except ValueError:
                continue
            p_valores_validos = diagnostico["lb_pvalue"].dropna()
            if p_valores_validos.empty:
                continue
            p_valor_minimo = float(p_valores_validos.min())
            rezago_critico = int(p_valores_validos.idxmin())
            residuos_blancos = p_valor_minimo >= nivel_significancia

            try:
                rmse_validacion_retorno, rmse_validacion_precio = _validar_pronostico_horizonte(
                    serie_modelo, p, q, inicio_validacion, precios_log_modelo
                )
                rmse_rolling_retorno = _validar_rolling_un_paso(
                    serie_modelo, p, q, inicio_validacion
                )
            except (ValueError, np.linalg.LinAlgError):
                continue

            ajustes[nombre] = ajuste
            filas.append(
                {
                    "modelo": nombre,
                    "p": p,
                    "d": 0,
                    "q": q,
                    "tipo": (
                        "Ruido blanco + deriva"
                        if p == 0 and q == 0
                        else "AR"
                        if q == 0
                        else "MA"
                        if p == 0
                        else "ARMA"
                    ),
                    "aic": ajuste.aic,
                    "bic": ajuste.bic,
                    "hqic": ajuste.hqic,
                    "sse": float(np.sum(ajuste.resid**2)),
                    "p_valor_ljung_box_min": p_valor_minimo,
                    "rezago_ljung_box_critico": rezago_critico,
                    "residuos_blancos": residuos_blancos,
                    "rmse_rolling_retorno": rmse_rolling_retorno,
                    "rmse_validacion_retorno": rmse_validacion_retorno,
                    "rmse_validacion_precio": rmse_validacion_precio,
                }
            )

    if not filas:
        raise ValueError("No fue posible ajustar ningún modelo ARIMA candidato.")

    comparacion = pd.DataFrame(filas)
    if not comparacion["residuos_blancos"].any():
        advertencias.append(
            "Ningún modelo candidato produjo residuos compatibles con ruido blanco "
            f"según Ljung-Box (alpha = {nivel_significancia:.2f})."
        )
    comparacion = comparacion.sort_values(
        ["residuos_blancos", "aic", "rmse_validacion_precio"],
        ascending=[False, True, True],
    ).reset_index(drop=True)
    return ajustes, comparacion, advertencias


def _ajustar_validacion(serie: pd.Series, p: int, q: int) -> object:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", _WARNINGS_CONVERGENCIA_ESPERADOS)
        return ARIMA(
            serie,
            order=(p, 0, q),
            trend="c",
            enforce_stationarity=True,
            enforce_invertibility=True,
        ).fit()


def _validar_rolling_un_paso(
    serie: pd.Series, p: int, q: int, inicio_validacion: int
) -> float:
    entrenamiento = serie.iloc[:inicio_validacion].reset_index(drop=True)
    prueba = serie.iloc[inicio_validacion:].reset_index(drop=True)
    ajuste = _ajustar_validacion(entrenamiento, p, q)
    predicciones = []
    for valor_real in prueba.to_numpy():
        predicciones.append(float(np.asarray(ajuste.forecast(steps=1))[0]))
        ajuste = ajuste.append([valor_real], refit=False)
    return float(
        np.sqrt(np.mean((prueba.to_numpy() - np.asarray(predicciones, dtype=float)) ** 2))
    )


def _validar_pronostico_horizonte(
    serie: pd.Series,
    p: int,
    q: int,
    inicio_validacion: int,
    log_precios: pd.Series | None,
) -> tuple[float, float]:
    entrenamiento = serie.iloc[:inicio_validacion].reset_index(drop=True)
    prueba = serie.iloc[inicio_validacion:].reset_index(drop=True)
    ajuste = _ajustar_validacion(entrenamiento, p, q)
    predicciones = np.asarray(ajuste.forecast(steps=len(prueba)), dtype=float)
    rmse_retorno = float(
        np.sqrt(np.mean((prueba.to_numpy() - predicciones) ** 2))
    )
    if log_precios is None:
        return rmse_retorno, rmse_retorno
    nivel_inicial = float(log_precios.iloc[inicio_validacion])
    precios_pronosticados = np.exp(nivel_inicial + np.cumsum(predicciones))
    precios_reales = np.exp(log_precios.iloc[inicio_validacion + 1 :].to_numpy())
    rmse_precio = float(
        np.sqrt(np.mean((precios_reales - precios_pronosticados) ** 2))
    )
    return rmse_retorno, rmse_precio


def analizar_arima(
    ticker: str, precios: pd.Series, alpha_estacionariedad: float = 0.05
) -> ResultadoARIMA:
    """Ejecuta el flujo Box-Jenkins manteniendo la interfaz de la aplicación."""
    log_precios, retornos_log = preparar_serie(precios)
    prueba_niveles = probar_estacionariedad(
        log_precios, "log(precio) [d=0]", alpha_estacionariedad
    )
    prueba_retornos = probar_estacionariedad(
        retornos_log, "retorno log [d=1]", alpha_estacionariedad
    )
    estacionariedad = pd.DataFrame([prueba_niveles, prueba_retornos])
    advertencias = []
    if prueba_niveles["estacionaria"]:
        advertencias.append(
            "log(precio) ya resultó estacionaria en niveles; diferenciarla puede "
            "introducir estructura de media móvil artificial."
        )
    if not prueba_retornos["estacionaria"]:
        raise ValueError(
            "El retorno logarítmico (d=1) no pasó como estacionario: "
            f"ADF p-valor={prueba_retornos['adf_p_valor']:.4f}, "
            f"KPSS p-valor={prueba_retornos['kpss_p_valor']:.4f} "
            f"(conclusión: {prueba_retornos['conclusion']})."
        )

    diagnostico = calcular_diagnostico(retornos_log)
    ajustes, comparacion, advertencias_modelo = ajustar_modelos(
        retornos_log, log_precios=log_precios
    )
    advertencias.extend(advertencias_modelo)
    modelo_seleccionado = str(comparacion.iloc[0]["modelo"])
    if not bool(comparacion.iloc[0]["residuos_blancos"]):
        advertencias.append(
            f"El modelo seleccionado ({modelo_seleccionado}) no pasó Ljung-Box."
        )
    return ResultadoARIMA(
        ticker=ticker,
        precios=precios,
        log_precios=log_precios,
        retornos_log=retornos_log,
        estacionariedad=estacionariedad,
        acf=diagnostico["acf"],
        pacf=diagnostico["pacf"],
        ljung_box=diagnostico["ljung_box"],
        comparacion_modelos=comparacion,
        modelo_seleccionado=modelo_seleccionado,
        ajuste=ajustes[modelo_seleccionado],
        advertencias=advertencias,
    )


def pronosticar_precios(
    resultado: ResultadoARIMA, horizonte: int = 10, alpha: float = 0.05
) -> pd.DataFrame:
    """Pronostica retornos y propaga sus intervalos a los precios."""
    if horizonte < 1 or not 0 < alpha < 1:
        raise ValueError("El horizonte debe ser positivo y alpha debe estar entre 0 y 1.")
    prediccion = resultado.ajuste.get_prediction(
        start=len(resultado.retornos_log),
        end=len(resultado.retornos_log) + horizonte - 1,
        dynamic=False,
    )
    retornos = np.asarray(prediccion.predicted_mean, dtype=float)
    intervalo = np.asarray(prediccion.conf_int(alpha=alpha), dtype=float)
    retornos_inf, retornos_sup = intervalo[:, 0], intervalo[:, 1]
    ultimo_precio = resultado.precios.iloc[-1]
    indice = pd.bdate_range(
        resultado.precios.index[-1] + pd.Timedelta(days=1), periods=horizonte
    )
    return pd.DataFrame(
        {
            "retorno_log_pronosticado": retornos,
            "precio_pronosticado": ultimo_precio * np.exp(np.cumsum(retornos)),
            "precio_ic_inferior": ultimo_precio * np.exp(np.cumsum(retornos_inf)),
            "precio_ic_superior": ultimo_precio * np.exp(np.cumsum(retornos_sup)),
        },
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
            "precio_ic_inferior": pronosticos["precio_ic_inferior"].to_numpy(),
            "precio_ic_superior": pronosticos["precio_ic_superior"].to_numpy(),
        },
        index=test.index,
    )
    comparacion["error_porcentual"] = (
        (comparacion["precio_real"] - comparacion["precio_pronosticado"])
        / comparacion["precio_real"]
        * 100
    )
    return comparacion