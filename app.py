import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import date, timedelta


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


def fecha_hace_dos_anios(fecha: date) -> date:
    try:
        return fecha.replace(year=fecha.year - 2)
    except ValueError:
        return fecha.replace(year=fecha.year - 2, day=28)


@st.cache_data(ttl=3600)
def descargar_precios(activos: list[str], fecha_inicio: date, fecha_fin: date):
    precios = yf.download(
        activos,
        start=fecha_inicio,
        end=fecha_fin + timedelta(days=1),
        interval="1d",
        auto_adjust=False,
        progress=False,
    )["Close"]
    precios.index.name = "Fecha"
    return precios


def calcular_estadisticas(precios: pd.DataFrame) -> pd.DataFrame:
    retornos = precios.pct_change(fill_method=None).dropna(how="all")
    estadisticas = pd.DataFrame(index=precios.columns)
    estadisticas["Último precio"] = precios.ffill().iloc[-1]
    estadisticas["Retorno total"] = (precios.ffill().iloc[-1] / precios.bfill().iloc[0]) - 1
    estadisticas["Retorno anualizado"] = (1 + estadisticas["Retorno total"]) ** (252 / len(retornos)) - 1
    estadisticas["Volatilidad anualizada"] = retornos.std() * (252**0.5)
    estadisticas["Máxima caída"] = (precios.ffill() / precios.ffill().cummax() - 1).min()
    return estadisticas


st.set_page_config(page_title="Monitor de activos", page_icon="📈", layout="wide")
fecha_actual = date.today()
fecha_inicio = fecha_hace_dos_anios(fecha_actual)

with st.sidebar:
    st.header("Módulos")
    modulo = st.radio(
        "Selecciona una sección",
        ["Monitoreo", "Optimización BL & HRP"],
    )

if modulo == "Optimización BL & HRP":
    st.title("Optimización de portafolios")
    st.info("Este módulo se incorporará aquí próximamente.")
    st.stop()

st.title("Monitoreo de activos e índices")
st.caption("Precios diarios, retornos históricos y estadísticas de riesgo")

with st.sidebar:
    st.divider()
    st.header("Configuración del monitoreo")
    instrumentos = st.multiselect(
        "Activos e índices",
        options=[*ACTIVOS, *INDICES],
        default=list(ACTIVOS)[:2],
    )
    tickers_personalizados = st.text_input(
        "Otros tickers",
        placeholder="Ejemplo: AMZN, ^VIX",
        help="Sepáralos por comas usando los símbolos de Yahoo Finance.",
    )
    fecha_inicio = st.date_input(
        "Fecha inicial",
        value=fecha_inicio,
        max_value=fecha_actual,
    )
    st.date_input("Fecha final", value=fecha_actual, disabled=True)

tickers = [ACTIVOS.get(item, INDICES.get(item, item)) for item in instrumentos]
tickers.extend(
    ticker.strip().upper()
    for ticker in tickers_personalizados.split(",")
    if ticker.strip()
)
tickers = list(dict.fromkeys(tickers))

if not tickers:
    st.warning("Selecciona al menos un activo.")
    st.stop()

with st.spinner("Descargando precios..."):
    precios = descargar_precios(tuple(tickers), fecha_inicio, fecha_actual)

if precios.empty:
    st.error("No se encontraron precios para el periodo seleccionado.")
    st.stop()

estadisticas = calcular_estadisticas(precios)
estadisticas.index.name = "Ticker"

tab_precios, tab_retorno, tab_estadisticas = st.tabs(
    ["Precios", "Retornos históricos", "Estadísticas clave"]
)

with tab_precios:
    st.subheader("Evolución de los precios")
    st.line_chart(precios, y_label="Precio de cierre", x_label="Fecha")
    st.dataframe(precios, width="stretch")

with tab_retorno:
    retornos = precios.pct_change(fill_method=None).dropna(how="all")
    st.subheader("Retornos diarios")
    st.line_chart(retornos, y_label="Retorno", x_label="Fecha")
    st.dataframe(retornos, width="stretch")

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

st.caption("La volatilidad y los retornos anualizados usan 252 días de mercado.")

st.caption(
    f"Periodo: {precios.index.min().date()} a {precios.index.max().date()} | "
    f"Filas: {len(precios):,}"
)
