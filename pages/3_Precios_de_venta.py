from datetime import timedelta

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from optimizacion_portafolios.arima import (
    analizar_arima,
    backtest_arima,
    pronosticar_precios,
)
from optimizacion_portafolios.ui import ACTIVOS, configurar_pagina, descargar_serie_arima


fecha_actual, _ = configurar_pagina("Precios de venta de opciones")

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
for advertencia in resultado_arima.advertencias:
    st.warning(advertencia)
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
            {
                "aic": "{:.2f}",
                "bic": "{:.2f}",
                "hqic": "{:.2f}",
                "sse": "{:.6f}",
                "p_valor_ljung_box_min": "{:.4f}",
                "rmse_rolling_retorno": "{:.6f}",
                "rmse_validacion_retorno": "{:.6f}",
                "rmse_validacion_precio": "{:.4f}",
            }
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption("Se priorizan residuos blancos por Ljung-Box y luego parsimonia por AIC.")
with tab_diagnostico:
    st.subheader("Estacionariedad (ADF + KPSS)")
    st.dataframe(
        resultado_arima.estacionariedad.style.format(
            {
                "adf_estadistico": "{:.4f}",
                "adf_p_valor": "{:.4f}",
                "kpss_estadistico": "{:.4f}",
                "kpss_p_valor": "{:.4f}",
            }
        ),
        hide_index=True,
        width="stretch",
    )
    st.line_chart(resultado_arima.retornos_log, y_label="Retorno logarítmico", x_label="Fecha")
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
    futuro = pronostico_arima[
        ["precio_pronosticado", "precio_ic_inferior", "precio_ic_superior"]
    ].rename(
        columns={
            "precio_pronosticado": "Precio pronosticado",
            "precio_ic_inferior": "Intervalo inferior",
            "precio_ic_superior": "Intervalo superior",
        }
    )
    st.line_chart(pd.concat([historico, futuro]), y_label="Precio", x_label="Fecha")
    st.dataframe(
        pronostico_arima.style.format(
            {
                "retorno_log_pronosticado": "{:.4%}",
                "precio_pronosticado": "${:,.2f}",
                "precio_ic_inferior": "${:,.2f}",
                "precio_ic_superior": "${:,.2f}",
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
            backtest.index,
            backtest["precio_pronosticado"],
            color="#d95f02",
            marker="x",
            linestyle="--",
            linewidth=2.2,
            label="Pronóstico ARIMA",
        )
        eje.fill_between(
            backtest.index,
            backtest["precio_ic_inferior"],
            backtest["precio_ic_superior"],
            color="#d95f02",
            alpha=0.15,
            label="Intervalo de confianza",
        )
        eje.axvline(
            inicio_prueba,
            color="#555555",
            linestyle=":",
            linewidth=1.5,
            label="Inicio de la prueba",
        )
        eje.set_title("Validación fuera de muestra")
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
                    "precio_ic_inferior": "${:,.2f}",
                    "precio_ic_superior": "${:,.2f}",
                    "error_porcentual": "{:.2f}%",
                }
            ),
            width="stretch",
        )
    except ValueError as error:
        st.warning(str(error))
