import streamlit as st

from optimizacion_portafolios.ui import configurar_pagina, mostrar_seguimiento


TICKERS_OPCIONES = (
    "UEC",
    "HAI",
    "VG",
    "PR",
    "UUUU",
    "EQT",
    "DVN",
    "PTEN",
    "NESR",
    "APA",
    "CRK",
    "MGY",
    "AESI",
    "PUMP",
    "CRGY",
    "NOV",
    "SM",
    "LBRT",
    "AR",
    "WTTR",
    "NEXT",
    "NOG",
    "AM",
    "RES",
    "BTU",
    "RRC",
    "MUR",
    "CNX",
    "EGY",
    "TALO",
    "VNOM",
    "BKV",
    "TTI",
    "WKC",
    "PAA",
    "NE",
    "AROC",
    "HP",
)
ACTIVOS_OPCIONES = {ticker: ticker for ticker in TICKERS_OPCIONES}


fecha_actual, fecha_inicio = configurar_pagina("Monitoreo de activos")

st.title("Monitoreo de activos e índices")
st.caption("Consulta precios, rendimiento y riesgo de los activos seleccionados")

tab_opciones, tab_portafolio = st.tabs(
    [
        "Activos monitoreo: Opciones Financieras",
        "Activos monitoreo: portafolio de inversión",
    ]
)

with tab_opciones:
    mostrar_seguimiento(
        "Opciones Financieras",
        "Precios, retornos y estadísticas clave de los activos de las imágenes.",
        "opciones_financieras",
        fecha_inicio,
        fecha_actual,
        ACTIVOS_OPCIONES,
        {},
        list(ACTIVOS_OPCIONES),
    )

with tab_portafolio:
    pass
