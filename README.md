# Atlas · Analítica de portafolios

Aplicación web Flask para monitorear activos y analizar portafolios con Hierarchical Risk Parity (HRP) y Black-Litterman. Los precios históricos se descargan de Tiingo (con Yahoo Finance como respaldo) y los datos fundamentales, capitalizaciones y tipos de cambio se consultan en Yahoo Finance.

## Requisitos

- Python 3.14 o superior
- [uv](https://docs.astral.sh/uv/)

## Instalación y ejecución local

```powershell
uv sync
uv run flask --app wsgi:app run --debug
```

Abre `http://127.0.0.1:5000`. Para iniciar el servidor local sin el CLI de Flask:

```powershell
uv run optimizacion-portafolios
```

## Configuración de la fuente de precios

Los precios de cierre ajustados se descargan de [Tiingo](https://www.tiingo.com/) cuando la variable de entorno `TIINGO_API_KEY` está definida. Crea una cuenta gratuita, copia tu API key y guárdala en Windows con:

```powershell
setx TIINGO_API_KEY "tu-api-key"
```

`setx` solo afecta a las terminales nuevas: abre otra terminal antes de ejecutar la aplicación.

Yahoo Finance se usa como respaldo:

- Sin `TIINGO_API_KEY`, todos los precios se descargan de Yahoo Finance y se registra una advertencia una sola vez.
- Con la key, se envían a Yahoo Finance los tickers que Tiingo no cubre: acciones colombianas y otros listados fuera de EE. UU. (`.CL` o cualquier sufijo con punto), futuros y divisas (`GC=F`, `EURUSD=X`), índices (`^GSPC`, `^COLCAP`) y cualquier ticker para el que Tiingo responda 404 o no devuelva datos.
- Las criptomonedas `XXX-USD` (`BTC-USD`, `ETH-USD`, `BNB-USD`, `XRP-USD`) se piden al endpoint de cripto de Tiingo (`btcusd`, ...) y, si no hay datos, a Yahoo Finance.

Las series de ambas fuentes se alinean por fecha (sin hora ni zona horaria). Los errores HTTP de Tiingo distintos de 404 (key inválida, límite de solicitudes, fallas del servidor) se informan como error de descarga y no activan el respaldo.

## Pruebas

```powershell
uv run python -m unittest discover -s tests
```

Las pruebas no acceden a la red: las descargas de Tiingo y Yahoo Finance se simulan con `unittest.mock`.

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

## Arquitectura

El código está organizado en capas. Cada capa solo depende de las que están por debajo:

```text
web        →  interfaz Flask: rutas HTTP, servicios de página, plantillas
models     →  modelos de optimización y pronóstico (HRP, Black-Litterman, ARIMA)
analytics  →  estadísticas, volatilidad y métricas de desempeño
data       →  catálogos de activos y acceso a Tiingo/Yahoo Finance
```

`data`, `analytics` y `models` no importan Flask: se pueden usar desde un notebook, un script o las pruebas sin levantar la aplicación.

```text
wsgi.py                         punto de entrada WSGI (Flask CLI y Waitress)
tests/
  support.py                    precios sintéticos y cliente Flask con CSRF
  test_market_data.py           capa data
  test_analytics.py             capa analytics
  test_hrp_model.py             modelo HRP
  test_black_litterman_model.py modelo Black-Litterman
  test_web.py                   rutas HTTP de punta a punta
src/optimizacion_portafolios/
  data/
    catalogs.py                 universos, benchmarks y periodos de volatilidad
    market_data.py              precios (Tiingo + respaldo Yahoo) y perfiles con caché
    tiingo_prices.py            cliente Tiingo y enrutamiento de tickers por fuente
  analytics/
    statistics.py               retornos, riesgo y retornos esperados
    volatility.py               volatilidad mensual e histórica por periodo
    performance.py              métricas e informe QuantStats
  models/
    hrp.py                      pesos HRP y contribuciones
    black_litterman.py          prior de equilibrio, views y optimización
    arima.py                    flujo Box-Jenkins (pendiente de integrar en la web)
  web/
    __init__.py                 create_app(), CSRF, manejo de errores, run()
    templates/  static/         plantilla base, error y CSS compartidos
    common/
      charts.py                 gráficos Matplotlib/QuantStats como imágenes PNG
      tables.py                 tablas HTML escapadas y formatos
      forms.py                  lectura y validación de formularios
    main/                       panel de inicio
    monitoring/                 monitoreo y análisis fundamental
    hrp/                        optimización HRP
    black_litterman/            optimización Black-Litterman
```

Cada módulo web (`main`, `monitoring`, `hrp`, `black_litterman`) sigue la misma forma:

| Archivo | Responsabilidad |
|---|---|
| `__init__.py` | Declara el `Blueprint` |
| `routes.py` | Solo HTTP: lee el formulario, valida, muestra mensajes y renderiza |
| `services.py` | Orquesta datos → analytics → modelo y arma el diccionario que usa la plantilla |
| `templates/<módulo>/` | Plantillas Jinja del módulo |

## Convenciones para extender el proyecto

- **Nuevo cálculo o modelo:** va en `analytics/` o `models/`, sin importar Flask, con sus pruebas en `tests/test_analytics.py` o `tests/test_<modelo>_model.py`.
- **Nueva fuente de datos:** va en `data/`. Las llamadas de red se simulan en las pruebas.
- **Nueva página:** crea `web/<módulo>/` con `__init__.py`, `routes.py`, `services.py` y `templates/<módulo>/`, y registra el Blueprint en `web/__init__.py`.
- **Gráficos y tablas:** reutiliza `web/common/charts.py` y `web/common/tables.py` antes de crear funciones nuevas.
- **Nuevos universos de activos:** se agregan en `data/catalogs.py`.
- **Idioma:** el código nuevo usa identificadores en inglés y textos de interfaz en español. `models/black_litterman.py` y `models/arima.py` conservan su API original en español.
- **Integración de ARIMA:** cuando se incorpore, crea `web/forecast/` siguiendo la misma forma y reutiliza `models/arima.py`.
