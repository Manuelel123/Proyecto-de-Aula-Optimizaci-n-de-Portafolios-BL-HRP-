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
    "XLV": "XLV",
    "XLE": "XLE",
    "XLI": "XLI",
    "XLF": "XLF",
    "AAPL": "AAPL",
    "JNJ": "JNJ",
    "GILD": "GILD",
    "GLD": "GLD",
    "V": "V",
    "BAC": "BAC",
    "ELV": "ELV",
    "MSFT": "MSFT",
    "LLY": "LLY",
    "XLK": "XLK",
    "XLU": "XLU",
    "TSLA": "TSLA",
    "HOOD": "HOOD",
    "HIMS": "HIMS",
    "COIN": "COIN",
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


@st.cache_data(ttl=3600, show_spinner=False)
def descargar_tipo_cambio_yahoo(moneda: str) -> float:
    moneda = moneda.upper()
    if moneda == "USD":
        return 1.0

    ultimo_error = None
    for ticker_fx, invertir in (
        (f"{moneda}USD=X", False),
        (f"USD{moneda}=X", True),
    ):
        try:
            precios_fx = yf.Ticker(ticker_fx).history(period="5d")["Close"].dropna()
        except Exception as error:
            ultimo_error = error
            continue
        if precios_fx.empty:
            continue
        tasa = float(precios_fx.iloc[-1])
        if not np.isfinite(tasa) or tasa <= 0:
            continue
        return 1 / tasa if invertir else tasa

    raise ValueError(
        f"No se pudo obtener el tipo de cambio de {moneda} a USD. {ultimo_error or ''}"
    )


@st.cache_data(ttl=3600, show_spinner=False)
def descargar_capitalizacion_yahoo(ticker: str) -> float | None:
    informacion = yf.Ticker(ticker).get_info()
    capitalizacion = informacion.get("marketCap")
    if capitalizacion is None and informacion.get("quoteType") in {
        "ETF",
        "MUTUALFUND",
    }:
        capitalizacion = informacion.get("totalAssets")
    if capitalizacion is None:
        return None
    try:
        capitalizacion = float(capitalizacion)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(capitalizacion) or capitalizacion <= 0:
        return None
    moneda = informacion.get("currency")
    if not moneda:
        return None
    moneda = str(moneda).strip()
    if moneda in {"GBp", "GBX"}:
        capitalizacion /= 100
        moneda = "GBP"
    return capitalizacion * descargar_tipo_cambio_yahoo(moneda)


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


PERIODOS_VOLATILIDAD = {
    "Semanal (7 días)": "W-FRI",
    "Quincenal (15 días)": "15D",
    "Mensual": "ME",
    "45 días": "45D",
    "Dos meses": "2ME",
}


