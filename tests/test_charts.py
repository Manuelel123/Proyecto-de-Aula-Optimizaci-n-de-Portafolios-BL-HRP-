"""Plotly figure payloads (web.common.charts) and price explorer payload."""

import json
import re
import unittest

import numpy as np
import pandas as pd
from skfolio.cluster import HierarchicalClustering
from skfolio.distance import PearsonDistance

from optimizacion_portafolios.analytics.volatility import calculate_monthly_volatility
from optimizacion_portafolios.web.common import chart_theme, charts
from optimizacion_portafolios.web.common.price_series import price_series_payload
from support import make_prices

TICKERS = ("AAPL", "MSFT", "NVDA", "XOM", "GLD")


def load(payload) -> dict:
    return json.loads(str(payload))


class PayloadAssertions(unittest.TestCase):
    def assertSafePayload(self, payload) -> dict:
        self.assertIsNotNone(payload)
        text = str(payload)
        self.assertNotIn("<", text)
        self.assertNotIn(">", text)
        self.assertNotIn("NaN", text)
        self.assertNotIn("Infinity", text)
        figure = load(payload)
        layout = figure["layout"]
        self.assertEqual(layout["separators"], ".,")
        self.assertFalse(layout.get("title", {}).get("text"))
        # Own light template, never the default "plotly" one; transparent backgrounds.
        template_layout = layout["template"]["layout"]
        self.assertEqual(template_layout["paper_bgcolor"], "rgba(0,0,0,0)")
        self.assertEqual(template_layout["plot_bgcolor"], "rgba(0,0,0,0)")
        self.assertEqual(template_layout["colorway"], list(chart_theme.SERIES))
        self.assertNotIn("data", layout["template"])
        return figure


class AllocationChartTests(PayloadAssertions):
    def setUp(self) -> None:
        self.weights = pd.Series({"MSFT": 0.2, "AAPL": 0.5, "GLD": 0.3})

    def test_sorted_horizontal_bars_with_one_label_per_asset(self) -> None:
        figure = self.assertSafePayload(charts.allocation_chart(self.weights, {"AAPL": "Apple"}))
        bars = [trace for trace in figure["data"] if trace["type"] == "bar"]
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["orientation"], "h")
        self.assertEqual(bars[0]["y"], ["AAPL", "GLD", "MSFT"])
        self.assertEqual(figure["layout"]["yaxis"]["autorange"], "reversed")
        self.assertEqual(figure["layout"]["xaxis"]["tickformat"], ".0%")
        self.assertIn("Apple", bars[0]["customdata"])
        self.assertEqual(figure["layout"]["height"], 3 * charts.BAR_ROW_HEIGHT + 80)

    def test_reference_market_weights_and_bounds(self) -> None:
        figure = self.assertSafePayload(
            charts.allocation_chart(
                self.weights,
                reference=1 / 3,
                market_weights=pd.Series({"AAPL": 0.4, "MSFT": 0.4, "GLD": 0.2}),
                bounds={"AAPL": (0.1, 0.6), "MSFT": (0.0, 0.4), "GLD": (0.05, 0.5)},
            )
        )
        first_bar = next(trace for trace in figure["data"] if trace["type"] == "bar")
        self.assertEqual(len(first_bar["y"]), 3)
        market = [trace for trace in figure["data"] if trace.get("name") == "Peso de mercado"]
        self.assertEqual(market[0]["marker"]["symbol"], "line-ns-open")
        shapes = figure["layout"]["shapes"]
        self.assertTrue(any(shape["type"] == "line" and shape["line"]["dash"] == "dot" for shape in shapes))
        self.assertEqual(sum(shape["type"] == "rect" for shape in shapes), 3)
        self.assertTrue(figure["layout"]["showlegend"])

    def test_negative_weights_use_negative_color_and_include_zero(self) -> None:
        weights = pd.Series({"AAPL": 0.8, "MSFT": 0.5, "GLD": -0.3})
        figure = self.assertSafePayload(charts.allocation_chart(weights))
        colors = figure["data"][0]["marker"]["color"]
        self.assertEqual(colors[-1], chart_theme.NEGATIVE)
        self.assertEqual(colors[0], chart_theme.PRIMARY)
        low, high = figure["layout"]["xaxis"]["range"]
        self.assertLess(low, -0.3)
        self.assertGreater(high, 0.8)

    def test_empty_weights_return_none(self) -> None:
        self.assertIsNone(charts.allocation_chart(pd.Series(dtype=float)))
        self.assertIsNone(charts.allocation_chart(pd.Series({"AAPL": np.nan})))


