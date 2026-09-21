import streamlit as st
import yfinance as yf
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.cluster.hierarchy import linkage
from scipy.spatial.distance import squareform
from datetime import date, timedelta
from optimizacion_portafolios.arima import (
    analizar_arima,
    backtest_arima,
    descargar_precios_yahoo,
    pronosticar_precios,
)


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


def fecha_hace_un_anio(fecha: date) -> date:
    try:
        return fecha.replace(year=fecha.year - 1)
    except ValueError:
        return fecha.replace(year=fecha.year - 1, day=28)


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


def portafolio_varianza_inversa(covarianza: pd.DataFrame) -> np.ndarray:
    varianzas = np.diag(covarianza)
    pesos = 1 / varianzas
    return pesos / pesos.sum()


def varianza_cluster(covarianza: pd.DataFrame, activos: list[str]) -> float:
    covarianza_cluster = covarianza.loc[activos, activos]
    pesos = portafolio_varianza_inversa(covarianza_cluster).reshape(-1, 1)
    varianza = pesos.T @ covarianza_cluster.to_numpy() @ pesos
    return varianza.item()


def orden_cuasi_diagonal(enlace: np.ndarray) -> list[int]:
    enlace = enlace.astype(int)
    orden = pd.Series([enlace[-1, 0], enlace[-1, 1]])
    numero_activos = int(enlace[-1, 3])

    while orden.max() >= numero_activos:
        orden.index = range(0, orden.shape[0] * 2, 2)
        clusters = orden[orden >= numero_activos]
        posiciones = clusters.index
        indices_enlace = clusters.values - numero_activos
        orden[posiciones] = enlace[indices_enlace, 0]
        nuevos = pd.Series(enlace[indices_enlace, 1], index=posiciones + 1)
        orden = pd.concat([orden, nuevos]).sort_index()
        orden.index = range(orden.shape[0])

    return orden.tolist()


