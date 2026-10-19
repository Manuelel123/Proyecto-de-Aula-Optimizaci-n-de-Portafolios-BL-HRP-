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

## Usuarios y acceso

Las cuentas son **locales**: viven en la tabla `users` de la base SQLite (ver la sección siguiente). El correo es el usuario (sin distinguir mayúsculas) y la contraseña se guarda solo como hash argon2id (`argon2-cffi`); nunca en texto plano ni en registros.

- **Todo requiere sesión.** Sin iniciar sesión solo se ven la portada (`/`), `/auth/registro`, `/auth/ingresar` y los archivos estáticos. Las páginas redirigen al inicio de sesión (con `next`) y los endpoints JSON (`/api/...`) responden 401. La regla está en `web/auth/guard.py` (`PUBLIC_ENDPOINTS`, `register_access_check`).
- **Registro e inicio de sesión:** contraseña de al menos 8 caracteres con letras y números. Tras 5 intentos fallidos en 15 minutos (por correo o por IP) el acceso se bloquea temporalmente. El mensaje de error no revela si el correo existe.
- **Roles:** `explorador` (por defecto), `inversionista` y `gestor`. `gestor` no se puede elegir: lo reciben solo los correos de `GESTOR_EMAILS` (separados por coma), al registrarse o al iniciar sesión; si un correo sale de la lista, vuelve a `explorador`.
- **Historial de backtests:** cada backtest HRP se guarda una vez por combinación de activos, periodo y parámetros (`/cuenta/backtests`), en la base local.
- **Migrar portafolios de las antiguas cuentas de InsForge:** registra la cuenta local y ejecuta `flask --app wsgi:app users reassign-portfolios <id_insforge> <correo>`.

Configuración local: copia `.env.example` a `.env` y completa las variables. Define `FLASK_SECRET_KEY` (si falta, la app avisa en el log y las sesiones se pierden al reiniciar).

### Caché de precios (InsForge, opcional)