class HistoricalChartsTests(PayloadAssertions):
    def setUp(self) -> None:
        returns = make_prices(TICKERS, rows=320).pct_change().dropna()
        self.portfolio = returns.mean(axis=1).rename("Portafolio HRP")
        self.benchmark = returns["XOM"].rename("SPY")

    def test_all_keys_with_enough_history(self) -> None:
        result, has_rolling = charts.historical_charts(self.portfolio, self.benchmark)
        self.assertTrue(has_rolling)
        self.assertEqual(
            set(result),
            {"cumulative", "drawdown", "monthly_heatmap", "rolling_sharpe", "returns_distribution", "yearly_returns"},
        )
        for payload in result.values():
            self.assertSafePayload(payload)

    def test_cumulative_benchmark_is_gray_and_dotted(self) -> None:
        figure = self.assertSafePayload(charts.cumulative_returns_chart(self.portfolio, self.benchmark))
        self.assertEqual([trace["name"] for trace in figure["data"]], ["Portafolio HRP", "SPY"])
        self.assertEqual(figure["data"][0]["line"]["color"], chart_theme.PRIMARY)
        self.assertEqual(figure["data"][1]["line"]["dash"], "dot")
        self.assertEqual(figure["data"][1]["line"]["color"], chart_theme.TEXT_3)
        self.assertEqual(figure["layout"]["hovermode"], "x unified")
        self.assertRegex(figure["data"][0]["x"][0], r"^\d{4}-\d{2}-\d{2}$")

    def test_drawdown_marks_worst_drop(self) -> None:
        figure = self.assertSafePayload(charts.drawdown_chart(self.portfolio))
        self.assertEqual(figure["data"][0]["fill"], "tozeroy")
        self.assertEqual(figure["data"][1]["name"], "Peor caída")
        self.assertIn("Peor caída", figure["layout"]["annotations"][0]["text"])

    def test_monthly_heatmap_has_year_column_and_zero_midpoint(self) -> None:
        figure = self.assertSafePayload(charts.monthly_returns_heatmap(self.portfolio))
        self.assertEqual([trace["type"] for trace in figure["data"]], ["heatmap", "heatmap"])
        self.assertEqual(figure["data"][0]["zmid"], 0)
        self.assertEqual(len(figure["data"][0]["x"]), 12)
        self.assertEqual(figure["data"][1]["x"], ["Año"])
        self.assertEqual(figure["data"][0]["texttemplate"], "%{z:.1%}")

    def test_rolling_sharpe_needs_enough_history(self) -> None:
        short = self.portfolio.iloc[:100]
        self.assertIsNone(charts.rolling_sharpe_chart(short, self.benchmark.iloc[:100]))
        result, has_rolling = charts.historical_charts(short, self.benchmark.iloc[:100])
        self.assertFalse(has_rolling)
        self.assertNotIn("rolling_sharpe", result)
        figure = self.assertSafePayload(charts.rolling_sharpe_chart(self.portfolio, self.benchmark))
        self.assertEqual(len(figure["data"]), 3)
        self.assertTrue(figure["data"][2]["name"].startswith("Media"))

    def test_distribution_and_yearly(self) -> None:
        distribution = self.assertSafePayload(
            charts.returns_distribution_chart(self.portfolio, self.benchmark)
        )
        self.assertEqual([trace["type"] for trace in distribution["data"]], ["histogram", "histogram"])
        self.assertEqual(distribution["layout"]["barmode"], "overlay")
        self.assertEqual(distribution["data"][0]["xbins"], distribution["data"][1]["xbins"])
        yearly = self.assertSafePayload(charts.yearly_returns_chart(self.portfolio, self.benchmark))
        self.assertEqual(yearly["layout"]["barmode"], "group")
        self.assertEqual(yearly["data"][0]["x"], ["2024", "2025"])

    def test_empty_returns_give_no_charts(self) -> None:
        empty = pd.Series(dtype=float)
        result, has_rolling = charts.historical_charts(empty, empty)
        self.assertEqual(result, {})
        self.assertFalse(has_rolling)


