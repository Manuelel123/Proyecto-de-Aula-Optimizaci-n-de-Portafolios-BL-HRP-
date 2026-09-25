import streamlit as st

from optimizacion_portafolios.ui import (
    ACTIVOS,
    COMMODITIES,
    CRIPTOMONEDAS,
    INDICES,
    configurar_pagina,
    mostrar_seguimiento,
)


fecha_actual, fecha_inicio = configurar_pagina("Monitoreo de activos")

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
