from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import quantstats as qs
import streamlit as st
import yfinance as yf
from pypfopt import HRPOpt, expected_returns
from scipy.cluster.hierarchy import to_tree

from optimizacion_portafolios.arima import descargar_precios_yahoo


ACTIVOS = {
    "Apple (AAPL)": "AAPL",
    "Microsoft (MSFT)": "MSFT",
    "Alphabet (GOOGL)": "GOOGL",
    "Bitcoin (BTC-USD)": "BTC-USD",
}
INDICES = {
    "SALUD": "XLV",
    "FINANCIERO": "XLF",
    "TÉCNOLOGICO": "XLK",
    "ENERGÍA": "XLE",
}
CRIPTOMONEDAS = {
    "Bitcoin (BTC-USD)": "BTC-USD",
    "Ethereum (ETH-USD)": "ETH-USD",
    "BNB (BNB-USD)": "BNB-USD",
    "XRP (XRP-USD)": "XRP-USD",
}
COMMODITIES = {
    "Oro (GC=F)": "GC=F",
    "Plata (SI=F)": "SI=F",
    "Petróleo WTI (CL=F)": "CL=F",
    "Gas natural (NG=F)": "NG=F",
    "Cobre (HG=F)": "HG=F",
}
PORTAFOLIO_IMAGEN = {
    "MSFT": "MSFT",
    "GLD": "GLD",
    "AAPL": "AAPL",
    "LULU": "LULU",
    "DOGEUSD": "DOGE-USD",
    "AVGO": "AVGO",
    "BABA": "BABA",
    "HOOD": "HOOD",
    "V": "V",
    "COIN": "COIN",
    "BRENT": "BZ=F",
    "AXP": "AXP",
    "AMD": "AMD",
    "TKO": "TKO",
    "GILD": "GILD",
    "BAC": "BAC",
    "XLU": "XLU",
    "XLV": "XLV",
    "JNJ": "JNJ",
    "LLY": "LLY",
    "ELV": "ELV",
    "HIMS": "HIMS",
}
PORTAFOLIO_COLOMBIA = {
    ticker: f"{ticker}.CL"
    for ticker in (
        "PFCIBEST",
        "CIBEST",
        "ISA",
        "ECOPETROL",
        "PFGRUPSURA",
        "GRUPOSURA",
        "GRUPOARGOS",
        "CEMARGOS",
        "PFGRUPOARG",
        "PFDAVVIGR",
        "PFAVAL",
        "CELSIA",
        "PEI",
        "CORFICOLOCF",
        "MINEROS",
        "GRUBOLIVAR",
        "BOGOTA",
        "TERPEL",
        "EXITO",
        "PROMIGAS",
        "PFCORFICOL",
    )
}
ACTIVOS_HRP = {
    **ACTIVOS,
    **INDICES,
    **CRIPTOMONEDAS,
    **COMMODITIES,
    **PORTAFOLIO_IMAGEN,
    **PORTAFOLIO_COLOMBIA,
}
BENCHMARKS = {
    "S&P 500 (SPY)": "SPY",
    "Nasdaq 100 (QQQ)": "QQQ",
    "Dow Jones (DIA)": "DIA",
    "Russell 2000 (IWM)": "IWM",
    "Mercado global (VT)": "VT",
    "Índice S&P 500 (^GSPC)": "^GSPC",
    "Bitcoin (BTC-USD)": "BTC-USD",
}


def fecha_hace_un_anio(fecha: date) -> date:
    try:
        return fecha.replace(year=fecha.year - 1)
    except ValueError:
        return fecha.replace(year=fecha.year - 1, day=28)


@st.cache_data(ttl=3600)
def descargar_precios(activos: tuple[str, ...], fecha_inicio: date, fecha_fin: date):
    precios = yf.download(
        activos,
        start=fecha_inicio,
        end=fecha_fin + timedelta(days=1),
        interval="1d",
        auto_adjust=True,
        progress=False,
    )["Close"]
    precios.index.name = "Fecha"
    return precios