def calcular_volatilidades_historicas(
    retornos: pd.DataFrame,
    periodo: str,
    fecha_inicio: date | pd.Timestamp,
    fecha_fin: date | pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    regla = PERIODOS_VOLATILIDAD[periodo]
    inicio = pd.Timestamp(fecha_inicio).normalize()
    fin = pd.Timestamp(fecha_fin).normalize()
    if regla in {"15D", "45D"}:
        dias = int(regla.removesuffix("D"))
        indices_periodo = (retornos.index.normalize() - inicio).days // dias
        grupos = retornos.groupby(indices_periodo)
        desviacion_diaria = grupos.std(ddof=1)
        observaciones = grupos.count()
        etiquetas = inicio + pd.to_timedelta(desviacion_diaria.index * dias, unit="D")
        desviacion_diaria.index = etiquetas
        observaciones.index = etiquetas
    else:
        grupos = retornos.resample(regla)
        desviacion_diaria = grupos.std(ddof=1)
        observaciones = grupos.count()
    volatilidad_periodo = (desviacion_diaria * np.sqrt(observaciones)).where(
        observaciones >= 2
    ).dropna(how="all")
    volatilidad_anualizada = (desviacion_diaria * np.sqrt(252)).where(
        observaciones >= 2
    ).dropna(how="all")
    periodos = volatilidad_anualizada.index

    if regla == "W-FRI":
        inicio_periodo = periodos - pd.Timedelta(days=6)
        completos = (inicio_periodo >= inicio) & (periodos <= fin)
    elif regla in {"15D", "45D"}:
        dias = int(regla.removesuffix("D"))
        fin_periodo = periodos + pd.Timedelta(days=dias - 1)
        completos = (periodos >= inicio) & (fin_periodo <= fin)
    elif regla == "ME":
        inicio_periodo = periodos.to_period("M").to_timestamp(how="start")
        completos = (inicio_periodo >= inicio) & (periodos <= fin)
    else:
        inicio_periodo = (periodos.to_period("M") - 1).to_timestamp(how="start")
        completos = (inicio_periodo >= inicio) & (periodos <= fin)

    return volatilidad_periodo.loc[completos], volatilidad_anualizada.loc[completos]


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
    retornos_portafolio: pd.Series,
    benchmark: pd.Series,
    titulo: str = "Portafolio HRP",
    nombre_archivo: str = "reporte_quantstats_hrp.html",
) -> bytes:
    with TemporaryDirectory() as directorio_temporal:
        archivo_reporte = Path(directorio_temporal) / "reporte.html"
        qs.reports.html(
            retornos_portafolio,
            benchmark=benchmark,
            output=str(archivo_reporte),
            title=f"Strategy Tearsheet - {titulo}",
            download_filename=nombre_archivo,
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
    histogramas_todos_activos: bool = False,
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
        if histogramas_todos_activos:
            st.subheader("Volatilidad histórica anualizada por activo")
            periodo = st.selectbox(
                "Periodo de cálculo",
                options=list(PERIODOS_VOLATILIDAD),
                key=f"{clave}_periodo_histograma_volatilidad",
            )
            volatilidad_periodo, volatilidad_anualizada = calcular_volatilidades_historicas(
                retornos,
                periodo,
                precios.index.min(),
                precios.index.max(),
            )
            activos_con_volatilidad = [
                ticker
                for ticker in volatilidad_anualizada.columns
                if volatilidad_anualizada[ticker].notna().any()
            ]
            activos_sin_datos = sorted(set(tickers) - set(precios.columns))
            if activos_sin_datos:
                st.warning(
                    "Yahoo Finance no devolvió precios para: "
                    + ", ".join(activos_sin_datos)
                )
            if activos_con_volatilidad:
                st.caption(
                    f"Cada estimación usa retornos diarios dentro de un periodo "
                    f"completo de {periodo.lower()}: VH del periodo con √N sesiones "
                    "observadas y anualizada con √252. "
                    f"El histograma muestra {len(volatilidad_anualizada)} periodos "
                    "completos; se excluyen ventanas parciales. La línea roja "
                    "marca el periodo completo más reciente."
                )
                volatilidad_actual = pd.DataFrame(
                    {
                        f"Volatilidad {periodo.lower()}": volatilidad_periodo.iloc[-1],
                        "Volatilidad anualizada": volatilidad_anualizada.iloc[-1],
                    }
                )
                volatilidad_actual.index.name = "Ticker"
                st.dataframe(
                    volatilidad_actual.style.format("{:.2%}"),
                    width="stretch",
                )

                columnas = 3
                filas = (len(activos_con_volatilidad) + columnas - 1) // columnas
                figura, ejes = plt.subplots(
                    filas,
                    columnas,
                    figsize=(18, 3.6 * filas),
                    squeeze=False,
                )
                for eje, ticker in zip(ejes.flat, activos_con_volatilidad):
                    valores_anualizados = volatilidad_anualizada[ticker].dropna()
                    ultimo_periodo = volatilidad_periodo.loc[
                        valores_anualizados.index[-1], ticker
                    ]
                    ultimo_anualizado = valores_anualizados.iloc[-1]
                    valores = valores_anualizados * 100
                    eje.hist(
                        valores,
                        bins="auto",
                        color="#287D6B",
                        edgecolor="white",
                    )
                    eje.axvline(
                        valores.iloc[-1],
                        color="#D26045",
                        linestyle="--",
                        linewidth=1,
                    )
                    eje.text(
                        0.97,
                        0.95,
                        f"VH {periodo.lower()}: {ultimo_periodo:.2%}\n"
                        f"VH anualizada: {ultimo_anualizado:.2%}",
                        transform=eje.transAxes,
                        ha="right",
                        va="top",
                        fontsize=10,
                        fontweight="bold",
                        color="#173A34",
                        bbox={
                            "boxstyle": "round,pad=0.35",
                            "facecolor": "white",
                            "edgecolor": "#287D6B",
                            "alpha": 0.9,
                        },
                    )
                    eje.set_title(f"{ticker} (n={len(valores)})")
                    eje.set_xlabel("Volatilidad anualizada (%)")
                    eje.set_ylabel("Frecuencia")
                for eje in list(ejes.flat)[len(activos_con_volatilidad) :]:
                    eje.set_visible(False)
                figura.tight_layout()
                st.pyplot(figura)
                plt.close(figura)
                with st.expander("Detalle de volatilidad por periodo"):
                    st.dataframe(
                        pd.concat(
                            {
                                f"VH {periodo.lower()}": volatilidad_periodo,
                                "VH anualizada": volatilidad_anualizada,
                            },
                            axis="columns",
                        ).style.format("{:.2%}"),
                        width="stretch",
                    )
            else:
                st.info(
                    "No hay periodos completos con suficientes datos para calcular "
                    "la volatilidad seleccionada. Amplía el rango de fechas."
                )
        else:
            st.subheader("Distribución de volatilidad mensual")
            volatilidad_mensual = calcular_volatilidad_mensual(retornos)
            volatilidad_actual = retornos.tail(21).std() * np.sqrt(21)
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
    if not histogramas_todos_activos:
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
