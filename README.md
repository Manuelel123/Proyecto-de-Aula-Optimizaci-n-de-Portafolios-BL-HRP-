# Proyecto-de-Aula-Optimizaci-n-de-Portafolios-BL-HRP-
Optimización de Portafolios bajo los modelos BlacK Litterman &amp; Paridad por Riesgo Jerárquico.

## Ejecutar la aplicación

Desde la raíz del proyecto, ejecuta únicamente el archivo principal:

```bash
uv run streamlit run app.py
```

Streamlit detecta automáticamente las páginas dentro de `pages/`:

- `1_Monitoreo.py`: seguimiento de activos e índices.
- `2_Optimización_HRP.py`: optimización mediante HRP.
- `3_Precios_de_venta.py`: análisis y pronóstico ARIMA.

Las constantes, descargas y funciones compartidas están en
`src/optimizacion_portafolios/ui.py`.