@st.cache_data(ttl=3600)
def descargar_serie_arima(ticker: str, fecha_inicio: date, fecha_fin: date) -> pd.Series:
    return descargar_precios_yahoo(ticker, fecha_inicio, fecha_fin)


def calcular_estadisticas(precios: pd.DataFrame) -> pd.DataFrame:
    retornos = precios.pct_change(fill_method=None).dropna(how="all")
    estadisticas = pd.DataFrame(index=precios.columns)
    estadisticas["Último precio"] = precios.ffill().iloc[-1]
    estadisticas["Retorno total"] = (precios.ffill().iloc[-1] / precios.bfill().iloc[0]) - 1
    estadisticas["Retorno anualizado"] = (1 + estadisticas["Retorno total"]) ** (252 / len(retornos)) - 1
    estadisticas["Volatilidad anualizada"] = retornos.std() * (252**0.5)
    estadisticas["Máxima caída"] = (precios.ffill() / precios.ffill().cummax() - 1).min()
    return estadisticas


def calcular_retornos_esperados(precios: pd.DataFrame) -> pd.DataFrame:
    precios = precios.dropna(axis="columns", how="all")
    retorno_historico = expected_returns.mean_historical_return(
        precios,
        returns_data=False,
        compounding=True,
        frequency=252,
    )
    retorno_ema = expected_returns.ema_historical_return(
        precios,
        returns_data=False,
        compounding=True,
        span=500,
        frequency=252,
    )
    return pd.DataFrame(
        {
            "Retorno histórico esperado": retorno_historico,
            "Retorno esperado EMA": retorno_ema,
        }
    )


