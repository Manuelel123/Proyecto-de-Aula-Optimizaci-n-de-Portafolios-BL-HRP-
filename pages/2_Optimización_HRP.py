import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import quantstats as qs
import streamlit as st
import streamlit.components.v1 as components

from optimizacion_portafolios.ui import (
    ACTIVOS_HRP,
    BENCHMARKS,
    COMMODITIES,
    CRIPTOMONEDAS,
    PORTAFOLIO_COLOMBIA,
    PORTAFOLIO_IMAGEN,
    calcular_contribuciones_hrp,
    calcular_hrp,
    calcular_metricas_quantstats,
    calcular_volatilidad_mensual,
    configurar_pagina,
    descargar_precios,
    generar_tearsheet_quantstats,
)


fecha_actual, fecha_inicio = configurar_pagina("Optimización BL & HRP")

st.title("Optimización de portafolios")
st.caption("Paridad por Riesgo Jerárquico con datos históricos de Yahoo Finance")

universos_hrp = {
    "Selección directa de activos": ACTIVOS_HRP,
    "Criptomonedas y commodities": {**CRIPTOMONEDAS, **COMMODITIES},
    "Portafolio de la imagen": PORTAFOLIO_IMAGEN,
    "Portafolio Colombia": PORTAFOLIO_COLOMBIA,
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
tickers_personalizados = st.text_input(
    "Agregar otros activos",
    placeholder="Ejemplo: AMZN, NVDA, ^VIX",
    help="Escribe tickers de Yahoo Finance separados por comas.",
    key="optimizacion_hrp_tickers_personalizados",
)
nombre_benchmark = st.selectbox(
    "Benchmark para el tearsheet de QuantStats",
    options=list(BENCHMARKS),
    index=0,
    key="optimizacion_hrp_benchmark",
)
ticker_benchmark_personalizado = st.text_input(
    "Benchmark personalizado (opcional)",
    placeholder="Ejemplo: ^COLCAP, SPY o QQQ",
    help="Si lo escribes, reemplazará el benchmark seleccionado arriba.",
    key="optimizacion_hrp_benchmark_personalizado",
).strip().upper()
if ticker_benchmark_personalizado:
    ticker_benchmark = ticker_benchmark_personalizado
    nombre_benchmark = f"Benchmark personalizado ({ticker_benchmark})"
else:
    ticker_benchmark = BENCHMARKS[nombre_benchmark]
fecha_inicio_hrp = st.date_input(
    "Fecha inicial",
    value=fecha_inicio,
    max_value=fecha_actual,
    key="optimizacion_hrp_fecha_inicio",
)

tickers_hrp = [opciones_hrp[activo] for activo in activos_hrp]
nombres_hrp = {ticker: nombre for nombre, ticker in opciones_hrp.items()}
for ticker in tickers_personalizados.split(","):
    ticker = ticker.strip().upper()
    if ticker:
        tickers_hrp.append(ticker)
        nombres_hrp.setdefault(ticker, ticker)
tickers_hrp = list(dict.fromkeys(tickers_hrp))

if len(tickers_hrp) < 2:
    st.warning("Selecciona al menos dos activos para calcular HRP.")
    st.stop()

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
with st.spinner(f"Descargando benchmark {ticker_benchmark} para el tearsheet..."):
    precios_benchmark = descargar_precios(
        (ticker_benchmark,), fecha_inicio_hrp, fecha_actual
    )
if precios_benchmark.empty:
    st.error(f"Yahoo Finance no devolvió datos para el benchmark {ticker_benchmark}.")
    st.stop()
if isinstance(precios_benchmark, pd.Series):
    serie_benchmark = precios_benchmark
elif ticker_benchmark in precios_benchmark.columns:
    serie_benchmark = precios_benchmark[ticker_benchmark]
else:
    st.error(f"No se encontró la serie de cierre del benchmark {ticker_benchmark}.")
    st.stop()
retornos_benchmark = serie_benchmark.pct_change(fill_method=None).rename(
    ticker_benchmark
)
retornos_portafolio_hrp = retornos_hrp.dot(pesos_hrp).rename("Portafolio HRP")
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
    st.caption("Precio de cierre ajustado descargado desde Yahoo Finance.")
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
        f"El reporte compara el portafolio HRP contra {nombre_benchmark} "
        "usando la salida generada por QuantStats."
    )
    contribuciones_hrp, resumen_contribuciones = calcular_contribuciones_hrp(
        retornos_hrp, pesos_hrp
    )
    st.subheader("Contribución de cada activo")
    tabla_contribuciones = resumen_contribuciones.rename(
        index=nombres_hrp
    ).rename_axis("Activo")
    st.dataframe(
        tabla_contribuciones.style.format(
            {
                "Peso HRP": "{:.2%}",
                "Retorno anualizado": "{:.2%}",
                "Volatilidad anualizada": "{:.2%}",
                "Contribución anualizada": "{:.2%}",
                "Participación del retorno": "{:.2%}",
            }
        ),
        width="stretch",
    )
    st.bar_chart(
        resumen_contribuciones["Contribución anualizada"].rename(index=nombres_hrp),
        y_label="Contribución anualizada",
    )
    st.subheader("Métricas del portafolio")
    st.dataframe(
        calcular_metricas_quantstats(retornos_portafolio_hrp).style.format(
            {"Valor": "{:.2%}"}
        ),
        width="stretch",
    )
    tab_graficos_qs, tab_reporte_qs = st.tabs(
        ["Gráficos QuantStats", "Tearsheet completo"]
    )
    with tab_graficos_qs:
        graficos_quantstats = [
            (
                "Rendimiento acumulado",
                qs.plots.returns(
                    retornos_portafolio_hrp,
                    benchmark=retornos_benchmark,
                    figsize=(10, 5),
                    show=False,
                ),
            ),
            (
                "Caída acumulada",
                qs.plots.drawdown(
                    retornos_portafolio_hrp,
                    figsize=(10, 4),
                    show=False,
                ),
            ),
            (
                "Sharpe móvil",
                qs.plots.rolling_sharpe(
                    retornos_portafolio_hrp,
                    benchmark=retornos_benchmark,
                    figsize=(10, 3.5),
                    show=False,
                ),
            ),
            (
                "Rendimientos mensuales",
                qs.plots.monthly_heatmap(
                    retornos_portafolio_hrp,
                    benchmark=retornos_benchmark,
                    figsize=(10, 5),
                    show=False,
                ),
            ),
        ]
        for titulo_grafico, figura_quantstats in graficos_quantstats:
            st.subheader(titulo_grafico)
            st.pyplot(figura_quantstats, clear_figure=True, width="stretch")
            plt.close(figura_quantstats)
    reporte_quantstats = generar_tearsheet_quantstats(
        retornos_portafolio_hrp, retornos_benchmark
    )
    with tab_reporte_qs:
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
        ax.hist(valores, bins=bins, color="#2f6f8f", edgecolor="white")
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
