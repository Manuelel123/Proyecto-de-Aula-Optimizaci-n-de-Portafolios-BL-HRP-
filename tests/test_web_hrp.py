"""HTTP tests for the HRP page."""

import json
import re
import unittest
from datetime import date
from unittest.mock import patch

from optimizacion_portafolios.models.hrp_backtest import clear_backtest_cache
from support import WebTestCase, make_prices


class HrpPageTests(WebTestCase):
    def test_invalid_custom_ticker_is_reported_without_market_request(self) -> None:
        response = self.post(
            "/hrp",
            action="optimize",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            start_date="2024-01-01",
            tickers=["<script>"],
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("no es válido".encode(), response.data)

    def test_invalid_custom_benchmark_is_reported_without_market_request(self) -> None:
        response = self.post(
            "/hrp",
            action="optimize",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            custom_benchmark="<script>",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT"],
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("no es válido".encode(), response.data)

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_hrp_optimization_renders_result_and_quantstats(self, download) -> None:
        def price_response(tickers, _start_date, _end_date):
            return make_prices(tickers)

        download.side_effect = price_response
        response = self.post(
            "/hrp",
            action="optimize",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            custom_benchmark="^GSPC",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT"],
            custom_tickers="",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Distribución del portafolio".encode(), response.data)
        self.assertIn("Análisis histórico".encode(), response.data)
        self.assertIn("Descargar informe QuantStats".encode(), response.data)
        self.assertEqual(download.call_count, 2)
        self.assertEqual(download.call_args_list[1].args[0], ("^GSPC",))

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_hrp_report_download_returns_html_attachment(self, download) -> None:
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        response = self.post(
            "/hrp",
            action="download_report",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT"],
            custom_tickers="",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        self.assertIn(
            "reporte_quantstats_hrp.html",
            response.headers["Content-Disposition"],
        )

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_hrp_applies_weight_limits_and_keeps_them_in_forms(self, download) -> None:
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        response = self.post(
            "/hrp",
            action="optimize",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT", "XLV"],
            custom_tickers="",
            min_weight="10",
            max_weight="50",
            min_AAPL="",
            max_AAPL="",
            min_MSFT="40",
            max_MSFT="",
            min_XLV="",
            max_XLV="20",
        )
        self.assertEqual(response.status_code, 200)
        html = response.data.decode()
        self.assertIn("Ratio de Sortino anualizado", html)
        self.assertIn("Ratio de Sortino (QuantStats", html)
        self.assertIn('name="min_weight" value="10"', html)
        self.assertIn('name="max_weight" value="50"', html)
        self.assertIn('name="min_MSFT" value="40"', html)  # hidden report field
        self.assertIn('name="max_XLV" value="20"', html)
        self.assertIn('id="min_MSFT" name="min_MSFT" type="number"', html)

        weights = self._hrp_weights(html)
        self.assertAlmostEqual(sum(weights.values()), 100.0, places=1)
        self.assertGreaterEqual(weights["MSFT"], 40.0 - 0.01)
        self.assertLessEqual(weights["XLV"], 20.0 + 0.01)
        for value in weights.values():
            self.assertTrue(10.0 - 0.01 <= value <= 50.0 + 0.01)

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_hrp_report_download_accepts_weight_limits(self, download) -> None:
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        response = self.post(
            "/hrp",
            action="download_report",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT"],
            custom_tickers="",
            min_weight="20",
            max_weight="80",
            max_AAPL="30",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_hrp_reports_infeasible_or_invalid_weight_limits(self, download) -> None:
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        cases = {
            "suma de los pesos mínimos": {"min_weight": "60"},
            "suma de los pesos máximos": {"max_weight": "40"},
            "no puede superar al máximo en: AAPL": {
                "min_AAPL": "30",
                "max_AAPL": "20",
            },
            "peso máximo debe estar entre 0 % y 100 %": {"max_weight": "150"},
            "peso mínimo de MSFT debe ser numérico": {"min_MSFT": "abc"},
        }
        for message, limits in cases.items():
            with self.subTest(message=message):
                response = self.post(
                    "/hrp",
                    action="optimize",
                    universe="Portafolio actual",
                    benchmark="S&P 500 (SPY)",
                    start_date="2024-01-01",
                    tickers=["AAPL", "MSFT"],
                    custom_tickers="",
                    **limits,
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(message.encode(), response.data)
                self.assertNotIn("Distribución del portafolio".encode(), response.data)

    def test_get_renders_form_components_and_empty_state(self) -> None:
        html = self.client.get("/hrp").data.decode()
        self.assertIn('id="universes"', html)
        self.assertIn("Portafolio actual", html)
        self.assertIn('data-loading-title="Optimizando portafolio HRP"', html)
        self.assertIn('data-ticker-rows="limit-row"', html)
        self.assertIn('<template id="limit-row">', html)
        self.assertIn(
            'id="min___TICKER__" name="min___TICKER__" type="number"', html
        )
        self.assertIn('name="action" value="refresh"', html)
        self.assertIn("data-feasibility", html)
        self.assertIn('class="empty-state"', html)
        self.assertNotIn("data-plotly=", html)

    def test_refresh_keeps_selection_inside_new_universe(self) -> None:
        response = self.post(
            "/hrp",
            action="refresh",
            universe="Criptomonedas y commodities",
            tickers=["AAPL", "BTC-USD"],
        )
        html = response.data.decode()
        self.assertEqual(response.status_code, 200)
        self.assertRegex(html, r'value="BTC-USD"\s+checked')
        self.assertIn('id="min_BTC-USD" name="min_BTC-USD" type="number"', html)
        self.assertNotIn('id="min_AAPL"', html)

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_result_uses_interactive_charts_and_closed_sections(self, download) -> None:
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        tickers = ["AAPL", "MSFT", "XLV"]
        response = self.post(
            "/hrp",
            action="optimize",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            start_date="2024-01-01",
            tickers=tickers,
            custom_tickers="",
        )
        html = response.data.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("data:image/png", html)
        self.assertIn('data-plotly="chart-allocation"', html)
        self.assertIn("data-price-explorer", html)
        self.assertIn('id="universes"', html)
        self.assertNotIn('class="empty-state"', html)

        payload = re.search(
            r'<script type="application/json" id="chart-allocation">(.*?)</script>',
            html,
            re.S,
        )
        self.assertIsNotNone(payload)
        figure = json.loads(payload.group(1))
        bars = [trace for trace in figure["data"] if trace.get("type") == "bar"]
        self.assertTrue(bars)
        self.assertEqual(len(bars[0]["y"]), len(tickers))

        # The allocation card is immediate; every other analysis is on demand.
        allocation_at = html.index('data-plotly="chart-allocation"')
        first_section = html.index('<details class="analysis-section"')
        self.assertLess(allocation_at, first_section)
        sections = re.findall(r'<details class="analysis-section"[^>]*>', html)
        self.assertGreaterEqual(len(sections), 6)
        self.assertTrue(all(" open" not in section for section in sections))
        for key in ("correlation", "dendrogram", "risk_contribution", "cumulative"):
            self.assertGreater(html.index(f'data-plotly="chart-{key}"'), first_section)
        self.assertGreater(html.index("data-price-explorer"), first_section)

        # Real catalog names instead of repeating the ticker; exact total.
        weights = self._hrp_weights(html)
        self.assertEqual(set(weights), set(tickers))
        self.assertIn("<td>AAPL</td><td>Apple</td>", html)
        self.assertIn("100.00%", html.split('id="allocation-table"', 1)[1])
        self.assertNotIn("Límite aplicado", html)

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_limits_column_appears_only_with_limits(self, download) -> None:
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        response = self.post(
            "/hrp",
            action="optimize",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT", "XLV"],
            custom_tickers="",
            max_weight="45",
        )
        html = response.data.decode()
        self.assertIn("Límite aplicado (mín–máx)", html)
        self.assertIn("0.00% – 45.00%", html)
        self.assertIn('<details class="advanced" open data-weight-limits>', html)

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_returns_matrix_sits_below_price_explorer(self, download) -> None:
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        response = self.post(
            "/hrp",
            action="optimize",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT", "XLV"],
            custom_tickers="",
        )
        html = response.data.decode()
        self.assertEqual(response.status_code, 200)
        prices = html.split('data-section="prices">', 1)[1].split("</details>", 1)[0]
        self.assertIn("Matriz de rendimientos históricos", prices)
        self.assertIn('data-linked-chart="chart-returns_matrix"', prices)
        self.assertIn('<script type="application/json" id="chart-returns_matrix">', prices)
        self.assertLess(
            prices.index("data-price-explorer"),
            prices.index('data-plotly="chart-returns_matrix"'),
        )
        self.assertEqual(html.count('id="chart-returns_matrix"'), 1)
        # The historical monthly heatmap is kept.
        self.assertIn('data-plotly="chart-monthly_heatmap"', html)

    @patch("optimizacion_portafolios.web.hrp.services.returns_matrix_chart")
    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_returns_matrix_series_order(self, download, matrix) -> None:
        from optimizacion_portafolios.web.common.charts import PORTFOLIO_SERIES
        from optimizacion_portafolios.web.hrp.services import run_hrp

        def price_response(tickers, _start, _end):
            prices = make_prices(tickers)
            if "BTC-USD" in prices:
                # Missing quotes stay missing: no ffill across assets.
                prices.iloc[10:15, list(prices.columns).index("BTC-USD")] = float("nan")
            return prices

        download.side_effect = price_response
        matrix.return_value = None
        result = run_hrp(
            ["AAPL", "BTC-USD", "XLV"],
            "SPY",
            date(2024, 1, 1),
            labels={"AAPL": "Apple (AAPL)"},
        )
        series, names = matrix.call_args.args
        allocation = [row["ticker"] for row in result["allocation_rows"]]
        self.assertEqual(list(series), [PORTFOLIO_SERIES, "SPY", *allocation])
        self.assertEqual(names[PORTFOLIO_SERIES], "Portafolio HRP")
        self.assertEqual(names["AAPL"], "Apple (AAPL)")
        self.assertEqual(names["SPY"], "S&P 500 (SPY)")
        self.assertEqual(len(series["BTC-USD"]), len(series["AAPL"]) - 5)
        self.assertNotIn("returns_matrix", result["charts"])

        # A benchmark that is also an asset is not repeated.
        run_hrp(["AAPL", "BTC-USD", "XLV"], "AAPL", date(2024, 1, 1))
        series, _ = matrix.call_args.args
        self.assertEqual(list(series)[0], PORTFOLIO_SERIES)
        self.assertEqual(sorted(list(series)[1:]), ["AAPL", "BTC-USD", "XLV"])

    def _post_backtest(self, **extra):
        data = dict(
            action="optimize",
            universe="Portafolio actual",
            benchmark="S&P 500 (SPY)",
            start_date="2024-01-01",
            tickers=["AAPL", "MSFT", "XLV"],
            custom_tickers="",
            backtest="1",
            bt_lookback="60",
        )
        data.update(extra)
        return self.post("/hrp", **data)

    def test_backtest_toggle_is_checked_by_default(self) -> None:
        html = self.client.get("/hrp").data.decode()
        self.assertIn('name="backtest" value="1" data-backtest-toggle checked', html)
        self.assertIn('id="bt_lookback" name="bt_lookback"', html)
        self.assertIn("Backtest walk-forward (vectorbt)", html)

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_backtest_renders_strategies_metrics_and_rebalance_dates(self, download) -> None:
        clear_backtest_cache()
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        response = self._post_backtest(bt_drift="3", bt_fees="0.2", bt_vol_threshold="15")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode()
        self.assertIn("Backtest de rebalanceo · vectorbt", html)
        self.assertIn('id="chart-backtest_equity"', html)
        self.assertIn('id="chart-backtest_drawdown"', html)
        self.assertIn("Fechas de rebalanceo", html)
        for label in ("Buy &amp; hold", "Mensual", "Trimestral", "Semestral", "Anual",
                      "Desviación ±3 pp", "Volatilidad (15 %)"):
            self.assertIn(label, html)
        for column in ("CAGR", "Ratio de Sortino", "Máxima caída", "Rebalanceos", "Costos totales"):
            self.assertIn(column, html)
        self.assertIn('id="bt_drift" name="bt_drift" type="number" value="3"', html)
        # One download of the assets with the longer history for the estimation
        # window (reused by the optimization), then the benchmark.
        self.assertEqual(download.call_count, 2)
        history_call = download.call_args_list[0].args
        self.assertEqual(history_call[0], ("AAPL", "MSFT", "XLV"))
        self.assertLess(history_call[1], date(2024, 1, 1))
        self.assertEqual(download.call_args_list[1].args[0], ("SPY",))

        # Same inputs: the backtest comes from the cache.
        again = self._post_backtest(bt_drift="3", bt_fees="0.2", bt_vol_threshold="15")
        self.assertIn("reutilizado de la caché", again.data.decode())

    @patch("optimizacion_portafolios.web.hrp.services.run_backtest_section")
    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_backtest_is_skipped_when_disabled(self, download, section) -> None:
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        response = self._post_backtest(backtest="")
        self.assertEqual(response.status_code, 200)
        section.assert_not_called()
        self.assertNotIn("Backtest de rebalanceo · vectorbt", response.data.decode())
        self.assertIn('name="backtest" value="1" data-backtest-toggle>', response.data.decode())

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_backtest_errors_do_not_hide_the_hrp_result(self, download) -> None:
        download.side_effect = lambda tickers, _start, _end: make_prices(tickers)
        response = self._post_backtest(bt_lookback="1000")
        html = response.data.decode()
        self.assertIn("Distribución del portafolio", html)
        self.assertIn("No se pudo calcular el backtest", html)
        self.assertIn("No hay datos suficientes", html)

    @patch("optimizacion_portafolios.web.hrp.services.download_prices")
    def test_invalid_backtest_parameters_are_reported(self, download) -> None:
        for field, value, message in (
            ("bt_lookback", "abc", "La ventana de estimación debe ser un número entero"),
            ("bt_lookback", "10", "La ventana de estimación debe estar entre 20 y 1260"),
            ("bt_fees", "9", "La comisión debe estar entre 0 % y 5 %"),
            ("bt_vol_threshold", "0", "El umbral de volatilidad debe estar entre 1 % y 200 %"),
        ):
            with self.subTest(field=field, value=value):
                response = self._post_backtest(**{field: value})
                self.assertIn(message, response.data.decode())
        download.assert_not_called()

    @staticmethod
    def _hrp_weights(html: str) -> dict[str, float]:
        table = html.split('id="allocation-table"', 1)[1].split("</table>", 1)[0]
        body = table.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        rows = re.findall(
            r"<tr>\s*<td>([^<]+)</td>\s*<td>[^<]+</td>\s*<td>([\d.,]+)\s*%</td>",
            body,
        )
        return {ticker: float(value.replace(",", ".")) for ticker, value in rows}


if __name__ == "__main__":
    unittest.main()
