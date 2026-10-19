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

## Cuentas y base de datos (InsForge)

[InsForge](https://insforge.dev) aporta el login y una base de datos Postgres. Todo es opcional: sin configuración, la app funciona igual que antes, sin cuentas y sin caché persistente.

- **Login:** registro con correo y contraseña verificado con un código de 6 dígitos, recuperación de contraseña con código y acceso con Google o GitHub (OAuth con PKCE). Rutas en `/auth/...`.
- **Mis portafolios:** los resultados de HRP y Black-Litterman se pueden guardar desde la página del resultado (`/cuenta/portafolios`).
- **Historial de backtests:** con la sesión iniciada, cada backtest HRP se guarda una vez por combinación de activos, periodo y parámetros (`/cuenta/backtests`).
- **Caché de precios incremental:** los cierres ajustados se guardan en `price_history` y se reutilizan; al optimizar solo se descargan los días que faltan desde el último guardado (más 7 días de solapamiento). Si el solapamiento no coincide con lo guardado (un dividendo o split reajustó la serie), o si la última descarga completa tiene más de 7 días (`ATLAS_PRICE_FULL_REFRESH_DAYS`), se vuelve a descargar el historial completo de ese activo. Un rango que llega a hoy se considera vigente durante 60 minutos (`ATLAS_PRICE_INTRADAY_TTL_MINUTES`), porque el precio del día puede seguir cambiando.

Configuración local: copia `.env.example` a `.env` y completa las variables. Los valores del proyecto vinculado están en `.insforge/project.json` y en `npx -y @insforge/cli secrets get ANON_KEY`. `INSFORGE_API_KEY` es una clave de administrador: solo la usa el servidor, para la caché de precios, y nunca debe subirse al repositorio.

El esquema está en `migrations/` y se aplica con `npx -y @insforge/cli db migrations up --all`. Las tablas de usuario tienen RLS (cada usuario solo ve sus filas) y las de precios solo son accesibles con la clave de administrador. La configuración de auth, incluidas las URLs de retorno de OAuth permitidas, está en `insforge.toml` (`npx -y @insforge/cli config apply`). Si sirves la app en otra URL, agrega su `/auth/oauth/callback` a `allowed_redirect_urls`.

## Portafolios en seguimiento (base de datos local)

Los portafolios fijados como óptimos, sus versiones, posiciones y operaciones se guardan en una base SQL local con Flask-SQLAlchemy:

- **Ubicación:** `DATABASE_URL` (por defecto `sqlite:///instance/atlas.db`; la carpeta `instance/` está fuera del control de versiones). `ATLAS_INSTANCE_PATH` cambia la carpeta `instance/`.
- **Migraciones:** son revisiones de Flask-Migrate en `src/optimizacion_portafolios/storage/migrations`. La app aplica las pendientes al arrancar; se desactiva con `ATLAS_AUTO_MIGRATE=0`. Para hacerlo a mano:

  ```powershell
  uv run flask --app wsgi:app db upgrade --directory src/optimizacion_portafolios/storage/migrations
  ```

  Tras cambiar `storage/models.py`, genera la revisión con `db migrate -m "..."` y el mismo `--directory`.
- **Usuarios:** son las cuentas de InsForge. Flask-Login gestiona la sesión (`current_user`) y cada fila guarda el id de usuario de InsForge; un portafolio ajeno responde 404. Tras 5 intentos fallidos de inicio de sesión en 15 minutos (por correo o por IP), el acceso se bloquea temporalmente.

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
- **Optimización HRP** ([skfolio](https://skfolio.org/) `HierarchicalRiskParity`, enlace *single*): selección de activos, benchmark, límites de peso mínimo y máximo (globales y por activo), pesos, correlación cuasi-diagonal, contribuciones, ratio de Sortino, métricas de riesgo, gráficos QuantStats e informe HTML descargable.
- **Black-Litterman** (skfolio `BlackLitterman` + `MeanRisk`): prior de equilibrio por capitalización bursátil en USD, views anuales por activo editables en la interfaz con confianza fija del 95 % (método de Idzorek), objetivos de optimización (máximo Sharpe, máximo Sortino, mínima varianza, mínimo CVaR, máxima rentabilidad, máxima utilidad), retornos prior y posterior, covarianza posterior, ratio de Sortino esperado y análisis/informe QuantStats histórico.

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
    auth/                       inicio de sesión con InsForge (Flask-Login, límite de intentos)
    portfolios/                 Mis portafolios: seguimiento de portafolios óptimos
  storage/
    models.py                   tablas SQLAlchemy (Portfolio, PortfolioVersion, Holding, Transaction)
    repositories.py             lecturas y escrituras filtradas por usuario
    migrations/                 revisiones de Flask-Migrate
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