def calcular_hrp(retornos: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    optimizador = HRPOpt(returns=retornos)
    pesos = pd.Series(
        optimizador.optimize(linkage_method="single"),
        dtype=float,
    )
    correlacion = retornos.corr()
    orden = retornos.columns[to_tree(optimizador.clusters).pre_order()].tolist()
    return pesos.sort_values(ascending=False), correlacion.loc[orden, orden]


def calcular_volatilidad_mensual(retornos: pd.DataFrame) -> pd.DataFrame:
    volatilidad_mensual = retornos.resample("ME").std() * np.sqrt(21)
    return volatilidad_mensual.dropna(how="all")


def calcular_metricas_quantstats(retornos_portafolio: pd.Series) -> pd.DataFrame:
    metricas = {
        "Retorno anual compuesto": qs.stats.cagr(retornos_portafolio),
        "Volatilidad anualizada": qs.stats.volatility(retornos_portafolio),
        "Ratio de Sharpe": qs.stats.sharpe(retornos_portafolio),
        "Ratio de Sortino": qs.stats.sortino(retornos_portafolio),
        "Máxima caída": qs.stats.max_drawdown(retornos_portafolio),
        "Ratio de Calmar": qs.stats.calmar(retornos_portafolio),
        "Porcentaje de días positivos": qs.stats.win_rate(retornos_portafolio),
    }
    return pd.DataFrame.from_dict(metricas, orient="index", columns=["Valor"])


def generar_tearsheet_quantstats(
    retornos_portafolio: pd.Series, benchmark: pd.Series
) -> bytes:
    with TemporaryDirectory() as directorio_temporal:
        archivo_reporte = Path(directorio_temporal) / "reporte.html"
        qs.reports.html(
            retornos_portafolio,
            benchmark=benchmark,
            output=str(archivo_reporte),
            title="Strategy Tearsheet - Portafolio HRP",
            download_filename="reporte_quantstats_hrp.html",
        )
        return archivo_reporte.read_bytes()


def calcular_contribuciones_hrp(
    retornos: pd.DataFrame, pesos: pd.Series
) -> tuple[pd.DataFrame, pd.DataFrame]:
    contribuciones_diarias = retornos.mul(pesos, axis="columns")
    resumen = pd.DataFrame(index=pesos.index)
    resumen["Peso HRP"] = pesos
    resumen["Retorno anualizado"] = retornos.mean() * 252
    resumen["Volatilidad anualizada"] = retornos.std() * np.sqrt(252)
    resumen["Contribución anualizada"] = contribuciones_diarias.mean() * 252
    retorno_total = resumen["Contribución anualizada"].sum()
    resumen["Participación del retorno"] = (
        resumen["Contribución anualizada"] / retorno_total
        if retorno_total != 0
        else 0
    )
    return contribuciones_diarias, resumen


def mostrar_seguimiento(
    titulo: str,
    descripcion: str,
    clave: str,
    fecha_inicio: date,
    fecha_actual: date,
    catalogo_activos: dict[str, str],
    catalogo_indices: dict[str, str],
    activos_predeterminados: list[str],
    permitir_filtro_tipo: bool = False,
) -> None:
    st.subheader(titulo)
    st.caption(descripcion)
    catalogo = {**catalogo_activos, **catalogo_indices}
    if permitir_filtro_tipo:
        tipo_instrumento = st.radio(
            "Filtrar instrumentos por tipo",
            ["Todos", "Activos", "Índices"],
            horizontal=True,
            key=f"{clave}_tipo_instrumento",
        )
        if tipo_instrumento == "Activos":
            opciones = list(catalogo_activos)
        elif tipo_instrumento == "Índices":
            opciones = list(catalogo_indices)
        else:
            opciones = list(catalogo)
        valores_predeterminados = (
            activos_predeterminados if tipo_instrumento == "Todos" else opciones[:2]
        )
        clave_instrumentos = f"{clave}_instrumentos_{tipo_instrumento.lower()}"
    else:
        opciones = list(catalogo)
        valores_predeterminados = activos_predeterminados
        clave_instrumentos = f"{clave}_instrumentos"
    instrumentos = st.multiselect(
        "Instrumentos disponibles",
        options=opciones,
        default=valores_predeterminados,
        key=clave_instrumentos,
    )
    tickers_personalizados = st.text_input(
        "Otros tickers",
        placeholder="Ejemplo: AMZN, ^VIX",
        help="Sepáralos por comas usando los símbolos de Yahoo Finance.",
        key=f"{clave}_tickers_personalizados",
    )
    col_fecha_inicio, col_fecha_final = st.columns(2)
    with col_fecha_inicio:
        fecha_inicio_seleccionada = st.date_input(
            "Fecha inicial",
            value=fecha_inicio,
            max_value=fecha_actual,
            key=f"{clave}_fecha_inicio",
        )
    with col_fecha_final:
        st.date_input(
            "Fecha final",
            value=fecha_actual,
            disabled=True,
            key=f"{clave}_fecha_final",
        )
    tickers = [catalogo.get(item, item) for item in instrumentos]
    tickers.extend(
        ticker.strip().upper()
        for ticker in tickers_personalizados.split(",")
        if ticker.strip()
    )
    tickers = list(dict.fromkeys(tickers))
    if not tickers:
        st.warning("Selecciona al menos un activo o índice para continuar.")
        return
    with st.spinner("Descargando precios..."):
        precios = descargar_precios(tuple(tickers), fecha_inicio_seleccionada, fecha_actual)
    precios = precios.dropna(axis="columns", how="all")
    if precios.empty or precios.shape[1] == 0:
        st.error("No se encontraron precios para el periodo seleccionado.")
        return
    estadisticas = calcular_estadisticas(precios)
    estadisticas.index.name = "Ticker"
    retornos = precios.pct_change(fill_method=None).dropna(how="all")
    retornos_esperados = calcular_retornos_esperados(precios)
    volatilidad_mensual = calcular_volatilidad_mensual(retornos)
    volatilidad_actual = retornos.tail(21).std() * np.sqrt(21)
    tab_precios, tab_retorno, tab_esperados, tab_volatilidad, tab_estadisticas = st.tabs(
        [
            "Precios",
            "Retornos históricos",
            "Retornos esperados",
            "Volatilidad",
            "Estadísticas clave",
        ]
    )
    with tab_precios:
        st.subheader("Evolución de los precios")
        st.line_chart(precios, y_label="Precio de cierre", x_label="Fecha")
        st.dataframe(precios, width="stretch")
    with tab_retorno:
        st.subheader("Retornos diarios")
        st.line_chart(retornos, y_label="Retorno", x_label="Fecha")
        st.dataframe(retornos, width="stretch")
    with tab_esperados:
        st.subheader("Retornos esperados anualizados")
        retornos_esperados.index.name = "Ticker"
        st.dataframe(
            retornos_esperados.style.format("{:.2%}"),
            width="stretch",
        )
        st.caption(
            "Estimaciones geométricas anualizadas con precios ajustados y frecuencia "
            "de 252 días. EMA usa span de 500 días."
        )
    with tab_volatilidad:
        st.subheader("Distribución de volatilidad mensual")
        tabla_volatilidad = volatilidad_actual.rename(
            "Volatilidad mensual actual"
        ).to_frame()
        tabla_volatilidad.index.name = "Ticker"
        st.dataframe(
            tabla_volatilidad.style.format("{:.2%}"),
            width="stretch",
        )
        activos_con_volatilidad = [
            ticker
            for ticker in volatilidad_mensual.columns
            if volatilidad_mensual[ticker].notna().any()
        ]
        if activos_con_volatilidad:
            ticker = st.selectbox(
                "Activo para el histograma",
                options=activos_con_volatilidad,
                key=f"{clave}_activo_histograma_volatilidad",
            )
            valores_volatilidad = volatilidad_mensual[ticker].dropna()
            valor_actual = volatilidad_actual.get(ticker, np.nan)
            figura, eje = plt.subplots()
            eje.hist(
                valores_volatilidad * 100,
                bins="auto",
                color="#287D6B",
                edgecolor="white",
            )
            if pd.notna(valor_actual):
                eje.axvline(
                    valor_actual * 100,
                    color="#D26045",
                    linestyle="--",
                    label=f"Actual: {valor_actual:.2%}",
                )
                eje.legend()
            eje.set_xlabel("Volatilidad mensual (%)")
            eje.set_ylabel("Número de meses")
            eje.set_title(f"Histograma de volatilidad: {ticker}")
            st.pyplot(figura)
            plt.close(figura)
            st.metric(
                "Volatilidad mensual actual (últimos 21 días)",
                f"{valor_actual:.2%}" if pd.notna(valor_actual) else "N/D",
            )
        else:
            st.info("No hay suficientes datos para calcular volatilidad mensual.")
    with tab_estadisticas:
        st.subheader("Resumen de rendimiento y riesgo")
        st.dataframe(
            estadisticas.style.format(
                {
                    "Último precio": "{:.2f}",
                    "Retorno total": "{:.2%}",
                    "Retorno anualizado": "{:.2%}",
                    "Volatilidad anualizada": "{:.2%}",
                    "Máxima caída": "{:.2%}",
                }
            ),
            width="stretch",
        )
    st.caption(
        "Los retornos esperados usan 252 días de mercado; la volatilidad mensual "
        "usa la desviación diaria del periodo multiplicada por √21."
    )
    st.caption(
        f"Periodo: {precios.index.min().date()} a {precios.index.max().date()} | "
        f"Filas: {len(precios):,}"
    )


def configurar_pagina(titulo: str) -> tuple[date, date]:
    st.set_page_config(page_title=titulo, page_icon="📊", layout="wide")
    fecha_actual = date.today()
    return fecha_actual, fecha_hace_un_anio(fecha_actual)