class StructureChartsTests(PayloadAssertions):
    def setUp(self) -> None:
        self.returns = make_prices(TICKERS, rows=260).pct_change().dropna()

    def test_correlation_heatmap(self) -> None:
        figure = self.assertSafePayload(
            charts.correlation_heatmap(self.returns.corr(), "Correlación cuasi-diagonal")
        )
        heatmap = figure["data"][0]
        self.assertEqual(heatmap["type"], "heatmap")
        self.assertEqual((heatmap["zmin"], heatmap["zmid"], heatmap["zmax"]), (-1, 0, 1))
        self.assertEqual(heatmap["texttemplate"], "%{z:.2f}")
        self.assertEqual(heatmap["x"], list(TICKERS))
        self.assertIn("Correlación cuasi-diagonal", heatmap["hovertemplate"])
        self.assertIsNone(charts.correlation_heatmap(pd.DataFrame()))

    def test_large_correlation_hides_annotations(self) -> None:
        tickers = tuple(f"T{index:02d}" for index in range(16))
        returns = make_prices(tickers, rows=120).pct_change().dropna()
        figure = self.assertSafePayload(charts.correlation_heatmap(returns.corr()))
        self.assertNotIn("texttemplate", figure["data"][0])

    def test_dendrogram_is_rethemed_and_compact(self) -> None:
        distance = PearsonDistance().fit(self.returns).distance_
        clustering = HierarchicalClustering().fit(
            pd.DataFrame(distance, index=self.returns.columns, columns=self.returns.columns)
        )
        figure = self.assertSafePayload(charts.dendrogram_chart(clustering))
        self.assertTrue(all(trace["type"] == "scatter" for trace in figure["data"]))
        self.assertLessEqual(len(figure["data"]), chart_theme.MAX_CATEGORICAL + 1)
        self.assertEqual(sorted(figure["layout"]["xaxis"]["ticktext"]), sorted(TICKERS))
        self.assertNotIn("width", figure["layout"])
        self.assertIsNone(charts.dendrogram_chart(None))
        self.assertIsNone(charts.dendrogram_chart(HierarchicalClustering()))

    def test_contribution_chart(self) -> None:
        frame = pd.DataFrame(
            {"Participación del retorno": [0.6, -0.1, 0.5], "Peso HRP": [0.4, 0.3, 0.3]},
            index=["AAPL", "MSFT", "GLD"],
        )
        figure = self.assertSafePayload(charts.contribution_chart(frame, "Participación del retorno"))
        self.assertEqual(figure["data"][0]["orientation"], "h")
        self.assertEqual(figure["data"][0]["y"], ["AAPL", "GLD", "MSFT"])
        compared = self.assertSafePayload(
            charts.contribution_chart(frame, "Participación del retorno", compare="Peso HRP")
        )
        self.assertEqual(len(compared["data"]), 2)
        self.assertIsNone(charts.contribution_chart(frame, "No existe"))


class VolatilityChartsTests(PayloadAssertions):
    def setUp(self) -> None:
        self.returns = make_prices(TICKERS, rows=300).pct_change().dropna()
        self.monthly = calculate_monthly_volatility(self.returns)

    def test_histogram_selector_shows_one_trace(self) -> None:
        current = self.returns.tail(21).std() * np.sqrt(21)
        figure = self.assertSafePayload(
            charts.volatility_histogram_chart(self.monthly, current, selected="NVDA")
        )
        self.assertEqual(len(figure["data"]), len(TICKERS))
        visible = [trace["name"] for trace in figure["data"] if trace.get("visible", True) is True]
        self.assertEqual(visible, ["NVDA"])
        menu = figure["layout"]["updatemenus"][0]
        self.assertEqual([button["label"] for button in menu["buttons"]], list(TICKERS))
        self.assertEqual(menu["active"], TICKERS.index("NVDA"))
        self.assertIn("Actual", figure["layout"]["annotations"][0]["text"])
        self.assertEqual(figure["layout"]["shapes"][0]["line"]["dash"], "dash")

    def test_histogram_defaults_to_last_value(self) -> None:
        figure = self.assertSafePayload(
            charts.volatility_histogram_chart(self.monthly, self.monthly.iloc[-1], axis_title="Volatilidad anualizada")
        )
        self.assertEqual(figure["layout"]["shapes"][0]["x0"], float(self.monthly["AAPL"].iloc[-1]))
        self.assertEqual(figure["layout"]["xaxis"]["title"]["text"], "Volatilidad anualizada")
        self.assertIsNone(charts.volatility_histogram_chart(pd.DataFrame({"AAPL": [np.nan]})))

    def test_returns_box_colors_follow_ticker_order(self) -> None:
        figure = self.assertSafePayload(charts.returns_box_chart(self.returns))
        self.assertEqual([trace["type"] for trace in figure["data"]], ["box"] * len(TICKERS))
        self.assertEqual(
            [trace["line"]["color"] for trace in figure["data"]], list(chart_theme.SERIES[: len(TICKERS)])
        )
        self.assertIsNone(charts.returns_box_chart(pd.DataFrame()))

    def test_period_volatility_bars_and_histograms(self) -> None:
        annualized = self.monthly * np.sqrt(12)
        figure = self.assertSafePayload(charts.period_volatility_chart(annualized, self.monthly, "Mensual"))
        bars = figure["data"][0]
        self.assertEqual(bars["type"], "bar")
        self.assertEqual(bars["orientation"], "h")
        self.assertEqual(len(bars["y"]), len(TICKERS))
        self.assertEqual(len(figure["data"]), 1 + len(TICKERS))
        labels = [button["label"] for button in figure["layout"]["updatemenus"][0]["buttons"]]
        self.assertEqual(labels, ["Todos los activos", *TICKERS])
        selected = self.assertSafePayload(
            charts.period_volatility_chart(annualized, self.monthly, "Mensual", selected="GLD")
        )
        self.assertTrue(selected["layout"]["xaxis2"]["visible"])
        self.assertIsNone(charts.period_volatility_chart(pd.DataFrame(), pd.DataFrame(), "Mensual"))


