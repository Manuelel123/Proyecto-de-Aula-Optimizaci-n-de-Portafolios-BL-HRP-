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


def mostrar_seguimiento(
    titulo: str,
    descripcion: str,
    clave: str,
    fecha_inicio: date,
    fecha_actual: date,
    activos_predeterminados: list[str],
) -> None:
    st.subheader(titulo)
    st.caption(descripcion)

    instrumentos = st.multiselect(
        "Activos e índices",
        options=[*ACTIVOS, *INDICES],
        default=activos_predeterminados,
        key=f"{clave}_instrumentos",
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

    tickers = [ACTIVOS.get(item, INDICES.get(item, item)) for item in instrumentos]
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
        precios = descargar_precios(
            tuple(tickers), fecha_inicio_seleccionada, fecha_actual
        )

    if precios.empty:
        st.error("No se encontraron precios para el periodo seleccionado.")
        return

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


st.set_page_config(
    page_title="Dashboard de portafolios",
    page_icon="📊",
    layout="wide",
)
fecha_actual = date.today()
fecha_inicio = fecha_hace_dos_anios(fecha_actual)

if "pagina" not in st.session_state:
    st.session_state.pagina = "Dashboard"

with st.sidebar:
    st.header("Navegación")
    if st.button("⌂  Dashboard", use_container_width=True):
        st.session_state.pagina = "Dashboard"
        st.rerun()
    modulo = st.radio(
        "Ir directamente a",
        ["Monitoreo", "Optimización BL & HRP"],
        index=None,
    )
    if modulo:
        st.session_state.pagina = modulo

pagina = st.session_state.pagina

if pagina == "Dashboard":
    st.title("Dashboard de optimización de portafolios")
    st.caption("Selecciona el módulo con el que quieres trabajar")

    st.divider()
    col_monitoreo, col_optimizacion = st.columns(2, gap="large")

    with col_monitoreo:
        st.subheader("📈 Monitoreo de activos")
        st.write(
            "Consulta precios, retornos históricos y estadísticas de riesgo "
            "para activos e índices."
        )
        if st.button("Abrir monitoreo", type="primary", use_container_width=True):
            st.session_state.pagina = "Monitoreo"
            st.rerun()

    with col_optimizacion:
        st.subheader("⚖️ Optimización BL & HRP")
        st.write(
            "Construye portafolios usando los modelos Black-Litterman y "
            "Paridad por Riesgo Jerárquico."
        )
        if st.button(
            "Abrir optimización",
            type="primary",
            use_container_width=True,
        ):
            st.session_state.pagina = "Optimización BL & HRP"
            st.rerun()

    st.divider()
    st.subheader("Resumen")
    metrica_activos, metrica_modelos = st.columns(2)
    metrica_activos.metric("Activos disponibles", len(ACTIVOS) + len(INDICES))
    metrica_modelos.metric("Modelos de optimización", 2)
    st.stop()

if pagina == "Optimización BL & HRP":
    st.title("Optimización de portafolios")
    st.info("Este módulo se incorporará aquí próximamente.")
    st.stop()

st.title("Monitoreo de activos e índices")
st.caption("Selecciona los activos de cada modelo y consulta sus precios, retornos y riesgos")

tab_black_litterman, tab_hrp = st.tabs(
    ["Activos para Black-Litterman", "Activos de seguimiento HRP"]
)

with tab_black_litterman:
    mostrar_seguimiento(
        "Activos e índices para Black-Litterman",
        "Define el universo de activos e índices que utilizará el modelo Black-Litterman.",
        "black_litterman",
        fecha_inicio,
        fecha_actual,
        list(ACTIVOS)[:2],
    )

with tab_hrp:
    mostrar_seguimiento(
        "Activos de seguimiento para HRP",
        "Selecciona los activos que se seguirán para construir el portafolio HRP.",
        "hrp",
        fecha_inicio,
        fecha_actual,
        list(ACTIVOS)[:2],
    )
