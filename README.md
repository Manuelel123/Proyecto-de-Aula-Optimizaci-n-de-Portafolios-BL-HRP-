# Atlas · Analítica de portafolios

Aplicación web Flask para monitorear activos y analizar portafolios con Hierarchical Risk Parity (HRP) y Black-Litterman. Los precios y datos fundamentales se consultan en Yahoo Finance.

## Requisitos

- Python 3.14 o superior
- [uv](https://docs.astral.sh/uv/)

## Instalación y ejecución local

```powershell
uv sync
uv run flask --app wsgi:app run --debug
```

Abre `http://127.0.0.1:5000`. Para iniciar el servidor local sin el CLI:

```powershell
uv run optimizacion-portafolios
```

## Despliegue WSGI

Waitress está incluido para ejecutar Flask en Windows y otros sistemas:

```powershell
uv run waitress-serve --host 0.0.0.0 --port 8000 wsgi:app
```

Configura `FLASK_SECRET_KEY` con un valor aleatorio secreto al desplegar y habilita `FLASK_SESSION_COOKIE_SECURE=true` cuando la aplicación se sirva exclusivamente por HTTPS. El servidor Flask integrado es para desarrollo; Waitress es el servidor WSGI de producción.

## Módulos

- **Monitoreo de activos:** universos de opciones y portafolios, tickers personalizados, fechas, precios ajustados, retornos, retornos anualizados, riesgo, volatilidad histórica por periodo y análisis fundamental.
- **Optimización HRP:** selección de activos, benchmark, pesos, correlación, contribuciones, métricas de riesgo, gráficos QuantStats e informe HTML descargable.
- **Black-Litterman:** views individuales, objetivos de optimización, prior de mercado, capitalizaciones en USD, pesos posteriores, covarianza y análisis/informe QuantStats.

La aplicación se organiza en módulos Flask independientes al estilo de las aplicaciones Django. Cada módulo (`main`, `monitoring`, `hrp` y `black_litterman`) registra su propio Blueprint y agrupa rutas y plantillas; `common` contiene helpers compartidos. Los formularios están protegidos con tokens CSRF. Los gráficos se generan en el servidor y la interfaz no necesita un servicio externo de gráficos.

## Estructura

```text
app.py
wsgi.py
src/optimizacion_portafolios/
  analytics.py
  catalogs.py
  market_data.py
  black_litterman.py
  app/
    __init__.py
    templates/
    static/
    common/
      charts.py
      forms.py
      portfolio_views.py
    main/
      __init__.py
      routes.py
      templates/main/
    monitoring/
      __init__.py
      routes.py
      templates/monitoring/
    hrp/
      __init__.py
      routes.py
      templates/hrp/
    black_litterman/
      __init__.py
      routes.py
      templates/black_litterman/
```

Los servicios ARIMA permanecen disponibles en `src/optimizacion_portafolios/arima.py` para que puedan integrarse como módulo Flask en una etapa posterior.
