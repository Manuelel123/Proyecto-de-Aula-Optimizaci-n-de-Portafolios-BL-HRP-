import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import quantstats as qs
import streamlit as st
import streamlit.components.v1 as components

from optimizacion_portafolios.black_litterman import optimizar_black_litterman
from optimizacion_portafolios.ui import (
    ACTIVOS,
    ACTIVOS_HRP,
    BENCHMARKS,
    COMMODITIES,
    CRIPTOMONEDAS,
    PORTAFOLIO_COLOMBIA,
    PORTAFOLIO_IMAGEN,
    calcular_metricas_quantstats,
    configurar_pagina,
    descargar_precios,
    descargar_capitalizacion_yahoo,
    fecha_hace_un_anio,
    generar_tearsheet_quantstats,
)


fecha_actual, _ = configurar_pagina("Optimización Black Litterman")
fecha_inicio_bl = fecha_hace_un_anio(fecha_hace_un_anio(fecha_actual))

st.title("Optimización Black Litterman")
st.caption(
    "Combina retornos de equilibrio con una view anualizada y su confianza para cada activo."
)

universos = {
    "Activos principales": ACTIVOS,
    "Criptomonedas y commodities": {**CRIPTOMONEDAS, **COMMODITIES},
    "portafolio actual: 1": PORTAFOLIO_IMAGEN,
    "Portafolio Colombia": PORTAFOLIO_COLOMBIA,
    "Selección directa de activos": ACTIVOS_HRP,
}

with st.form("form_black_litterman"):
    nombre_universo = st.selectbox(
        "Universo de activos",
        options=list(universos),
        index=list(universos).index("portafolio actual: 1"),
        key="bl_universo_actual_1",
    )
    opciones_activos = universos[nombre_universo]
    activos_seleccionados = st.multiselect(
        "Activos del portafolio",
        options=list(opciones_activos),
        default=(
            list(opciones_activos)
            if nombre_universo == "portafolio actual: 1"
            else list(opciones_activos)[:4]
        ),
        key="bl_activos_actual_1",
    )
    tickers_personalizados = st.text_input(
        "Agregar otros tickers",
        placeholder="Ejemplo: AMZN, NVDA",
        help="Usa símbolos de Yahoo Finance separados por comas.",
        key="bl_tickers_personalizados",
    )

    st.caption(
        "El prior usa capitalizaciones convertidas a USD; para ETFs sin marketCap, "
        "usa sus activos netos convertidos a USD."
    )
    col_objetivo, col_tasa = st.columns(2)
    with col_objetivo:
        objetivo_seleccionado = st.selectbox(
            "Objetivo de optimización",
            [
                "Máximo Sharpe",
                "Mínima volatilidad",
                "Pesos implícitos del modelo",
            ],
            key="bl_objetivo",
        )
        if objetivo_seleccionado == "Pesos implícitos del modelo":
            st.info(
                "Usa BlackLittermanModel.bl_weights() sin límites long-only; "
                "los pesos pueden ser negativos o superar el 100%."
            )
    with col_tasa:
        tasa_libre_riesgo_pct = st.number_input(
            "Tasa libre de riesgo anual (%)",
            min_value=0.0,
            max_value=100.0,
            value=2.0,
            step=0.25,
            key="bl_tasa_libre_riesgo",
        )
    col_fecha, col_benchmark = st.columns(2)
    with col_fecha:
        fecha_inicio_bl = st.date_input(
            "Inicio de ventana fija de 2 años",
            value=fecha_hace_un_anio(fecha_hace_un_anio(fecha_actual)),
            max_value=fecha_actual,
            disabled=True,
            key="bl_fecha_inicio_ventana_dos_anios_fija",
        )
    with col_benchmark:
        nombre_benchmark = st.selectbox(
            "Benchmark para estimar aversión al riesgo",
            options=list(BENCHMARKS),
            key="bl_benchmark",
        )

    tickers = [opciones_activos[nombre] for nombre in activos_seleccionados]
    nombres_activos = {
        ticker: nombre for nombre, ticker in opciones_activos.items()
    }
    for ticker_personalizado in tickers_personalizados.split(","):
        ticker_personalizado = ticker_personalizado.strip().upper()
        if ticker_personalizado:
            tickers.append(ticker_personalizado)
            nombres_activos.setdefault(ticker_personalizado, ticker_personalizado)
    tickers = list(dict.fromkeys(tickers))

    if tickers:
        columnas_vistas = {
            "Activo": [nombres_activos[ticker] for ticker in tickers],
            "Ticker": tickers,
        }
        columnas_vistas["View anual (%)"] = [8.0] * len(tickers)
        columnas_vistas["Confianza (%)"] = [95.0] * len(tickers)
        vistas_iniciales = pd.DataFrame(columnas_vistas)
        st.subheader("Views individuales")
        st.caption(
            "Define el retorno esperado anual por activo. La confianza se mantiene fija en 95%."
        )
        configuracion_columnas = {
            "Activo": st.column_config.TextColumn(disabled=True),
            "Ticker": st.column_config.TextColumn(disabled=True),
            "View anual (%)": st.column_config.NumberColumn(
                min_value=-100.0,
                max_value=1000.0,
                step=0.5,
                format="%.2f",
            ),
            "Confianza (%)": st.column_config.NumberColumn(
                min_value=1.0,
                max_value=99.0,
                step=1.0,
                format="%.0f%%",
            ),
        }
        vistas_editadas = st.data_editor(
            vistas_iniciales,
            column_config=configuracion_columnas,
            disabled=["Activo", "Ticker", "Confianza (%)"],
            hide_index=True,
            width="stretch",
            key=f"bl_vistas_yahoo_95_{'_'.join(tickers)}",
        )
    else:
        vistas_editadas = pd.DataFrame()
        st.info("Selecciona al menos dos activos para definir sus views.")

    calcular = st.form_submit_button(
        "Calcular portafolio Black Litterman",
        type="primary",
        icon="📊",
    )