[InsForge](https://insforge.dev) ya no gestiona cuentas: solo aporta, si se configuran `INSFORGE_URL` e `INSFORGE_API_KEY`, una caché persistente de precios en Postgres. Los cierres ajustados se guardan en `price_history` y se reutilizan; al optimizar solo se descargan los días que faltan desde el último guardado (más 7 días de solapamiento). Si el solapamiento no coincide con lo guardado (un dividendo o split reajustó la serie), o si la última descarga completa tiene más de 7 días (`ATLAS_PRICE_FULL_REFRESH_DAYS`), se vuelve a descargar el historial completo de ese activo. Un rango que llega a hoy se considera vigente durante 60 minutos (`ATLAS_PRICE_INTRADAY_TTL_MINUTES`), porque el precio del día puede seguir cambiando. `INSFORGE_API_KEY` es una clave de administrador: solo la usa el servidor y nunca debe subirse al repositorio. El esquema de esas tablas está en `migrations/` (`npx -y @insforge/cli db migrations up --all`).

## Portafolios en seguimiento (base de datos local)

Los portafolios fijados como óptimos, sus versiones, posiciones y operaciones se guardan en una base SQL local con Flask-SQLAlchemy:

- **Ubicación:** `DATABASE_URL` (por defecto `sqlite:///instance/atlas.db`; la carpeta `instance/` está fuera del control de versiones). `ATLAS_INSTANCE_PATH` cambia la carpeta `instance/`.
- **Migraciones:** son revisiones de Flask-Migrate en `src/optimizacion_portafolios/storage/migrations`. La app aplica las pendientes al arrancar; se desactiva con `ATLAS_AUTO_MIGRATE=0`. Para hacerlo a mano:

  ```powershell
  uv run flask --app wsgi:app db upgrade --directory src/optimizacion_portafolios/storage/migrations
  ```

  Tras cambiar `storage/models.py`, genera la revisión con `db migrate -m "..."` y el mismo `--directory`.
- **Fijar un portafolio óptimo:** en los resultados de HRP y Black-Litterman, con la sesión iniciada, el botón «Establecer como portafolio óptimo» pide nombre, capital nocional (100 000 USD por defecto), benchmark y comisión (10 pb por defecto). Se guardan el modelo, todos sus parámetros (universo, fechas, límites de peso, views, objetivo, tasa libre de riesgo), los pesos objetivo, los últimos precios de cierre y las unidades (capital neto de comisión × peso / precio). La fecha de esos precios es el inicio del seguimiento.
- **Valoración:** comprar y mantener en USD con las unidades vigentes. Los cierres son ajustados, así que los dividendos se reinvierten implícitamente; un activo que deja de cotizar conserva su último precio. Los precios en otra moneda se convierten con el tipo de cambio diario de Yahoo (`{CCY}USD=X`); los precios en peniques (`GBp`) se dividen entre 100.
- **Métricas de seguimiento** (`analytics/tracking.py`, días hábiles, 252 por año): retorno acumulado, del mes, del año y del último año (N/D hasta tener un año), CAGR y contribución al retorno por activo; volatilidad anualizada y móvil (21 y 63 días), drawdown actual, máximo y su duración, VaR y CVaR históricos al 95 % (diarios), semidesviación y contribución al riesgo; Sharpe, Sortino (QuantStats), Calmar y Sharpe móvil de 126 días; beta, alpha de Jensen, tracking error, information ratio y capturas alcista y bajista frente al benchmark; desviación de pesos, índice Herfindahl, número efectivo de activos, correlación promedio móvil (63 días) y rotación acumulada. La tasa libre de riesgo es la del modelo (Black-Litterman) o 0.
- **Fotos diarias (snapshots):** cada día valorado guarda valor, valor del benchmark, pesos y, el último día, todas las métricas (`DailySnapshot`). Los días pasados no se reescriben aunque el proveedor revise sus precios; solo se actualiza la foto del último día. Al abrir el panel se guardan las fotos que falten.

  ```powershell
  uv run flask --app wsgi:app portfolios snapshot            # hasta hoy
  uv run flask --app wsgi:app portfolios snapshot --date 2026-10-16
  ```

  El comando termina con código 1 si algún portafolio falla (el resto se guarda igual).

  **Programarlo en Windows (Programador de tareas):** abre «Programador de tareas» → «Crear tarea básica…», nombre `Atlas snapshot`, desencadenador «Diariamente» a las 18:30 (después del cierre de EE. UU.), acción «Iniciar un programa» con:

  - Programa: `powershell.exe`
  - Argumentos: `-NoProfile -Command "uv run flask --app wsgi:app portfolios snapshot *>> instance\snapshot.log"`
  - Iniciar en: la carpeta del proyecto (donde están `wsgi.py` y `.env`).

  O con PowerShell, desde la carpeta del proyecto:

  ```powershell
  $action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory (Get-Location) `
    -Argument '-NoProfile -Command "uv run flask --app wsgi:app portfolios snapshot *>> instance\snapshot.log"'
  $trigger = New-ScheduledTaskTrigger -Daily -At 6:30pm
  Register-ScheduledTask -TaskName "Atlas snapshot" -Action $action -Trigger $trigger
  ```

- **Alertas** (`analytics/alerts.py`): reglas por portafolio con semáforo verde/amarillo/rojo, editables al fijar el portafolio y en `/portafolios/<id>/alertas`. Un nivel se alcanza cuando el valor supera estrictamente su umbral:

  | Regla | Amarillo | Rojo |
  |---|---|---|
  | Drawdown actual | > 10 % | > 15 % |
  | Volatilidad móvil de 21 días frente a su promedio desde la fijación | > 1,5× | > 2× |
  | Mayor desviación de un peso frente al objetivo | > 5 pp | > 10 pp |
  | Días seguidos con Sharpe móvil de 126 días negativo | > 63 | > 126 |
  | Mayor participación de un activo en el riesgo total | > 30 % | > 40 % |
  | Correlación promedio móvil de 63 días | > 0,70 | > 0,85 |
  | Días hábiles sin precio nuevo de un activo (posible baja de cotización) | > 2 | > 5 |

  Las reglas se evalúan al abrir el panel y con `flask portfolios snapshot`. Cada evento guarda cuándo se disparó y cuándo se resolvió; un cambio de nivel cierra el evento anterior y abre uno nuevo. No se envían correos ni notificaciones.
- **Gestión** (`/portafolios/<id>/gestionar`, lógica en `models/rebalancing.py`): cada cambio crea una versión nueva con motivo, fecha, pesos, unidades, operaciones y costo, sin reiniciar el seguimiento.
  - **Rebalancear:** a los pesos objetivo vigentes o re-optimizando con el mismo modelo (HRP con la misma longitud de ventana; Black-Litterman con sus views, objetivo y tasa libre de riesgo) y datos hasta hoy.
  - **Eliminar activos:** el peso liberado se reparte en proporción o re-optimizando con los activos restantes.
  - **Agregar activos:** con peso manual (el resto se reduce en proporción) o re-optimizando con el nuevo universo (en Black-Litterman hay que indicar la view del activo nuevo). El ticker se valida descargando sus precios.
  - **Vista previa:** antes de confirmar se muestran los pesos antes y después, las compras y ventas, el turnover (de una vía: la mitad de lo operado sobre el valor) y el costo (comisión del portafolio en pb sobre cada monto operado). La vista previa queda firmada; si el portafolio cambia antes de confirmar, se rechaza.
  - Se respetan los límites de peso de HRP (`WeightBounds`) en todas las operaciones; si no existe una asignación que los cumpla, el cambio se rechaza con un mensaje.
  - Las operaciones usan el último cierre disponible. Si el mercado está abierto, ese precio todavía puede moverse y la valoración del día se ajustará al cierre.
  - El valor es continuo: el día del cambio, el portafolio vale lo que valía antes menos el costo.
  - **Versiones:** historial en `/portafolios/<id>/versiones`, con comparación de dos versiones (`?a=1&b=3`).
  - **Archivar:** el portafolio deja de monitorearse (sin snapshots ni alertas) y conserva todo su historial.
- **Señales** (`/portafolios/<id>/senales`, `models/signals.py`): reglas por activo simuladas con vectorbt (`from_signals` con `group_by` y `cash_sharing`) sobre los pesos vigentes desde la fecha de fijación, frente a comprar y mantener. Un activo está «dentro» mientras se cumplen las reglas activas: media rápida sobre la lenta (50/200 días), volatilidad de 21 días como máximo 2× su mediana y drawdown del activo menor al 20 %. Aparte, un stop-loss del 10 % (dinámico por defecto) cierra la posición. Cada entrada compra peso objetivo × capital, porque el simulador de señales de vectorbt no admite tamaños de porcentaje objetivo. Son sugerencias: nunca modifican el portafolio; para actuar hay que usar Gestionar, que pide confirmación. La primera simulación tarda más mientras numba compila.
- **Usuarios:** son las cuentas locales (`users`). Flask-Login gestiona la sesión (`current_user`) y cada fila guarda `str(users.id)` en `user_id`; un portafolio ajeno responde 404.

## Pruebas

```powershell
uv run pytest -q
```

`uv run python -m unittest discover -s tests` ejecuta la misma suite sin pytest.

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
    fx.py                       moneda de cada ticker y conversión diaria a USD
  analytics/
    tracking.py                 seguimiento de portafolios fijados (funciones puras)
    alerts.py                   reglas de alerta y su evaluación (funciones puras)
    statistics.py               retornos, riesgo y retornos esperados
    volatility.py               volatilidad mensual e histórica por periodo
    performance.py              métricas e informe QuantStats
  models/
    hrp.py                      pesos HRP y contribuciones
    rebalancing.py              cambios de portafolio: límites, operaciones, costo y turnover
    signals.py                  señales de entrada y salida por activo (vectorbt)
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
    auth/                       cuentas locales: registro, sesión (Flask-Login), roles, guardia global, límite de intentos
    account/                    historial de backtests HRP
    portfolios/                 Mis portafolios: seguimiento de portafolios óptimos
  storage/
    models.py                   tablas SQLAlchemy (User, BacktestRun, BacktestResult, Portfolio, PortfolioVersion, Holding, Transaction, DailySnapshot, AlertRule, AlertEvent)
    repositories.py             lecturas y escrituras filtradas por usuario
    users.py                    cuentas locales y reasignación de dueño
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
