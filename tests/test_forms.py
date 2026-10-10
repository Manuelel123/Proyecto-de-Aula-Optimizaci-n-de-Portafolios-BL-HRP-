"""Form components: Jinja macros (macros/forms.html) and universes_payload."""

import json
import re
import unittest

from flask import render_template_string

from optimizacion_portafolios.data.catalogs import (
    ACTIVOS_HRP,
    GRUPOS_ACTIVOS,
    UNIVERSOS_BLACK_LITTERMAN,
    UNIVERSOS_HRP,
)
from optimizacion_portafolios.web import create_app
from optimizacion_portafolios.web.common.forms import (
    asset_groups,
    asset_labels,
    universes_payload,
)

_TICKER_PATTERN = re.compile(r"^[A-Z0-9^=.\-]{1,24}$")


class MacroTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})

    def render(self, body: str, **context) -> str:
        source = '{% import "macros/forms.html" as forms %}' + body
        with self.app.test_request_context():
            return render_template_string(source, **context)


class UniverseSelectTests(MacroTestCase):
    def test_renders_select_summary_and_json_catalog(self) -> None:
        payload = universes_payload(UNIVERSOS_HRP)
        html = self.render(
            '{{ forms.universe_select("universe", names, "Portafolio actual", payload) }}',
            names=list(UNIVERSOS_HRP),
            payload=payload,
        )
        self.assertIn('<select id="universe" name="universe" data-universe-select', html)
        self.assertIn('<option value="Portafolio actual" selected>', html)
        self.assertIn('id="universe-summary"', html)
        self.assertIn("19 activos", html)
        match = re.search(
            r'<script type="application/json" id="universes">(.*?)</script>', html, re.S
        )
        self.assertIsNotNone(match)
        self.assertEqual(json.loads(match.group(1)), payload)

    def test_summary_lists_groups_of_mixed_universes(self) -> None:
        payload = universes_payload(UNIVERSOS_HRP)
        html = self.render(
            '{{ forms.universe_select("u", names, "Criptomonedas y commodities", payload) }}',
            names=list(UNIVERSOS_HRP),
            payload=payload,
        )
        self.assertIn("9 activos · Criptomonedas, Commodities", html)

    def test_missing_payload_still_renders(self) -> None:
        html = self.render(
            '{{ forms.universe_select("u", ["A"], "A", none) }}'
        )
        self.assertIn('<option value="A" selected>A</option>', html)
        self.assertIn('id="universes">{}</script>', html)


class AssetSelectorTests(MacroTestCase):
    def test_chips_keep_submitted_field_and_checked_state(self) -> None:
        html = self.render(
            "{{ forms.asset_selector(available, selected) }}",
            available=["AAPL", "MSFT", "AAPL", "GC=F"],
            selected=["MSFT", "GC=F"],
        )
        self.assertIn('data-asset-selector data-name="tickers"', html)
        self.assertEqual(len(re.findall(r'\sname="tickers"', html)), 3)  # duplicates removed
        self.assertRegex(html, r'name="tickers" value="MSFT"\s+checked>')
        self.assertRegex(html, r'name="tickers" value="AAPL"\s+>')
        self.assertIn('class="chip__input visually-hidden"', html)
        self.assertIn('<span class="chip__check" aria-hidden="true">✓</span>', html)
        self.assertIn("<strong data-count-selected>2</strong> de <span data-count-total>3</span>", html)
        self.assertIn('placeholder="Buscar ticker…"', html)
        for action in ("all", "none", "invert"):
            self.assertIn(f'data-select="{action}"', html)
        self.assertNotIn("data-min-selected", html)
        self.assertNotIn("asset-group", html)

    def test_custom_name_labels_and_min_selected(self) -> None:
        html = self.render(
            '{{ forms.asset_selector(["AAPL", "XLV"], ["AAPL"], labels=labels, '
            'name="portfolio_tickers", field_id="sel", min_selected=2) }}',
            labels={"AAPL": "Apple (AAPL)", "XLV": "XLV"},
        )
        self.assertIn('id="sel" data-asset-selector data-name="portfolio_tickers"', html)
        self.assertIn('data-min-selected="2"', html)
        self.assertIn('name="portfolio_tickers" value="AAPL"', html)
        self.assertIn('title="Apple (AAPL)"', html)
        self.assertIn('<span class="visually-hidden">, Apple</span>', html)
        # A label equal to the ticker adds no tooltip.
        self.assertNotIn('title="XLV"', html)

    def test_groups_render_headings_when_several(self) -> None:
        tickers = ["AAPL", "BTC-USD", "XLV", "GC=F"]
        html = self.render(
            "{{ forms.asset_selector(t, t, groups=groups) }}",
            t=tickers,
            groups=asset_groups(tickers),
        )
        self.assertIn("data-grouped", html)
        titles = re.findall(r'<p class="asset-group__title"[^>]*>([^<]+)</p>', html)
        self.assertEqual(titles, ["Acciones", "Criptomonedas", "Índices sectoriales", "Commodities"])

    def test_single_group_renders_flat(self) -> None:
        tickers = ["BTC-USD", "ETH-USD"]
        html = self.render(
            "{{ forms.asset_selector(t, [], groups=groups) }}",
            t=tickers,
            groups=asset_groups(tickers),
        )
        self.assertNotIn("asset-group__title", html)

    def test_large_lists_scroll(self) -> None:
        tickers = [f"T{index}" for index in range(30)]
        html = self.render("{{ forms.asset_selector(t, []) }}", t=tickers)
        self.assertIn("asset-selector__options--scroll", html)