if not calcular:
    st.stop()

if len(tickers) < 2:
    st.error("Selecciona al menos dos activos para calcular el portafolio.")
    st.stop()

if vistas_editadas.empty or vistas_editadas.isna().any().any():
    st.error("Completa las views requeridas.")
    st.stop()

tickers_vistas = vistas_editadas["Ticker"].tolist()
vistas_absolutas = {
    fila["Ticker"]: float(fila["View anual (%)"]) / 100
    for _, fila in vistas_editadas.iterrows()
}
confianzas = {
    ticker: 0.95 for ticker in tickers_vistas
}
try:
    with st.spinner("Descargando capitalizaciones bursátiles desde Yahoo Finance..."):
        capitalizaciones = {
            ticker: descargar_capitalizacion_yahoo(ticker)
            for ticker in tickers
        }
except Exception as error:
    st.error(f"No se pudieron descargar las capitalizaciones de Yahoo Finance: {error}")
    st.stop()

tickers_sin_capitalizacion = [
    ticker for ticker, capitalizacion in capitalizaciones.items()
    if capitalizacion is None
]
if tickers_sin_capitalizacion:
    st.error(
        "Yahoo Finance no devolvió una capitalización bursátil positiva para: "
        + ", ".join(tickers_sin_capitalizacion)
    )
    st.stop()

