"""HTTP tests for the monitoring page."""

import json
import re
import unittest
from unittest.mock import patch

from yfinance.exceptions import YFRateLimitError

from support import WebTestCase, make_prices

_TICKER_CHECKBOX = re.compile(r'<input type="checkbox"[^>]*name="tickers"')

_SERVICES = "optimizacion_portafolios.web.monitoring.services"


def _json_script(html: bytes, script_id: str):
    match = re.search(
        rb'<script type="application/json" id="' + script_id.encode() + rb'">(.*?)</script>',
        html,
        re.S,
    )
    if match is None:
        raise AssertionError(f"Missing JSON script {script_id}")
    return json.loads(match.group(1))


def _sections(html: bytes) -> dict[str, bytes]:
    """Body of each ``details.analysis-section`` keyed by ``data-section``."""
    parts = re.split(rb'<details class="analysis-section" data-section="', html)
    sections = {}
    for part in parts[1:]:
        key, _, body = part.partition(b'">')
        sections[key.decode()] = body
    return sections


class MonitoringPageTests(WebTestCase):
    @patch(f"{_SERVICES}.download_prices")
    def test_monitoring_post_renders_prices_and_risk_tables(self, download) -> None:
        download.return_value = make_prices(("AAPL", "MSFT"))
        response = self.post(
            "/monitoring",
            action="analyze",
            view="portfolio",
            universe="Portafolio actual",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT"],
            custom_tickers="",
        )
        html = response.data
        self.assertEqual(response.status_code, 200)
        download.assert_called_once()
        self.assertIn("Evolución de precios ajustados".encode(), html)
        self.assertIn(b'data-price-explorer="prices-monitoring"', html)
        prices = _json_script(html, "prices-monitoring")
        self.assertEqual([s["ticker"] for s in prices["series"]], ["AAPL", "MSFT"])
        for series in prices["series"]:
            self.assertEqual(len(series["time"]), len(series["value"]))
            self.assertRegex(series["time"][0], r"^\d{4}-\d{2}-\d{2}$")
        # Prices come first; the analysis lives in closed accordions below.
        self.assertLess(
            html.index(b"data-price-explorer"), html.index(b"analysis-section")
        )
        sections = _sections(html)
        self.assertEqual(
            set(sections), {"returns", "expected", "volatility", "statistics"}
        )
        self.assertIn(b'data-plotly="chart-returns_box"', sections["returns"])
        self.assertIn("Volatilidad mensual actual".encode(), sections["volatility"])
        self.assertIn(b'data-plotly="chart-monthly_volatility"', sections["volatility"])
        self.assertIn("Retorno anualizado".encode(), sections["statistics"])
        # volatility_ticker is no longer a form field: the asset is picked
        # inside the histogram figure, which has one trace per asset.
        volatility = _json_script(html, "chart-monthly_volatility")
        self.assertIn("AAPL", [trace.get("name") for trace in volatility["data"]])
        self.assertNotIn(b'name="volatility_ticker"', html)
        self.assertNotRegex(html, rb'class="analysis-section"[^>]*\sopen')
        self.assertNotIn(b"data:image/png", html)
        self.assertIn(b"cdn.plot.ly/plotly-", html)

    @patch(f"{_SERVICES}.download_prices")
    def test_price_explorer_uses_prices_without_forward_fill(self, download) -> None:
        prices = make_prices(("AAPL", "BTC-USD"))
        prices.iloc[5, 0] = float("nan")
        download.return_value = prices
        response = self.post(
            "/monitoring",
            action="analyze",
            view="portfolio",
            portfolio="Portafolio actual",
            start_date="2024-01-01",
            tickers=["AAPL"],
            custom_tickers="BTC-USD",
        )
        payload = _json_script(response.data, "prices-monitoring")
        lengths = {s["ticker"]: len(s["value"]) for s in payload["series"]}
        self.assertEqual(lengths, {"AAPL": len(prices) - 1, "BTC-USD": len(prices)})

    @patch(f"{_SERVICES}.download_prices")
    def test_options_view_renders_period_volatility(self, download) -> None:
        download.return_value = make_prices(("UEC", "EQT"))
        response = self.post(
            "/monitoring",
            action="analyze",
            view="options",
            start_date="2024-01-01",
            period="Mensual",
            tickers=["UEC", "EQT"],
            custom_tickers="",
        )
        html = response.data
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<option value="Mensual" selected>', html)
        volatility = _sections(html)["volatility"]
        self.assertIn(b"VH mensual", volatility)
        self.assertIn(b'data-plotly="chart-period_volatility"', volatility)
        self.assertIn(b'data-plotly="chart-period_volatility_distribution"', volatility)
        self.assertIn(b'data-price-explorer="prices-monitoring"', html)
        self.assertNotIn(b"data:image/png", html)

    def test_get_renders_view_tabs_and_steps(self) -> None:
        for view, label in (
            ("portfolio", "Portafolio"),
            ("options", "Opciones financieras"),
            ("fundamental", "Análisis fundamental"),
        ):
            with self.subTest(view=view):
                html = self.client.get(f"/monitoring?view={view}").data.decode()
                self.assertIn('class="segmented"', html)
                tabs = html.split('class="segmented"', 1)[1].split("</nav>", 1)[0]
                current = re.findall(
                    r'<a href="[^"]*" aria-current="page">([^<]+)</a>', tabs
                )
                self.assertEqual(current, [label])
                self.assertIn('class="analysis-layout"', html)
                self.assertIn('class="param-panel"', html)
                self.assertIn('fieldset class="step"', html)
                self.assertIn("data-loading-title=", html)
                self.assertIn('class="empty-state"', html)
                self.assertNotIn("data:image/png", html)
        portfolio = self.client.get("/monitoring?view=portfolio").data.decode()
        self.assertIn('id="universes"', portfolio)
        self.assertIn("data-universe-select", portfolio)
        self.assertIn('data-min-selected="1"', portfolio)
        options = self.client.get("/monitoring?view=options").data.decode()
        self.assertEqual(len(_TICKER_CHECKBOX.findall(options)), 38)
        self.assertIn('data-date-presets="start_date"', options)

    def test_refresh_switches_portfolio_assets(self) -> None:
        response = self.post(
            "/monitoring",
            action="refresh",
            view="portfolio",
            universe="Portafolio imagen (23 activos)",
            tickers=["AAPL"],
        )
        html = response.data.decode()
        selector = html.split("data-asset-selector", 1)[1]
        self.assertEqual(len(_TICKER_CHECKBOX.findall(selector)), 23)
        self.assertNotIn('name="tickers" value="AAPL"', selector)
        self.assertEqual(
            len(re.findall(r'name="tickers" value="[^"]+" checked', selector)), 23
        )

    @patch(f"{_SERVICES}.download_prices")
    @patch(f"{_SERVICES}.fetch_fundamental_information")
    def test_fundamental_analysis_renders_company_information(
        self, fetch_info, download
    ) -> None:
        fetch_info.return_value = {
            "longName": "Apple Inc.",
            "currency": "USD",
            "currentPrice": 200.0,
            "previousClose": 190.0,
            "marketCap": 3_000_000_000_000,
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "country": "United States",
            "fiftyTwoWeekLow": 150.0,
            "fiftyTwoWeekHigh": 250.0,
            "revenueGrowth": 0.1,
        }
        download.return_value = make_prices(("AAPL",))
        response = self.post(
            "/monitoring",
            action="analyze",
            view="fundamental",
            portfolio="Portafolio actual",
            ticker="AAPL",
        )
        html = response.data
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Apple Inc.", html)
        self.assertIn(b'<span class="badge">Technology</span>', html)
        self.assertIn(b"USD 200.00", html)
        self.assertIn("▲ 5.26%".encode(), html)
        self.assertIn(b'class="kpi-grid"', html)
        self.assertIn("Capitalización bursátil".encode(), html)
        self.assertIn(b'data-price-explorer="prices-fundamental"', html)
        fetch_info.assert_called_once_with("AAPL")

    @patch(f"{_SERVICES}.download_prices", side_effect=ValueError("sin red"))
    @patch(f"{_SERVICES}.fetch_fundamental_information")
    def test_fundamental_analysis_without_prices_still_renders(
        self, fetch_info, _download
    ) -> None:
        fetch_info.return_value = {"longName": "Apple Inc.", "currentPrice": 200.0}
        response = self.post(
            "/monitoring", action="analyze", view="fundamental", ticker="AAPL"
        )
        self.assertIn(b"Apple Inc.", response.data)
        self.assertNotIn(b"data-price-explorer=", response.data)

    @patch(
        f"{_SERVICES}.fetch_fundamental_information",
        side_effect=YFRateLimitError(),
    )
    def test_fundamental_analysis_reports_yahoo_rate_limit(self, _fetch_info) -> None:
        response = self.post(
            "/monitoring",
            action="analyze",
            view="fundamental",
            portfolio="Portafolio actual",
            ticker="AAPL",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Yahoo Finance limitó temporalmente".encode(), response.data)


if __name__ == "__main__":
    unittest.main()