class OtherMacroTests(MacroTestCase):
    def test_custom_tickers_input_submits_custom_tickers(self) -> None:
        html = self.render('{{ forms.custom_tickers_input("NVDA, ^VIX") }}')
        self.assertIn(
            '<input id="custom_tickers" name="custom_tickers" value="NVDA, ^VIX"', html
        )
        self.assertIn("data-custom-tickers", html)
        self.assertIn('placeholder="AMZN, NVDA, ^VIX"', html)
        self.assertIn("Yahoo Finance", html)

    def test_percent_input_attribute_order_and_options(self) -> None:
        html = self.render(
            '{{ forms.percent_input("min_MSFT", "5", min=0, max=100, placeholder="0") }}'
            '{{ forms.percent_input("view_NVDA", "25.0", field_id="view_NVDA", required=True) }}'
            '{{ forms.percent_input("rf", "", min=0) }}'
        )
        self.assertIn('id="min_MSFT" name="min_MSFT" type="number" value="5"', html)
        self.assertIn('step="any"', html)
        self.assertIn('inputmode="decimal"', html)
        self.assertIn('min="0" max="100" placeholder="0"', html)
        self.assertIn('id="view_NVDA" name="view_NVDA" type="number" value="25.0"', html)
        self.assertRegex(html, r'name="view_NVDA"[^>]*required>')
        self.assertIn('<span class="input-affix__suffix" aria-hidden="true">%</span>', html)
        self.assertNotIn("max=\"\"", html)

    def test_help_tip_is_described_tooltip(self) -> None:
        html = self.render('{{ forms.help_tip("Texto <b>de ayuda</b>", "Ayuda τ") }}')
        match = re.search(r'aria-describedby="(tip-\d+)"', html)
        self.assertIsNotNone(match)
        self.assertIn(f'role="tooltip" id="{match.group(1)}"', html)
        self.assertIn('aria-label="Ayuda τ"', html)
        self.assertIn('type="button"', html)
        self.assertIn("Texto &lt;b&gt;de ayuda&lt;/b&gt;", html)

    def test_date_presets_target_and_years(self) -> None:
        html = self.render('{{ forms.date_presets("start_date", (1, 3)) }}')
        self.assertIn('data-date-presets="start_date"', html)
        self.assertIn('data-years="1"', html)
        self.assertIn('data-years="3"', html)
        self.assertNotIn('data-years="2"', html)
        self.assertIn("desde hace 1 año)", html)
        self.assertIn("desde hace 3 años)", html)
        self.assertIn("data-custom-date", html)
        self.assertIn(" hidden>", html)  # revealed by forms.js


