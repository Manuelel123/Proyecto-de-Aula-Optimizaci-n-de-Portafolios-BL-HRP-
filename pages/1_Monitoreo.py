import math

import requests
import streamlit as st
import yfinance as yf

from optimizacion_portafolios.ui import (
    PORTAFOLIO_IMAGEN,
    configurar_pagina,
    mostrar_seguimiento,
)


TICKERS_OPCIONES = (
    "UEC",
    "HAI",
    "VG",
    "PR",
    "UUUU",
    "EQT",
    "DVN",
    "PTEN",
    "NESR",
    "APA",
    "CRK",
    "MGY",
    "AESI",
    "PUMP",
    "CRGY",
    "NOV",
    "SM",
    "LBRT",
    "AR",
    "WTTR",
    "NEXT",
    "NOG",
    "AM",
    "RES",
    "BTU",
    "RRC",
    "MUR",
    "CNX",
    "EGY",
    "TALO",
    "VNOM",
    "BKV",
    "TTI",
    "WKC",
    "PAA",
    "NE",
    "AROC",
    "HP",
)
ACTIVOS_OPCIONES = {ticker: ticker for ticker in TICKERS_OPCIONES}


class SesionYahoo(requests.Session):
    def request(self, *args, **kwargs):
        kwargs["timeout"] = 15
        return super().request(*args, **kwargs)


@st.cache_data(ttl=3600, show_spinner=False)
def obtener_informacion_fundamental(ticker: str) -> dict:
    return yf.Ticker(ticker, session=SesionYahoo()).get_info()


def formatear_valor(valor: object, tipo: str, moneda: str) -> str:
    if valor is None:
        return "N/D"
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if not math.isfinite(numero):
        return "N/D"
    if tipo == "moneda":
        return f"{moneda} {numero:,.2f}"
    if tipo == "miles de millones":
        return f"{moneda} {numero / 1_000_000_000:,.2f} mil millones"
    if tipo == "porcentaje":
        return f"{numero:.2%}"
    if tipo == "múltiplo":
        return f"{numero:.2f}x"
    return f"{numero:,.2f}"


def mostrar_analisis_fundamental(ticker: str) -> None:
    try:
        with st.spinner(f"Consultando información de {ticker}..."):
            informacion = obtener_informacion_fundamental(ticker)
    except Exception as error:
        st.error(f"No se pudo consultar Yahoo Finance para {ticker}: {error}")
        return

    if not informacion:
        st.warning(f"Yahoo Finance no devolvió información para {ticker}.")
        return

    nombre = informacion.get("longName") or informacion.get("shortName") or ticker
    sector = informacion.get("sector") or "Sector no disponible"
    industria = informacion.get("industry") or "Industria no disponible"
    pais = informacion.get("country") or "País no disponible"
    moneda = informacion.get("currency") or "USD"
    st.markdown(f"### {nombre} ({ticker})")
    st.caption(f"{sector} · {industria} · {pais}")

    precio = informacion.get("currentPrice") or informacion.get("regularMarketPrice")
    indicadores = [
        ("Precio actual", precio, "moneda"),
        ("Capitalización bursátil", informacion.get("marketCap"), "miles de millones"),
        ("P/E (últimos 12 meses)", informacion.get("trailingPE"), "múltiplo"),
        ("P/E proyectado", informacion.get("forwardPE"), "múltiplo"),
        ("Rendimiento por dividendo", informacion.get("dividendYield"), "porcentaje"),
    ]
    columnas = st.columns(len(indicadores))
    for columna, (etiqueta, valor, tipo) in zip(columnas, indicadores):
        columna.metric(etiqueta, formatear_valor(valor, tipo, moneda))

    minimo = informacion.get("fiftyTwoWeekLow")
    maximo = informacion.get("fiftyTwoWeekHigh")
    rango_anual = (
        f"{formatear_valor(minimo, 'moneda', moneda)} – "
        f"{formatear_valor(maximo, 'moneda', moneda)}"
        if minimo is not None and maximo is not None
        else "N/D"
    )
    st.metric("Rango de precio (52 semanas)", rango_anual)

    filas = [
        ("Crecimiento de ingresos", "revenueGrowth", "porcentaje"),
        ("Margen bruto", "grossMargins", "porcentaje"),
        ("Margen operativo", "operatingMargins", "porcentaje"),
        ("Margen neto", "profitMargins", "porcentaje"),
        ("Retorno sobre patrimonio (ROE)", "returnOnEquity", "porcentaje"),
        ("Deuda / capital", "debtToEquity", "porcentaje"),
        ("Razón corriente", "currentRatio", "múltiplo"),
        ("Ingresos", "totalRevenue", "miles de millones"),
        ("Utilidad neta", "netIncomeToCommon", "miles de millones"),
        ("Flujo de caja libre", "freeCashflow", "miles de millones"),
    ]
    st.subheader("Indicadores financieros")
    st.dataframe(
        [
            {
                "Indicador": etiqueta,
                "Valor": formatear_valor(informacion.get(clave), tipo, moneda),
            }
            for etiqueta, clave, tipo in filas
        ],
        hide_index=True,
        width="stretch",
    )

    resumen = informacion.get("longBusinessSummary")
    if resumen:
        st.subheader("Descripción de la compañía")
        st.write(resumen)


fecha_actual, fecha_inicio = configurar_pagina("Monitoreo de activos")

st.title("Monitoreo de activos e índices")
st.caption("Consulta precios, rendimiento y riesgo de los activos seleccionados")

tab_opciones, tab_portafolio = st.tabs(
    [
        "Activos monitoreo: Opciones Financieras",
        "Activos monitoreo: portafolio de inversión",
    ],
    default="Activos monitoreo: portafolio de inversión",
    key="monitoreo_vistas",
    on_change="rerun",
)

if tab_opciones.open:
    with tab_opciones:
        mostrar_seguimiento(
            "Opciones Financieras",
            "Precios, retornos y estadísticas clave de los activos de las imágenes.",
            "opciones_financieras",
            fecha_inicio,
            fecha_actual,
            ACTIVOS_OPCIONES,
            {},
            list(ACTIVOS_OPCIONES),
        )

if tab_portafolio.open:
    with tab_portafolio:
        tab_seguimiento, tab_fundamental = st.tabs(
            ["Seguimiento del portafolio", "Análisis fundamental"],
            default="Análisis fundamental",
            key="monitoreo_portafolio_subventanas",
            on_change="rerun",
        )

        if tab_seguimiento.open:
            mostrar_seguimiento(
                "Portafolio de inversión",
                "Consulta precios, retornos y riesgo de los activos del portafolio.",
                "portafolio_inversion",
                fecha_inicio,
                fecha_actual,
                PORTAFOLIO_IMAGEN,
                {},
                list(PORTAFOLIO_IMAGEN),
            )

        if tab_fundamental.open:
            st.subheader("Análisis fundamental")
            st.caption(
                "Indicadores financieros y de valoración consultados desde Yahoo Finance."
            )
            tickers_portafolio = list(PORTAFOLIO_IMAGEN)
            ticker = st.selectbox(
                "Compañía o instrumento",
                options=tickers_portafolio,
                index=tickers_portafolio.index("AAPL"),
                key="analisis_fundamental_ticker",
            )
            if st.button(
                "Consultar datos fundamentales",
                key="consultar_datos_fundamentales",
            ):
                st.session_state["ticker_fundamental_consultado"] = ticker
            if st.session_state.get("ticker_fundamental_consultado") != ticker:
                st.info("Selecciona un ticker y consulta sus datos para ver el análisis.")
            else:
                mostrar_analisis_fundamental(ticker)
