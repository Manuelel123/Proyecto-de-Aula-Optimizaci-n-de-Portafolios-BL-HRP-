"""HTTP tests for the Black-Litterman page."""

import json
import re
import unittest
from datetime import date
from unittest.mock import patch

from optimizacion_portafolios.analytics.statistics import date_years_ago

from yfinance.exceptions import YFRateLimitError

from support import WebTestCase, make_prices


class BlackLittermanPageTests(WebTestCase):
    @staticmethod
    def _mock_market_data(download, market_cap) -> None:
        download.side_effect = lambda tickers, _start_date, _end_date: make_prices(
            tickers
        )
        market_cap.side_effect = {
            "AAPL": 3_000_000_000_000,
            "MSFT": 3_500_000_000_000,
            "GOOGL": 2_000_000_000_000,
            "NVDA": 4_000_000_000_000,
        }.__getitem__

    @patch(
        "optimizacion_portafolios.web.black_litterman.routes.run_black_litterman",
        side_effect=YFRateLimitError(),
    )
    def test_black_litterman_reports_yahoo_rate_limit(self, _run_model) -> None:
        response = self.post(
            "/black-litterman",
            action="optimize",
            universe="Activos principales",
            benchmark="S&P 500 (SPY)",
            objective="Máximo ratio de Sharpe",
            risk_free_rate="2.0",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT"],
            custom_tickers="",
            view_AAPL="8.0",
            view_MSFT="8.0",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Yahoo Finance limitó temporalmente".encode(), response.data)

    def _black_litterman_form(self, **overrides) -> dict:
        form = {
            "action": "optimize",
            "universe": "Activos principales",
            "benchmark": "S&P 500 (SPY)",
            "objective": "Máximo ratio de Sharpe",
            "risk_free_rate": "2.0",
            "start_date": "2024-01-01",
            "tickers": ["AAPL", "MSFT"],
            "custom_tickers": "nvda",
            "view_AAPL": "8.0",
            "view_MSFT": "9.0",
            "view_NVDA": "25.0",
        }
        form.update(overrides)
        return form

    @patch("optimizacion_portafolios.web.black_litterman.services.fetch_market_cap_usd")
    @patch("optimizacion_portafolios.web.black_litterman.services.download_prices")
    def test_black_litterman_runs_and_renders_posterior(
        self, download, market_cap
    ) -> None:
        self._mock_market_data(download, market_cap)
        response = self.post(
            "/black-litterman",
            **self._black_litterman_form(
                tickers=["AAPL", "MSFT", "GOOGL"],
                custom_tickers="",
                view_GOOGL="8.0",
            ),
        )
        self.assertEqual(response.status_code, 200)
        page = response.data.decode()
        self.assertIn("Distribución del portafolio", page)
        self.assertIn("Covarianza posterior", page)
        self.assertIn("Retorno prior (equilibrio)", page)
        self.assertIn("Ratio de Sortino esperado (modelo)", page)
        self.assertIn("Ratio de Sortino histórico", page)
        self.assertIn("Análisis histórico", page)
        self.assertIn("Descargar informe QuantStats", page)
        self.assertEqual(market_cap.call_count, 3)

        # Allocation chart: one horizontal bar per asset, rendered client-side.
        self.assertIn('data-plotly="chart-allocation"', page)
        match = re.search(
            r'<script type="application/json" id="chart-allocation">(.*?)</script>',
            page,
            re.S,
        )
        self.assertIsNotNone(match)
        figure = json.loads(match.group(1))
        bars = [trace for trace in figure["data"] if trace.get("type") == "bar"]
        self.assertTrue(bars)
        self.assertEqual(len(bars[0]["y"]), 3)
        self.assertIn("data-price-explorer", page)
        self.assertIn("cdn.plot.ly/plotly-", page)
        self.assertNotIn("data:image/png", page)

        # Immediate allocation card, every other analysis in a closed accordion.
        results = page[page.index('class="results"'):]
        card_position = results.index("allocation-card")
        first_section = results.index('<details class="analysis-section"')
        self.assertLess(card_position, first_section)
        sections = re.findall(r'<details class="analysis-section"([^>]*)>', page)
        self.assertGreaterEqual(len(sections), 5)
        self.assertTrue(all(" open" not in attributes for attributes in sections))
        for title in (
            "Covarianza posterior",
            "Retorno prior (equilibrio)",
            "Ratio de Sortino histórico",
            "data-price-explorer",
            "Supuestos del modelo",
        ):
            self.assertGreater(results.index(title), first_section, title)
        # Equilibrium column of the views form is filled from the last prior.
        self.assertRegex(page, r'data-equilibrium="-?\d+\.\d{2}"')

    @patch("optimizacion_portafolios.web.black_litterman.services.fetch_market_cap_usd")
    @patch("optimizacion_portafolios.web.black_litterman.services.download_prices")
    def test_black_litterman_uses_custom_ticker_view(
        self, download, market_cap
    ) -> None:
        self._mock_market_data(download, market_cap)
        for objective in ("Máximo ratio de Sortino", "Mínimo CVaR (95%)"):
            with self.subTest(objective=objective):
                response = self.post(
                    "/black-litterman",
                    **self._black_litterman_form(objective=objective),
                )
                self.assertEqual(response.status_code, 200)
                page = response.data.decode()
                self.assertIn("Distribución del portafolio", page)
                # NVDA's allocation row shows its 25% annual view.
                self.assertRegex(
                    page, r'<tr>\s*<td>NVDA</td>(?:(?!</tr>).)*>25\.00%</td>'
                )
                self.assertRegex(
                    page, r'id="view_NVDA" name="view_NVDA" type="number" value="25\.0"'
                )
        self.assertIn("NVDA", {call.args[0] for call in market_cap.call_args_list})

    def test_black_litterman_refresh_shows_custom_ticker_views(self) -> None:
        response = self.post(
            "/black-litterman",
            **self._black_litterman_form(
                action="refresh", custom_tickers="nvda, bad ticker"
            ),
        )
        self.assertEqual(response.status_code, 200)
        page = response.data.decode()
        self.assertIn('id="view_NVDA"', page)
        self.assertIn('id="view_AAPL"', page)
        self.assertIn('value="95%"', page)
        self.assertIn("Símbolos no válidos ignorados", page)
        self.assertIn('<template id="view-row">', page)
        self.assertIn('id="view___TICKER__"', page)

    def test_black_litterman_get_renders_form_and_empty_state(self) -> None:
        response = self.client.get("/black-litterman")
        self.assertEqual(response.status_code, 200)
        page = response.data.decode()
        self.assertIn('id="universes"', page)
        match = re.search(
            r'<script type="application/json" id="universes">(.*?)</script>', page, re.S
        )
        universes = json.loads(match.group(1))
        current = universes["Portafolio actual"]
        self.assertEqual(
            current["default"], [asset["ticker"] for asset in current["assets"]]
        )
        self.assertEqual(len(universes["Activos principales"]["default"]), 4)
        self.assertEqual(len(universes["Portafolio Colombia"]["default"]), 4)
        self.assertIn('data-ticker-rows="view-row"', page)
        self.assertIn('<optgroup label="Minimizar riesgo">', page)
        self.assertIn("data-help=", page)
        self.assertIn("Ventana fija: 2 años", page)
        self.assertIn("empty-state", page)
        self.assertNotIn('name="start_date"', page)
        self.assertIn('step="any"', page)

    @patch("optimizacion_portafolios.web.black_litterman.services.fetch_market_cap_usd")
    @patch("optimizacion_portafolios.web.black_litterman.services.download_prices")
    def test_black_litterman_ignores_submitted_start_date(
        self, download, market_cap
    ) -> None:
        self._mock_market_data(download, market_cap)
        response = self.post(
            "/black-litterman",
            **self._black_litterman_form(start_date="2010-01-01"),
        )
        self.assertEqual(response.status_code, 200)
        expected_start = date_years_ago(date.today(), 2)
        self.assertTrue(download.call_args_list)
        for call in download.call_args_list:
            self.assertEqual(call.args[1], expected_start)
            self.assertEqual(call.args[2], date.today())

    @patch("optimizacion_portafolios.web.black_litterman.services.download_prices")
    def test_black_litterman_requires_visible_view_for_custom_ticker(
        self, download
    ) -> None:
        form = self._black_litterman_form()
        del form["view_NVDA"]
        response = self.post("/black-litterman", **form)
        self.assertEqual(response.status_code, 200)
        page = response.data.decode()
        self.assertIn("Define la view anual de: NVDA", page)
        self.assertIn('id="view_NVDA"', page)
        download.assert_not_called()

    @patch("optimizacion_portafolios.web.black_litterman.services.fetch_market_cap_usd")
    @patch("optimizacion_portafolios.web.black_litterman.services.download_prices")
    def test_black_litterman_report_download_keeps_custom_views(
        self, download, market_cap
    ) -> None:
        self._mock_market_data(download, market_cap)
        response = self.post(
            "/black-litterman",
            **self._black_litterman_form(action="download_report"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "reporte_quantstats_black_litterman.html",
            response.headers["Content-Disposition"],
        )


if __name__ == "__main__":
    unittest.main()
