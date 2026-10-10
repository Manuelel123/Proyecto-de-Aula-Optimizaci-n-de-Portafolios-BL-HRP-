"""Asset and portfolio catalogs used by the web application."""

ACTIVOS = {
    "Apple (AAPL)": "AAPL",
    "Microsoft (MSFT)": "MSFT",
    "Alphabet (GOOGL)": "GOOGL",
    "Bitcoin (BTC-USD)": "BTC-USD",
}
INDICES = {
    "Sector salud (XLV)": "XLV",
    "Sector financiero (XLF)": "XLF",
    "Sector tecnología (XLK)": "XLK",
    "Sector energía (XLE)": "XLE",
}
CRIPTOMONEDAS = {
    "Bitcoin (BTC-USD)": "BTC-USD",
    "Ethereum (ETH-USD)": "ETH-USD",
    "BNB (BNB-USD)": "BNB-USD",
    "XRP (XRP-USD)": "XRP-USD",
}
COMMODITIES = {
    "Oro (GC=F)": "GC=F",
    "Plata (SI=F)": "SI=F",
    "Petróleo WTI (CL=F)": "CL=F",
    "Gas natural (NG=F)": "NG=F",
    "Cobre (HG=F)": "HG=F",
}
PORTAFOLIO_IMAGEN = {
    "Sector salud (XLV)": "XLV",
    "Sector energía (XLE)": "XLE",
    "Sector industrial (XLI)": "XLI",
    "Sector financiero (XLF)": "XLF",
    "Apple (AAPL)": "AAPL",
    "Johnson & Johnson (JNJ)": "JNJ",
    "Gilead Sciences (GILD)": "GILD",
    "SPDR Gold Shares (GLD)": "GLD",
    "Visa (V)": "V",
    "Bank of America (BAC)": "BAC",
    "Elevance Health (ELV)": "ELV",
    "Microsoft (MSFT)": "MSFT",
    "Eli Lilly (LLY)": "LLY",
    "Sector tecnología (XLK)": "XLK",
    "Sector servicios públicos (XLU)": "XLU",
    "Tesla (TSLA)": "TSLA",
    "Robinhood Markets (HOOD)": "HOOD",
    "Hims & Hers Health (HIMS)": "HIMS",
    "Coinbase Global (COIN)": "COIN",
}
PORTAFOLIO_ACCIONES_IMAGEN = {
    "Syntec Optics (OPTX)": "OPTX",
    "Fortive (FTV)": "FTV",
    "JinkoSolar (JKS)": "JKS",
    "Synaptics (SYNA)": "SYNA",
    "Standex International (SXI)": "SXI",
    "Credo Technology (CRDO)": "CRDO",
    "CTS Corporation (CTS)": "CTS",
    "Universal Display (OLED)": "OLED",
    "Himax Technologies (HIMX)": "HIMX",
    "Knowles (KN)": "KN",
    "A10 Networks (ATEN)": "ATEN",
    "Wolfspeed (WOLF)": "WOLF",
    "Advanced Micro Devices (AMD)": "AMD",
    "Exxon Mobil (XOM)": "XOM",
    "NextEra Energy (NEE)": "NEE",
    "Occidental Petroleum (OXY)": "OXY",
    "APA Corporation (APA)": "APA",
    "Chevron (CVX)": "CVX",
    "Friedman Industries (FRD)": "FRD",
    "Enviri (NVRI)": "NVRI",
    "Uranium Royalty (UROY)": "UROY",
    "Mineros (MINEROS)": "MINEROS.CL",
    "SPDR Gold Shares (GLD)": "GLD",
}
PORTAFOLIO_COLOMBIA = {
    ticker: f"{ticker}.CL"
    for ticker in (
        "PFCIBEST",
        "CIBEST",
        "ISA",
        "ECOPETROL",
        "PFGRUPSURA",
        "GRUPOSURA",
        "GRUPOARGOS",
        "CEMARGOS",
        "PFGRUPOARG",
        "PFDAVVIGR",
        "PFAVAL",
        "CELSIA",
        "PEI",
        "CORFICOLOCF",
        "MINEROS",
        "GRUBOLIVAR",
        "BOGOTA",
        "TERPEL",
        "EXITO",
        "PROMIGAS",
        "PFCORFICOL",
    )
}
ACTIVOS_HRP = {
    **ACTIVOS,
    **INDICES,
    **CRIPTOMONEDAS,
    **COMMODITIES,
    **PORTAFOLIO_IMAGEN,
    **PORTAFOLIO_ACCIONES_IMAGEN,
    **PORTAFOLIO_COLOMBIA,
}
BENCHMARKS = {
    "S&P 500 (SPY)": "SPY",
    "Nasdaq 100 (QQQ)": "QQQ",
    "Dow Jones (DIA)": "DIA",
    "Russell 2000 (IWM)": "IWM",
    "Mercado global (VT)": "VT",
    "Índice S&P 500 (^GSPC)": "^GSPC",
    "Bitcoin (BTC-USD)": "BTC-USD",
}
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
PORTAFOLIOS_MONITOREO = {
    "Portafolio actual": PORTAFOLIO_IMAGEN,
    "Portafolio imagen (23 activos)": PORTAFOLIO_ACCIONES_IMAGEN,
}
UNIVERSOS_HRP = {
    "Selección directa de activos": ACTIVOS_HRP,
    "Criptomonedas y commodities": {**CRIPTOMONEDAS, **COMMODITIES},
    "Portafolio actual": PORTAFOLIO_IMAGEN,
    "Portafolio imagen (23 activos)": PORTAFOLIO_ACCIONES_IMAGEN,
    "Portafolio Colombia": PORTAFOLIO_COLOMBIA,
}
UNIVERSOS_BLACK_LITTERMAN = {
    "Activos principales": ACTIVOS,
    "Criptomonedas y commodities": {**CRIPTOMONEDAS, **COMMODITIES},
    **PORTAFOLIOS_MONITOREO,
    "Portafolio Colombia": PORTAFOLIO_COLOMBIA,
    "Selección directa de activos": ACTIVOS_HRP,
}
PERIODOS_VOLATILIDAD = {
    "Semanal (7 días)": "W-FRI",
    "Quincenal (15 días)": "15D",
    "Mensual": "ME",
    "45 días": "45D",
    "Dos meses": "2ME",
}
# Display groups for the asset selector. A universe whose tickers all belong
# to one group is shown ungrouped; otherwise each ticker takes the first
# matching group (crypto goes before "Acciones" so BTC-USD is not a stock).
GRUPOS_ACTIVOS = {
    "Criptomonedas": CRIPTOMONEDAS,
    "Acciones": ACTIVOS,
    "Índices sectoriales": INDICES,
    "Commodities": COMMODITIES,
    "Portafolio actual": PORTAFOLIO_IMAGEN,
    "Portafolio imagen": PORTAFOLIO_ACCIONES_IMAGEN,
    "Colombia": PORTAFOLIO_COLOMBIA,
    "Opciones": ACTIVOS_OPCIONES,
}