class UniversesPayloadTests(unittest.TestCase):
    def test_format_and_unique_valid_tickers(self) -> None:
        payload = universes_payload(UNIVERSOS_HRP)
        self.assertEqual(list(payload), list(UNIVERSOS_HRP))
        for name, entry in payload.items():
            with self.subTest(universe=name):
                self.assertEqual(set(entry), {"assets", "default"})
                tickers = [asset["ticker"] for asset in entry["assets"]]
                self.assertEqual(len(tickers), len(set(tickers)))
                self.assertEqual(tickers, list(dict.fromkeys(UNIVERSOS_HRP[name].values())))
                for asset in entry["assets"]:
                    self.assertEqual(set(asset), {"ticker", "label", "group"})
                    self.assertRegex(asset["ticker"], _TICKER_PATTERN)
                self.assertEqual(entry["default"], tickers)
        json.dumps(payload)

    def test_labels_prefer_descriptive_names(self) -> None:
        payload = universes_payload({"Todo": ACTIVOS_HRP})
        labels = {asset["ticker"]: asset["label"] for asset in payload["Todo"]["assets"]}
        self.assertEqual(labels["AAPL"], "Apple (AAPL)")
        self.assertEqual(labels["BTC-USD"], "Bitcoin (BTC-USD)")
        self.assertEqual(labels["JNJ"], "JNJ")
        self.assertEqual(asset_labels({"X": "X", "Nombre (X)": "X"}), {"X": "Nombre (X)"})

    def test_groups(self) -> None:
        groups = asset_groups(list(ACTIVOS_HRP.values()) + ["ZZZ"])
        self.assertEqual(groups["AAPL"], "Acciones")
        self.assertEqual(groups["BTC-USD"], "Criptomonedas")
        self.assertEqual(groups["XLV"], "Índices sectoriales")
        self.assertEqual(groups["GC=F"], "Commodities")
        self.assertEqual(groups["ECOPETROL.CL"], "Colombia")
        self.assertEqual(groups["ZZZ"], "Otros")
        self.assertTrue(set(groups.values()) <= set(GRUPOS_ACTIVOS) | {"Otros"})
        # A universe inside a single catalog group is not split.
        single = universes_payload(UNIVERSOS_HRP)["Portafolio actual"]["assets"]
        self.assertEqual({asset["group"] for asset in single}, {"Portafolio actual"})

    def test_default_selection_rule(self) -> None:
        def black_litterman_default(name, tickers):
            return tickers if name == "Portafolio actual" else tickers[:4]

        payload = universes_payload(UNIVERSOS_BLACK_LITTERMAN, black_litterman_default)
        self.assertEqual(payload["Activos principales"]["default"], ["AAPL", "MSFT", "GOOGL", "BTC-USD"])
        self.assertEqual(
            payload["Portafolio actual"]["default"],
            [asset["ticker"] for asset in payload["Portafolio actual"]["assets"]],
        )

    def test_default_drops_unknown_and_duplicate_tickers(self) -> None:
        payload = universes_payload(
            {"U": {"A": "AAA", "B": "BBB"}}, lambda name, tickers: ["BBB", "BBB", "ZZZ"]
        )
        self.assertEqual(payload["U"]["default"], ["BBB"])


class SubmittedFieldNamesTests(MacroTestCase):
    """The macros must keep the field names the server reads."""

    def test_field_names(self) -> None:
        html = self.render(
            '{{ forms.universe_select("universe", ["U"], "U", {}) }}'
            '{{ forms.asset_selector(["AAPL"], ["AAPL"]) }}'
            "{{ forms.custom_tickers_input() }}"
            '{{ forms.percent_input("min_weight", "0") }}'
            '{{ forms.percent_input("max_weight", "100") }}'
            '{{ forms.percent_input("risk_free_rate", "2.0") }}'
        )
        names = set(re.findall(r'\sname="([^"]+)"', html))
        self.assertEqual(
            names,
            {"universe", "tickers", "custom_tickers", "min_weight", "max_weight", "risk_free_rate"},
        )
        # JS-only controls are never submitted.
        self.assertNotRegex(html, r'data-asset-search[^>]*name=')
        self.assertNotRegex(html, r'<input type="search"[^>]*name=')


if __name__ == "__main__":
    unittest.main()
