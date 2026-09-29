import streamlit as st

from optimizacion_portafolios.ui import ACTIVOS, INDICES, configurar_pagina


configurar_pagina("Dashboard de portafolios")

st.title("Dashboard de optimización de portafolios")
st.caption("Selecciona el módulo con el que quieres trabajar")

st.divider()
col_monitoreo, col_hrp, col_black_litterman = st.columns(3, gap="large")

with col_monitoreo:
    st.subheader("📈 Monitoreo de activos")
    st.write(
        "Consulta precios, retornos históricos y estadísticas de riesgo "
        "para activos e índices."
    )
    st.page_link("pages/1_Monitoreo.py", label="Abrir monitoreo", icon="📈")

with col_hrp:
    st.subheader("⚖️ Optimización HRP")
    st.write(
        "Construye portafolios con Paridad por Riesgo Jerárquico."
    )
    st.page_link(
        "pages/2_Optimización_HRP.py",
        label="Abrir HRP",
        icon="⚖️",
    )

with col_black_litterman:
    st.subheader("📊 Optimización Black Litterman")
    st.write("Combina un prior de mercado con views individuales por activo.")
    st.page_link(
        "pages/3_Optimización_Black_Litterman.py",
        label="Abrir Black Litterman",
        icon="📊",
    )

st.divider()
st.subheader("Resumen")
metrica_activos, metrica_modelos = st.columns(2)
metrica_activos.metric("Activos disponibles", len(ACTIVOS) + len(INDICES))
metrica_modelos.metric("Modelos de optimización", 2)