with st.spinner("Descargando precios y calculando el portafolio..."):
    try:
        precios = descargar_precios(tuple(tickers), fecha_inicio_bl, fecha_actual)
        if isinstance(precios, pd.Series):
            precios = precios.to_frame(name=tickers[0])
        precios = precios.dropna(axis="columns", how="all").ffill().dropna()
        tickers_sin_datos = [ticker for ticker in tickers if ticker not in precios]
        if tickers_sin_datos:
            st.warning(
                "Se excluirán activos sin precios disponibles: "
                + ", ".join(tickers_sin_datos)
            )
        precios = precios[[ticker for ticker in tickers if ticker in precios.columns]]
        if precios.shape[1] < 2:
            st.error("Yahoo Finance devolvió datos para menos de dos activos.")
            st.stop()

        precios_benchmark = None
        ticker_benchmark = BENCHMARKS[nombre_benchmark]
        precios_benchmark_descargados = descargar_precios(
            (ticker_benchmark,), fecha_inicio_bl, fecha_actual
        )
        if isinstance(precios_benchmark_descargados, pd.Series):
            precios_benchmark = precios_benchmark_descargados
        elif ticker_benchmark in precios_benchmark_descargados.columns:
            precios_benchmark = precios_benchmark_descargados[ticker_benchmark]
        elif len(precios_benchmark_descargados.columns) == 1:
            precios_benchmark = precios_benchmark_descargados.iloc[:, 0]
        else:
            raise ValueError(
                f"No se encontraron precios para el benchmark {ticker_benchmark}."
            )

        resultado = optimizar_black_litterman(
            precios=precios,
            vistas_absolutas={
                ticker: vistas_absolutas[ticker]
                for ticker in precios.columns
            },
            confianzas={ticker: confianzas[ticker] for ticker in precios.columns},
            objetivo=(
                "max_sharpe"
                if objetivo_seleccionado == "Máximo Sharpe"
                else (
                    "min_volatility"
                    if objetivo_seleccionado == "Mínima volatilidad"
                    else "model_weights"
                )
            ),
            tasa_libre_riesgo=tasa_libre_riesgo_pct / 100,
            capitalizaciones={ticker: capitalizaciones[ticker] for ticker in precios.columns},
            precios_mercado=precios_benchmark,
        )
    except (ValueError, TypeError, ArithmeticError) as error:
        st.error(f"No fue posible calcular Black Litterman: {error}")
        st.stop()

st.subheader("Resultados del portafolio")
col_retorno, col_volatilidad, col_sharpe = st.columns(3)
col_retorno.metric("Retorno esperado anual", f"{resultado.retorno_esperado:.2%}")
col_volatilidad.metric("Volatilidad anual", f"{resultado.volatilidad:.2%}")
col_sharpe.metric("Ratio de Sharpe", f"{resultado.sharpe:.3f}")

st.bar_chart(
    resultado.pesos.rename(index=nombres_activos),
    y_label="Peso del portafolio",
)

tabla_resultados = pd.DataFrame(
    {
        "Activo": [nombres_activos[ticker] for ticker in resultado.pesos.index],
        "Ticker": resultado.pesos.index,
        "Peso": resultado.pesos.values,
        "Capitalización / activos netos (USD)": [
            capitalizaciones[ticker] for ticker in resultado.pesos.index
        ],
        "View anual": [vistas_absolutas[ticker] for ticker in resultado.pesos.index],
        "Confianza": [confianzas[ticker] for ticker in resultado.pesos.index],
        "Retorno prior": resultado.retornos_prior.reindex(resultado.pesos.index).values,
        "Retorno posterior": resultado.retornos_posteriores.reindex(
            resultado.pesos.index
        ).values,
    }
)
st.dataframe(
    tabla_resultados.style.format(
        {
            "Peso": "{:.2%}",
            "Capitalización / activos netos (USD)": "{:,.0f}",
            "View anual": "{:.2%}",
            "Confianza": "{:.0%}",
            "Retorno prior": "{:.2%}",
            "Retorno posterior": "{:.2%}",
        }
    ),
    hide_index=True,
    width="stretch",
)
st.caption(f"Suma de pesos: {resultado.pesos.sum():.2%}")

