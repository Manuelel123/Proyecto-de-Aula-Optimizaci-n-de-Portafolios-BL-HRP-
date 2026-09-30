import numpy as np
import pandas as pd
import streamlit as st

from optimizacion_portafolios.black_litterman import optimizar_black_litterman
from optimizacion_portafolios.ui import (
    ACTIVOS,
    ACTIVOS_HRP,
    BENCHMARKS,
    COMMODITIES,
    CRIPTOMONEDAS,
    PORTAFOLIO_COLOMBIA,
    PORTAFOLIO_IMAGEN,
    configurar_pagina,
    descargar_precios,
)


fecha_actual, fecha_inicio = configurar_pagina("Optimización Black Litterman")

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

    prior_seleccionado = st.radio(
        "Prior de equilibrio",
        ["Pesos iguales", "Capitalización bursátil"],
        horizontal=True,
        help=(
            "Ambos priors calculan retornos de equilibrio. Pesos iguales asigna el "
            "mismo peso de mercado; capitalización bursátil usa los valores ingresados."
        ),
        key="bl_prior",
    )
    col_objetivo, col_tasa = st.columns(2)
    with col_objetivo:
        objetivo_seleccionado = st.selectbox(
            "Objetivo de optimización",
            ["Máximo Sharpe", "Mínima volatilidad"],
            key="bl_objetivo",
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
            "Fecha inicial de los datos",
            value=fecha_inicio,
            max_value=fecha_actual,
            key="bl_fecha_inicio",
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
        if prior_seleccionado == "Capitalización bursátil":
            columnas_vistas["Capitalización (USD, miles de millones)"] = [
                1.0
            ] * len(tickers)
        columnas_vistas["View anual (%)"] = [8.0] * len(tickers)
        columnas_vistas["Confianza (%)"] = [50.0] * len(tickers)
        vistas_iniciales = pd.DataFrame(columnas_vistas)
        st.subheader("Views individuales")
        st.caption(
            "Define un retorno esperado anual por activo y cuánto confías en esa view. "
            "La confianza debe estar entre 1% y 99%."
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
                format="%.0f",
            ),
        }
        if prior_seleccionado == "Capitalización bursátil":
            configuracion_columnas[
                "Capitalización (USD, miles de millones)"
            ] = st.column_config.NumberColumn(
                min_value=0.001,
                step=1.0,
                format="%.3f",
            )
        vistas_editadas = st.data_editor(
            vistas_iniciales,
            column_config=configuracion_columnas,
            disabled=["Activo", "Ticker"],
            hide_index=True,
            width="stretch",
            key=f"bl_vistas_{prior_seleccionado}_{'_'.join(tickers)}",
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
    st.error("Completa las views, confianzas y capitalizaciones requeridas.")
    st.stop()

tickers_vistas = vistas_editadas["Ticker"].tolist()
vistas_absolutas = {
    fila["Ticker"]: float(fila["View anual (%)"]) / 100
    for _, fila in vistas_editadas.iterrows()
}
confianzas = {
    fila["Ticker"]: float(fila["Confianza (%)"]) / 100
    for _, fila in vistas_editadas.iterrows()
}
capitalizaciones = {ticker: 1.0 for ticker in tickers}
if prior_seleccionado == "Capitalización bursátil":
    columna_capitalizacion = "Capitalización (USD, miles de millones)"
    capitalizaciones = {
        fila["Ticker"]: float(fila[columna_capitalizacion])
        for _, fila in vistas_editadas.iterrows()
    }

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
        if capitalizaciones is not None:
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
                else "min_volatility"
            ),
            tasa_libre_riesgo=tasa_libre_riesgo_pct / 100,
            capitalizaciones=(
                {ticker: capitalizaciones[ticker] for ticker in precios.columns}
                if capitalizaciones is not None
                else None
            ),
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

with st.expander("Covarianza posterior"):
    st.dataframe(
        resultado.covarianza_posterior.style.format("{:.6f}"),
        width="stretch",
    )