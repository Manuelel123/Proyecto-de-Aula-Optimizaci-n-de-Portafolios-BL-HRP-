import yfinance as yf
import pandas as pd
from datetime import date, timedelta

activos = ["AAPL", "MSFT", "GOOGL", "BTC-USD"]
fecha_actual = date.today()
try:
	fecha_inicio = fecha_actual.replace(year=fecha_actual.year - 2)
except ValueError:  # 29 de febrero en un año no bisiesto
	fecha_inicio = fecha_actual.replace(year=fecha_actual.year - 2, day=28)
fecha_fin = fecha_actual + timedelta(days=1)

precios = yf.download(
	activos,
	start=fecha_inicio,
	end=fecha_fin,
	interval="1d",
	auto_adjust=False,
	progress=False,
)["Close"]

precios.index.name = "Fecha"
precios = pd.DataFrame(precios)

print("Precios de cierre:")
print(precios.head())
print("\nValores faltantes por activo:")
print(precios.isna().sum())

# En un notebook, deja esta variable como última expresión para verla completa:
precios