def calcular_hrp(retornos: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    covarianza = retornos.cov()
    correlacion = retornos.corr()
    distancia = np.sqrt((1 - correlacion) / 2)
    enlace = linkage(squareform(distancia.to_numpy(), checks=False), method="single")
    orden = correlacion.index[orden_cuasi_diagonal(enlace)].tolist()

    pesos = pd.Series(1.0, index=orden)
    clusters = [orden]
    while clusters:
        clusters = [
            mitad
            for cluster in clusters
            for mitad in (cluster[: len(cluster) // 2], cluster[len(cluster) // 2 :])
            if len(cluster) > 1
        ]
        for posicion in range(0, len(clusters), 2):
            cluster_izquierdo = clusters[posicion]
            cluster_derecho = clusters[posicion + 1]
            varianza_izquierda = varianza_cluster(covarianza, cluster_izquierdo)
            varianza_derecha = varianza_cluster(covarianza, cluster_derecho)
            peso_izquierdo = 1 - varianza_izquierda / (
                varianza_izquierda + varianza_derecha
            )
            pesos[cluster_izquierdo] *= peso_izquierdo
            pesos[cluster_derecho] *= 1 - peso_izquierdo

    return pesos.sort_values(ascending=False), correlacion.loc[orden, orden]


def calcular_volatilidad_mensual(retornos: pd.DataFrame) -> pd.DataFrame:
    volatilidad_mensual = retornos.resample("ME").std() * np.sqrt(21)
    return volatilidad_mensual.dropna(how="all")


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
fecha_inicio = fecha_hace_un_anio(fecha_actual)

if "pagina" not in st.session_state:
    st.session_state.pagina = "Dashboard"

with st.sidebar:
    st.header("Navegación")
    if st.button("⌂  Dashboard", use_container_width=True):
        st.session_state.pagina = "Dashboard"
        st.rerun()
    modulo = st.radio(
        "Ir directamente a",
        ["Monitoreo", "Optimización BL & HRP", "Precios de venta de opciones"],
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

if pagina == "Precios de venta de opciones":
    st.title("Precios de venta de opciones")
    st.caption("Modelo ARIMA basado en el flujo Box-Jenkins del archivo entregado.")

    ticker = st.selectbox(
        "Subyacente",
        options=list(ACTIVOS.values()),
        format_func=lambda valor: next(
            nombre for nombre, simbolo in ACTIVOS.items() if simbolo == valor
        ),
        key="arima_ticker",
    )
    col_fecha, col_horizonte = st.columns(2)
    with col_fecha:
        fecha_inicio_arima = st.date_input(
            "Fecha inicial",
            value=fecha_actual - timedelta(days=730),
            max_value=fecha_actual,
            key="arima_fecha_inicio",
        )
    with col_horizonte:
        horizonte = st.number_input(
            "Días hábiles a pronosticar",
            min_value=1,
            max_value=100,
            value=10,
            step=1,
            key="arima_horizonte",
        )

    try:
        with st.spinner("Descargando precios y ejecutando Box-Jenkins..."):
            precios_arima = descargar_serie_arima(
                ticker, fecha_inicio_arima, fecha_actual
            )
            resultado_arima = analizar_arima(ticker, precios_arima)
            pronostico_arima = pronosticar_precios(resultado_arima, int(horizonte))
    except ValueError as error:
        st.error(str(error))
        st.stop()

    st.subheader(f"Resultado seleccionado: {resultado_arima.modelo_seleccionado}")
    metrica_precio, metrica_retorno, metrica_observaciones = st.columns(3)
    metrica_precio.metric("Último precio", f"${precios_arima.iloc[-1]:,.2f}")
    metrica_retorno.metric(
        "Retorno logarítmico medio",
        f"{resultado_arima.retornos_log.mean():.4%}",
    )
    metrica_observaciones.metric("Observaciones", f"{len(precios_arima):,}")

    tab_modelos, tab_diagnostico, tab_pronostico, tab_validacion = st.tabs(
        ["Comparación de modelos", "Identificación", "Pronóstico", "Validación"]
    )
    with tab_modelos:
        st.dataframe(
            resultado_arima.comparacion_modelos.style.format(
                {"aic": "{:.2f}", "bic": "{:.2f}", "hqic": "{:.2f}", "sse": "{:.6f}"}
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption("El modelo seleccionado es el de menor AIC, siguiendo la etapa de validación del script original.")
    with tab_diagnostico:
        st.line_chart(
            resultado_arima.retornos_log,
            y_label="Retorno logarítmico",
            x_label="Fecha",
        )
        col_acf, col_pacf = st.columns(2)
        with col_acf:
            st.subheader("ACF")
            st.bar_chart(resultado_arima.acf.set_index("rezago"), y_label="ACF")
        with col_pacf:
            st.subheader("PACF")
            st.bar_chart(resultado_arima.pacf.set_index("rezago"), y_label="PACF")
        st.subheader("Ljung-Box")
        st.dataframe(resultado_arima.ljung_box, hide_index=True, width="stretch")
    with tab_pronostico:
        historico = precios_arima.tail(120).rename("Precio real")
        futuro = pronostico_arima["precio_pronosticado"].rename("Precio pronosticado")
        st.line_chart(pd.concat([historico, futuro]), y_label="Precio", x_label="Fecha")
        st.dataframe(
            pronostico_arima.style.format(
                {
                    "retorno_log_pronosticado": "{:.4%}",
                    "precio_pronosticado": "${:,.2f}",
                }
            ),
            width="stretch",
        )
    with tab_validacion:
        try:
            backtest = backtest_arima(precios_arima, n_test=10)
            st.line_chart(
                backtest[["precio_real", "precio_pronosticado"]],
                y_label="Precio",
                x_label="Fecha",
            )
            st.dataframe(
                backtest.style.format(
                    {
                        "precio_real": "${:,.2f}",
                        "precio_pronosticado": "${:,.2f}",
                        "error_porcentual": "{:.2f}%",
                    }
                ),
                width="stretch",
            )
        except ValueError as error:
            st.warning(str(error))
    st.stop()

if pagina == "Optimización BL & HRP":
    st.title("Optimización de portafolios")
    st.caption("Paridad por Riesgo Jerárquico con datos históricos de Yahoo Finance")

    opciones_hrp = {**CRIPTOMONEDAS, **COMMODITIES}
    activos_hrp = st.multiselect(
        "Activos del portafolio HRP",
        options=list(opciones_hrp),
        default=list(opciones_hrp),
        key="optimizacion_hrp_activos",
    )
    fecha_inicio_hrp = st.date_input(
        "Fecha inicial",
        value=fecha_inicio,
        max_value=fecha_actual,
        key="optimizacion_hrp_fecha_inicio",
    )

    if len(activos_hrp) < 2:
        st.warning("Selecciona al menos dos activos para calcular HRP.")
        st.stop()

    tickers_hrp = [opciones_hrp[activo] for activo in activos_hrp]
    nombres_hrp = {ticker: nombre for nombre, ticker in opciones_hrp.items()}
    with st.spinner("Descargando datos y calculando el portafolio HRP..."):
        precios_hrp = descargar_precios(
            tuple(tickers_hrp), fecha_inicio_hrp, fecha_actual
        ).dropna(how="all")
        retornos_hrp = precios_hrp.pct_change(fill_method=None).dropna(how="all")
        retornos_hrp = retornos_hrp.dropna(axis="columns", how="all").dropna()

    tickers_sin_datos = [
        ticker for ticker in tickers_hrp if ticker not in retornos_hrp.columns
    ]
    if tickers_sin_datos:
        nombres_sin_datos = [nombres_hrp[ticker] for ticker in tickers_sin_datos]
        st.warning(
            "Yahoo Finance no devolvió datos para: "
            + ", ".join(nombres_sin_datos)
            + ". Se excluirán del cálculo."
        )

    if retornos_hrp.shape[1] < 2:
        st.error("No hay datos suficientes para calcular HRP con los activos elegidos.")
        st.stop()

    pesos_hrp, correlacion_ordenada = calcular_hrp(retornos_hrp)
    tabla_pesos = pd.DataFrame(
        {
            "Activo": [nombres_hrp[ticker] for ticker in pesos_hrp.index],
            "Ticker": pesos_hrp.index,
            "Peso": pesos_hrp.values,
        }
    )

    st.subheader("Distribución del portafolio")
    st.bar_chart(tabla_pesos.set_index("Activo")["Peso"], y_label="Peso")
    st.dataframe(
        tabla_pesos.style.format({"Peso": "{:.2%}"}),
        width="stretch",
        hide_index=True,
    )
    st.caption(f"Suma de pesos: {pesos_hrp.sum():.2%} | Observaciones: {len(retornos_hrp):,}")

    precios_validos = precios_hrp[retornos_hrp.columns].ffill().dropna()
    precios_mostrados = precios_validos.rename(columns=nombres_hrp)
    precios_unitarios = precios_validos / precios_validos.iloc[0]
    precios_unitarios = precios_unitarios.rename(columns=nombres_hrp)

    tab_precios, tab_unitarios, tab_correlacion, tab_retorno, tab_volatilidad = st.tabs(
        [
            "Precios por activo",
            "Evolución unitaria",
            "Correlación cuasi-diagonal",
            "Retornos históricos",
            "Volatilidad mensual",
        ]
    )
    with tab_precios:
        activo_precio = st.selectbox(
            "Selecciona un activo para ver su precio",
            options=list(precios_mostrados.columns),
            key="hrp_activo_precio",
        )
        st.line_chart(
            precios_mostrados[activo_precio],
            y_label="Precio de cierre",
            x_label="Fecha",
        )
        st.caption(
            "Precio de cierre ajustado descargado desde Yahoo Finance."
        )
    with tab_unitarios:
        st.line_chart(
            precios_unitarios,
            y_label="Valor unitario (inicio = 1.0)",
            x_label="Fecha",
        )
        st.caption(
            "Cada activo se normaliza a 1.0 en la primera fecha disponible para comparar su evolución."
        )
    with tab_correlacion:
        st.dataframe(
            correlacion_ordenada.style.background_gradient(
                cmap="RdYlGn", vmin=-1, vmax=1
            ).format("{:.2f}"),
            width="stretch",
        )
    with tab_retorno:
        st.line_chart(retornos_hrp, y_label="Retorno diario", x_label="Fecha")
    with tab_volatilidad:
        volatilidad_mensual = calcular_volatilidad_mensual(retornos_hrp)
        valores_por_activo = {
            ticker: (volatilidad_mensual[ticker].dropna() * 100).to_numpy()
            for ticker in volatilidad_mensual.columns
        }
        todos_los_valores = np.concatenate(list(valores_por_activo.values()))
        bins = np.histogram_bin_edges(todos_los_valores, bins=8)
        columnas_graficos = st.columns(2)
        for posicion, (ticker, valores) in enumerate(valores_por_activo.items()):
            fig, ax = plt.subplots(figsize=(7, 4))
            ax.hist(
                valores,
                bins=bins,
                color="#2f6f8f",
                edgecolor="white",
            )
            volatilidad_actual = valores[-1]
            ax.axvline(
                volatilidad_actual,
                color="#c75146",
                linestyle="--",
                linewidth=2,
                label=f"Actual: {volatilidad_actual:.2f}%",
            )
            ax.set_title(nombres_hrp[ticker])
            ax.set_xlabel("Volatilidad mensual (%)")
            ax.set_ylabel("Frecuencia (meses)")
            ax.grid(axis="y", alpha=0.25)
            ax.legend()
            columnas_graficos[posicion % 2].pyplot(fig)
            plt.close(fig)
        st.caption(
            "Cada observación representa la volatilidad realizada de un mes, "
            "calculada como la desviación estándar de los retornos diarios del mes "
            "por √21."
        )
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
        ACTIVOS,
        INDICES,
        list(ACTIVOS)[:2],
        permitir_filtro_tipo=True,
    )

with tab_hrp:
    mostrar_seguimiento(
        "Activos de seguimiento para HRP",
        "Selecciona los activos que se seguirán para construir el portafolio HRP.",
        "hrp",
        fecha_inicio,
        fecha_actual,
        CRIPTOMONEDAS,
        COMMODITIES,
        [*CRIPTOMONEDAS, *COMMODITIES],
    )
