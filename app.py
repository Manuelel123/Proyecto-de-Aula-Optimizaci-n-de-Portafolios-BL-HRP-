import streamlit as st
import yfinance as yf
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import quantstats as qs
import streamlit.components.v1 as components
from scipy.cluster.hierarchy import linkage
from scipy.spatial.distance import squareform
from datetime import date, timedelta
from tempfile import NamedTemporaryFile
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
    with NamedTemporaryFile(suffix=".html") as archivo_reporte:
        qs.reports.html(
            retornos_portafolio,
            benchmark=benchmark,
            output=archivo_reporte.name,
            title="Strategy Tearsheet - Portafolio HRP",
            download_filename="reporte_quantstats_hrp.html",
        )
        archivo_reporte.seek(0)
        return archivo_reporte.read()


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
    if not bool(resultado_arima.comparacion_modelos.iloc[0]["residuos_blancos"]):
        st.warning(
            "El modelo seleccionado minimiza el error de validación, pero sus residuos "
            "no son compatibles con ruido blanco según Ljung-Box."
        )

    tab_modelos, tab_diagnostico, tab_pronostico, tab_validacion = st.tabs(
        ["Comparación de modelos", "Identificación", "Pronóstico", "Validación"]
    )
    with tab_modelos:
        st.dataframe(
            resultado_arima.comparacion_modelos.style.format(
                {
                    "aic": "{:.2f}",
                    "bic": "{:.2f}",
                    "hqic": "{:.2f}",
                    "sse": "{:.6f}",
                    "p_valor_ljung_box_min": "{:.4f}",
                    "rmse_validacion_retorno": "{:.6f}",
                    "rmse_validacion_precio": "{:.2f}",
                }
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "Se selecciona el menor RMSE de precio en la validación temporal. "
            "Ljung-Box identifica si los residuos son ruido blanco y se usa como "
            "desempate; el AIC se usa como segundo desempate."
        )
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
            dias_historicos = min(60, len(precios_arima) - len(backtest))
            inicio_prueba = backtest.index[0]
            historico_validacion = precios_arima.loc[
                precios_arima.index < inicio_prueba
            ].tail(dias_historicos)
            pronostico_grafico = backtest["precio_pronosticado"].copy()
            pronostico_grafico.index = backtest.index

            figura, eje = plt.subplots(figsize=(12, 5))
            eje.plot(
                historico_validacion.index,
                historico_validacion.to_numpy(),
                color="#53606b",
                linewidth=1.8,
                label="Histórico de entrenamiento",
            )
            eje.plot(
                backtest.index,
                backtest["precio_real"],
                color="#1769aa",
                marker="o",
                linewidth=2.2,
                label="Precio real",
            )
            eje.plot(
                pronostico_grafico.index,
                pronostico_grafico,
                color="#d95f02",
                marker="x",
                linestyle="--",
                linewidth=2.2,
                label="Pronóstico ARIMA",
            )
            eje.axvline(
                inicio_prueba,
                color="#555555",
                linestyle=":",
                linewidth=1.5,
                label="Inicio de la prueba",
            )
            eje.set_title("Validación fuera de muestra: precio real frente a pronóstico")
            eje.set_xlabel("Fecha")
            eje.set_ylabel("Precio")
            eje.grid(axis="y", alpha=0.25)
            eje.legend()
            figura.autofmt_xdate()
            st.pyplot(figura)
            plt.close(figura)
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

    universos_hrp = {
        "Criptomonedas y commodities": {**CRIPTOMONEDAS, **COMMODITIES},
        "Portafolio de la imagen": PORTAFOLIO_IMAGEN,
    }
    nombre_universo = st.selectbox(
        "Portafolio para optimizar",
        options=list(universos_hrp),
        key="optimizacion_hrp_universo",
    )
    opciones_hrp = universos_hrp[nombre_universo]
    activos_hrp = st.multiselect(
        "Activos del portafolio HRP",
        options=list(opciones_hrp),
        default=list(opciones_hrp),
        key=f"optimizacion_hrp_activos_{nombre_universo}",
    )
    nombre_benchmark = st.selectbox(
        "Benchmark para el tearsheet de QuantStats",
        options=list(BENCHMARKS),
        index=0,
        key="optimizacion_hrp_benchmark",
    )
    ticker_benchmark = BENCHMARKS[nombre_benchmark]
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
    retornos_portafolio_hrp = retornos_hrp.dot(pesos_hrp).rename("Portafolio HRP")
    with st.spinner(f"Descargando benchmark {ticker_benchmark} para el tearsheet..."):
        precios_benchmark = descargar_precios(
            (ticker_benchmark,), fecha_inicio_hrp, fecha_actual
        )
    if precios_benchmark.empty:
        st.error(
            f"Yahoo Finance no devolvió datos para el benchmark {ticker_benchmark}."
        )
        st.stop()
    if isinstance(precios_benchmark, pd.Series):
        serie_benchmark = precios_benchmark
    else:
        if ticker_benchmark not in precios_benchmark.columns:
            st.error(
                f"No se encontró la serie de cierre del benchmark {ticker_benchmark}."
            )
            st.stop()
        serie_benchmark = precios_benchmark[ticker_benchmark]
    retornos_benchmark = serie_benchmark.pct_change(fill_method=None).rename(
        ticker_benchmark
    )
    datos_quantstats = pd.concat(
        [retornos_portafolio_hrp, retornos_benchmark], axis=1
    ).dropna()
    retornos_portafolio_hrp = datos_quantstats["Portafolio HRP"]
    retornos_benchmark = datos_quantstats[ticker_benchmark]
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

    tab_precios, tab_unitarios, tab_correlacion, tab_retorno, tab_volatilidad, tab_quantstats = st.tabs(
        [
            "Precios por activo",
            "Evolución unitaria",
            "Correlación cuasi-diagonal",
            "Retornos históricos",
            "Volatilidad mensual",
            "Análisis QuantStats",
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
        st.line_chart(
            retornos_portafolio_hrp,
            y_label="Retorno diario del portafolio",
            x_label="Fecha",
        )
    with tab_quantstats:
        st.subheader("Tearsheet oficial de QuantStats")
        st.caption(
            f"El reporte compara el portafolio HRP contra {nombre_benchmark} usando exactamente la salida "
            "generada por QuantStats."
        )
        contribuciones_hrp, resumen_contribuciones = calcular_contribuciones_hrp(
            retornos_hrp, pesos_hrp
        )
        st.markdown(
            "El retorno diario del portafolio se calcula como la suma de los retornos "
            "de cada activo multiplicados por su peso HRP:"
        )
        st.latex(
            r"R_{p,t} = \sum_i w_i R_{i,t}"
            r"\quad\text{y}\quad"
            r"C_{i,t} = w_i R_{i,t}"
        )
        st.caption(
            "Los pesos HRP son fijos durante el periodo analizado. Por eso QuantStats "
            "recibe la serie resultante de sumar las contribuciones diarias de todos los activos."
        )

        st.subheader("Contribución de cada activo")
        tabla_contribuciones = resumen_contribuciones.rename(
            index=nombres_hrp
        ).rename_axis("Activo")
        tabla_contribuciones_mostrada = tabla_contribuciones.copy()
        for columna in [
            "Peso HRP",
            "Retorno anualizado",
            "Volatilidad anualizada",
            "Contribución anualizada",
            "Participación del retorno",
        ]:
            tabla_contribuciones_mostrada[columna] = tabla_contribuciones_mostrada[
                columna
            ].map(lambda valor: f"{valor:.2%}")
        st.dataframe(tabla_contribuciones_mostrada, width="stretch")
        st.bar_chart(
            tabla_contribuciones["Contribución anualizada"],
            y_label="Contribución anualizada",
        )

        reporte_quantstats = generar_tearsheet_quantstats(
            retornos_portafolio_hrp, retornos_benchmark
        )
        components.html(reporte_quantstats, height=1800, scrolling=True)
        st.download_button(
            "Descargar tearsheet completo de QuantStats",
            data=reporte_quantstats,
            file_name="reporte_quantstats_hrp.html",
            mime="text/html",
            key="descargar_reporte_quantstats_hrp",
        )
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