retornos_activos = precios.pct_change(fill_method=None).dropna(how="all")
retornos_portafolio_quantstats = retornos_activos.loc[
    :, resultado.pesos.index
].dot(resultado.pesos).rename("Portafolio Black Litterman")
retornos_benchmark_quantstats = precios_benchmark.pct_change(
    fill_method=None
).rename(ticker_benchmark)
datos_quantstats = pd.concat(
    [retornos_portafolio_quantstats, retornos_benchmark_quantstats], axis=1
).dropna()

st.subheader("Análisis histórico con QuantStats")
st.caption(
    f"Rendimiento histórico del portafolio optimizado frente a {nombre_benchmark}. "
    "Las métricas usan las observaciones comunes disponibles."
)
if len(datos_quantstats) < 2:
    st.warning("No hay suficientes retornos comunes para generar el análisis QuantStats.")
else:
    retornos_portafolio_quantstats = datos_quantstats["Portafolio Black Litterman"]
    retornos_benchmark_quantstats = datos_quantstats[ticker_benchmark]
    metricas_quantstats = calcular_metricas_quantstats(
        retornos_portafolio_quantstats
    )["Valor"]
    indicadores_porcentuales = {
        "Retorno anual compuesto",
        "Volatilidad anualizada",
        "Máxima caída",
        "Porcentaje de días positivos",
    }
    tabla_metricas_quantstats = pd.DataFrame(
        {
            "Indicador": metricas_quantstats.index,
            "Valor": [
                "N/D"
                if pd.isna(valor)
                else (
                    f"{valor:.2%}"
                    if indicador in indicadores_porcentuales
                    else f"{valor:.3f}"
                )
                for indicador, valor in metricas_quantstats.items()
            ],
        }
    )
    st.dataframe(
        tabla_metricas_quantstats,
        hide_index=True,
        width="stretch",
    )

    pestañas_quantstats = st.tabs(
        [
            "Rendimiento acumulado",
            "Drawdown",
            "Sharpe móvil",
            "Rendimientos mensuales",
            "Tearsheet completo",
        ]
    )
    with pestañas_quantstats[0]:
        figura = qs.plots.returns(
            retornos_portafolio_quantstats,
            benchmark=retornos_benchmark_quantstats,
            figsize=(10, 5),
            show=False,
        )
        st.pyplot(figura, clear_figure=True, width="stretch")
        plt.close(figura)
    with pestañas_quantstats[1]:
        figura = qs.plots.drawdown(
            retornos_portafolio_quantstats,
            figsize=(10, 4),
            show=False,
        )
        st.pyplot(figura, clear_figure=True, width="stretch")
        plt.close(figura)
    with pestañas_quantstats[2]:
        figura = qs.plots.rolling_sharpe(
            retornos_portafolio_quantstats,
            benchmark=retornos_benchmark_quantstats,
            figsize=(10, 4),
            show=False,
        )
        st.pyplot(figura, clear_figure=True, width="stretch")
        plt.close(figura)
    with pestañas_quantstats[3]:
        figura = qs.plots.monthly_heatmap(
            retornos_portafolio_quantstats,
            benchmark=retornos_benchmark_quantstats,
            figsize=(10, 5),
            show=False,
        )
        st.pyplot(figura, clear_figure=True, width="stretch")
        plt.close(figura)

    reporte_quantstats = generar_tearsheet_quantstats(
        retornos_portafolio_quantstats,
        retornos_benchmark_quantstats,
        titulo="Portafolio Black Litterman",
        nombre_archivo="reporte_quantstats_black_litterman.html",
    )
    with pestañas_quantstats[4]:
        components.html(reporte_quantstats, height=1800, scrolling=True)
        st.download_button(
            "Descargar tearsheet de Black-Litterman",
            data=reporte_quantstats,
            file_name="reporte_quantstats_black_litterman.html",
            mime="text/html",
            key="descargar_reporte_quantstats_black_litterman",
        )

with st.expander("Covarianza posterior"):
    st.dataframe(
        resultado.covarianza_posterior.style.format("{:.6f}"),
        width="stretch",
    )