class PriorPosteriorChartTests(PayloadAssertions):
    def test_dumbbell(self) -> None:
        prior = pd.Series({"AAPL": 0.07, "MSFT": 0.06, "GLD": 0.03})
        views = pd.Series({"AAPL": 0.12, "MSFT": 0.04, "GLD": 0.05})
        posterior = pd.Series({"AAPL": 0.10, "MSFT": 0.05, "GLD": 0.04})
        figure = self.assertSafePayload(charts.prior_posterior_chart(prior, views, posterior))
        names = [trace["name"] for trace in figure["data"]]
        self.assertEqual(names, ["Desplazamiento", "Prior (equilibrio)", "View", "Posterior"])
        self.assertEqual(figure["data"][-1]["y"], ["AAPL", "MSFT", "GLD"])
        self.assertEqual(figure["layout"]["hovermode"], "y unified")
        self.assertIsNone(charts.prior_posterior_chart(prior, views, pd.Series(dtype=float)))


class PriceSeriesPayloadTests(unittest.TestCase):
    def test_columnar_payload_without_nan(self) -> None:
        prices = make_prices(("AAPL", "BTC-USD"), rows=30)
        prices.iloc[3:6, 0] = np.nan
        benchmark = make_prices(("SPY",), rows=30)["SPY"]
        payload = price_series_payload(prices, {"AAPL": "Apple"}, benchmark)
        text = str(payload)
        self.assertNotIn("NaN", text)
        self.assertNotIn("<", text)
        data = json.loads(text)
        aapl, btc = data["series"]
        self.assertEqual(aapl["ticker"], "AAPL")
        self.assertEqual(aapl["name"], "Apple")
        self.assertEqual(len(aapl["time"]), len(aapl["value"]))
        self.assertEqual(len(aapl["time"]), 27)
        self.assertEqual(len(btc["time"]), 30)
        self.assertTrue(all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) for day in aapl["time"]))
        self.assertEqual(aapl["time"], sorted(set(aapl["time"])))
        self.assertEqual(data["benchmark"]["ticker"], "SPY")
        self.assertEqual(len(data["benchmark"]["time"]), len(data["benchmark"]["value"]))

    def test_unsorted_duplicates_are_normalized(self) -> None:
        index = pd.to_datetime(["2024-01-03", "2024-01-02", "2024-01-03 16:00"], format="mixed")
        prices = pd.DataFrame({"AAPL": [10.0, 9.0, 11.0]}, index=index)
        data = json.loads(str(price_series_payload(prices)))
        self.assertEqual(data["series"][0]["time"], ["2024-01-02", "2024-01-03"])
        self.assertEqual(data["series"][0]["value"], [9.0, 11.0])
        self.assertIsNone(data["benchmark"])

    def test_empty_prices_return_none(self) -> None:
        self.assertIsNone(price_series_payload(pd.DataFrame({"AAPL": [np.nan, np.nan]})))


if __name__ == "__main__":
    unittest.main()
