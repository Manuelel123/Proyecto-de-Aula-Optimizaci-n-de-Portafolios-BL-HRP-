import streamlit as st
import yfinance as yf
from datetime import date, timedelta


ACTIVOS = ["AAPL", "MSFT", "GOOGL", "BTC-USD"]


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


st.set_page_config(page_title="Precios del portafolio", page_icon="📈", layout="wide")
st.title("Precios históricos del portafolio")
st.caption("Datos diarios descargados desde Yahoo Finance")

fecha_actual = date.today()
fecha_inicio = fecha_hace_dos_anios(fecha_actual)

with st.sidebar:
    st.header("Configuración")
    activos_seleccionados = st.multiselect(
        "Activos",
        options=ACTIVOS,
        default=ACTIVOS,
    )
    fecha_inicio = st.date_input(
        "Fecha inicial",
        value=fecha_inicio,
        max_value=fecha_actual,
    )
    st.date_input("Fecha final", value=fecha_actual, disabled=True)

if not activos_seleccionados:
    st.warning("Selecciona al menos un activo.")
    st.stop()

with st.spinner("Descargando precios..."):
    precios = descargar_precios(tuple(activos_seleccionados), fecha_inicio, fecha_actual)

if precios.empty:
    st.error("No se encontraron precios para el periodo seleccionado.")
    st.stop()

st.subheader("Evolución de los precios")
st.line_chart(precios, y_label="Precio de cierre", x_label="Fecha")

st.subheader("Datos descargados")
st.dataframe(precios, use_container_width=True)

st.caption(
    f"Periodo: {precios.index.min().date()} a {precios.index.max().date()} | "
    f"Filas: {len(precios):,}"
)
