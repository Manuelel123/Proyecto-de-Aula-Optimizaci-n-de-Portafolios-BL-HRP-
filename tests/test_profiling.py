"""Risk-profile questionnaire: YAML loading/validation, scores, final profile, vetoes and role."""

import copy
import tempfile
import unittest
from itertools import product
from pathlib import Path

import yaml

from optimizacion_portafolios.profiling import (
    DEFAULT_PATH,
    AnswerError,
    QuestionnaireError,
    block_score,
    evaluate,
    explain,
    level_for_score,
    load_questionnaire,
    parse_questionnaire,
    suggest_role,
)

QUESTIONNAIRE = load_questionnaire()
RAW = yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8"))

# Answers that put each scored block at a given level without triggering a veto
# (p1 = 1 "Vendo todo" and p6 = 1 "Menos de 1" are vetoes).
TOLERANCE = {
    "adverso": {"p1": 2, "p2": 1, "p3": 1, "p4": 1, "p5": 1},  # 1.2
    "moderado": {"p1": 2, "p2": 2, "p3": 2, "p4": 2, "p5": 2},  # 2.0
    "arriesgado": {"p1": 4, "p2": 4, "p3": 3, "p4": 3, "p5": 3},  # 3.4
}
CAPACITY = {
    "adverso": {"p6": 2, "p7": 1, "p8": 1},  # 1.33
    "moderado": {"p6": 2, "p7": 3, "p8": 3},  # 2.67
    "arriesgado": {"p6": 4, "p7": 3, "p8": 2},  # 3.0
}
KNOWLEDGE = {"k1": 3, "k2": 2}
HORIZON_CAP = {"menos_1": "adverso", "de_1_a_3": "moderado", "mas_3": "arriesgado"}
RANK = {"adverso": 0, "moderado": 1, "arriesgado": 2}


def answers(tolerance="moderado", capacity="moderado", **overrides):
    return {**TOLERANCE[tolerance], **CAPACITY[capacity], **KNOWLEDGE, **overrides}


class QuestionnaireFileTests(unittest.TestCase):
    def test_file_content(self):
        q = QUESTIONNAIRE
        self.assertTrue(q.version)
        self.assertEqual(q.level_ids, ("adverso", "moderado", "arriesgado"))
        self.assertEqual([block.id for block in q.blocks], ["tolerancia", "capacidad", "conocimiento"])
        self.assertEqual(q.question_ids, ("p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "k1", "k2"))
        self.assertFalse(q.block("conocimiento").affects_profile)
        self.assertEqual(q.question("p1").text, "Su inversión cae 20 % en tres meses. ¿Qué hace?")
        self.assertEqual(q.question("p1").options, ("Vendo todo", "Vendo una parte", "Mantengo", "Compro más"))
        self.assertEqual(q.question("p6").options, ("Menos de 1", "1 a 3", "3 a 6", "Más de 6"))
        self.assertEqual(q.question("p5").options[0], '"Prefiero no arriesgar"')
        self.assertEqual([choice.text for choice in q.horizon_options], ["Menos de 1 año", "1 a 3 años", "Más de 3 años"])
        self.assertEqual(len(q.capital_options), 5)
        self.assertEqual({(veto.question, veto.option_text) for veto in q.vetoes},
                         {("p1", "Vendo todo"), ("p6", "Menos de 1")})
        self.assertEqual(q.tiebreak, ("veto", "horizonte", "capacidad", "tolerancia"))
        self.assertEqual(q.question("k2").correct, 2)
        for block in q.blocks:
            for question in block.questions:
                self.assertEqual(len(question.options), 4, question.id)

    def _broken(self, mutate):
        data = copy.deepcopy(RAW)
        mutate(data)
        with self.assertRaises(QuestionnaireError) as caught:
            parse_questionnaire(data, source="prueba.yaml")
        self.assertIn("prueba.yaml", str(caught.exception))
        return str(caught.exception)

    def test_invalid_files_fail_with_a_clear_message(self):
        self.assertIn("version", self._broken(lambda d: d.pop("version")))
        self.assertIn("exactamente 4 opciones", self._broken(
            lambda d: d["bloques"][0]["preguntas"][0]["opciones"].pop()))
        self.assertIn("no es una opción", self._broken(lambda d: d["vetos"][0].update(opcion="Vendo casi todo")))
        self.assertIn("desempate", self._broken(lambda d: d["factor_limitante"]["desempate"].remove("capacidad")))
        self.assertIn("no es un nivel", self._broken(
            lambda d: d["datos_previos"]["horizonte"]["opciones"][0].update(nivel_maximo="extremo")))
        self.assertIn("umbrales", self._broken(lambda d: d["umbrales"].pop("arriesgado")))
        self.assertIn("crecer", self._broken(lambda d: d["umbrales"].update(moderado=3.5)))
        self.assertIn("rol sugerible", self._broken(lambda d: d["rol_sugerido"].update(si_cumple="gestor")))
        self.assertIn("marcador", self._broken(
            lambda d: d["factor_limitante"]["explicaciones"].update(capacidad="{desconocido}")))
        self.assertIn("repetido", self._broken(lambda d: d["bloques"][1]["preguntas"][0].update(id="p1")))

    def test_missing_or_malformed_yaml(self):
        with self.assertRaises(QuestionnaireError):
            load_questionnaire(Path(tempfile.gettempdir()) / "no-existe-cuestionario.yaml")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "malo.yaml"
            path.write_text("version: 1\nniveles: [\n", encoding="utf-8")
            with self.assertRaises(QuestionnaireError) as caught:
                load_questionnaire(path)
            self.assertIn("YAML no es válido", str(caught.exception))


class ScoreTests(unittest.TestCase):
    def test_block_score_is_the_average_of_positions(self):
        given = answers(p1=4, p2=3, p3=2, p4=1, p5=3)
        self.assertEqual(float(block_score(QUESTIONNAIRE, "tolerancia", given)), 2.6)
        self.assertAlmostEqual(float(block_score(QUESTIONNAIRE, "capacidad", answers("moderado", "adverso"))), 4 / 3)

    def test_thresholds(self):
        cases = {1.0: "adverso", 1.99: "adverso", 2.0: "moderado", 2.8: "moderado", 2.99: "moderado",
                 3.0: "arriesgado", 4.0: "arriesgado"}
        for score, level in cases.items():
            self.assertEqual(level_for_score(QUESTIONNAIRE, score), level, score)

    def test_result_fields(self):
        result = evaluate(QUESTIONNAIRE, "5m_20m", "mas_3", answers("arriesgado", "moderado"))
        self.assertEqual(result.scores, {"tolerancia": 3.4, "capacidad": 8 / 3})
        self.assertEqual(result.block_levels, {"tolerancia": "arriesgado", "capacidad": "moderado"})
        self.assertEqual((result.final_level, result.limiting_factor), ("moderado", "capacidad"))
        self.assertEqual(result.horizon_level, "arriesgado")
        self.assertFalse(result.horizon_capped)

    def test_incomplete_or_invalid_answers(self):
        partial = answers()
        partial.pop("p8")
        with self.assertRaises(AnswerError):
            evaluate(QUESTIONNAIRE, "5m_20m", "mas_3", partial)
        with self.assertRaises(AnswerError):
            evaluate(QUESTIONNAIRE, "5m_20m", "mas_3", answers(p2=5))
        with self.assertRaises(AnswerError):
            evaluate(QUESTIONNAIRE, "otro", "mas_3", answers())
        with self.assertRaises(AnswerError):
            evaluate(QUESTIONNAIRE, "5m_20m", "nunca", answers())


class FinalProfileTests(unittest.TestCase):
    def test_final_profile_is_the_lowest_for_every_combination(self):
        for tolerance, capacity, horizon in product(TOLERANCE, CAPACITY, HORIZON_CAP):
            with self.subTest(tolerance=tolerance, capacity=capacity, horizon=horizon):
                result = evaluate(QUESTIONNAIRE, "1m_5m", horizon, answers(tolerance, capacity))
                expected = min((tolerance, capacity, HORIZON_CAP[horizon]), key=RANK.get)
                self.assertEqual(result.final_level, expected)
                self.assertEqual(result.block_levels, {"tolerancia": tolerance, "capacidad": capacity})
                self.assertEqual(result.horizon_level, HORIZON_CAP[horizon])
                # Tie-break: horizon (only when it caps) > capacity > tolerance.
                if horizon != "mas_3" and HORIZON_CAP[horizon] == expected:
                    factor = "horizonte"
                elif capacity == expected:
                    factor = "capacidad"
                else:
                    factor = "tolerancia"
                self.assertEqual(result.limiting_factor, factor)
                self.assertIsNone(result.veto)

    def test_horizon_caps(self):
        risky = answers("arriesgado", "arriesgado")
        self.assertEqual(evaluate(QUESTIONNAIRE, "mas_100m", "menos_1", risky).final_level, "adverso")
        self.assertEqual(evaluate(QUESTIONNAIRE, "mas_100m", "de_1_a_3", risky).final_level, "moderado")
        uncapped = evaluate(QUESTIONNAIRE, "mas_100m", "mas_3", risky)
        # Capacity and tolerance tie at the top: capacity wins the tie-break.
        self.assertEqual((uncapped.final_level, uncapped.limiting_factor), ("arriesgado", "capacidad"))

    def test_vetoes_force_adverse(self):
        risky = answers("arriesgado", "arriesgado")
        for question, option in (("p1", 1), ("p6", 1)):
            with self.subTest(question=question):
                result = evaluate(QUESTIONNAIRE, "mas_100m", "mas_3", {**risky, question: option})
                self.assertEqual((result.final_level, result.limiting_factor), ("adverso", "veto"))
                self.assertEqual(result.veto.question, question)
                self.assertEqual(explain(QUESTIONNAIRE, result), result.veto.explanation)
        both = evaluate(QUESTIONNAIRE, "mas_100m", "mas_3", {**risky, "p1": 1, "p6": 1})
        self.assertEqual(len(both.vetoes), 2)
        # A veto wins the tie-break even when another factor is also adverse.
        short = evaluate(QUESTIONNAIRE, "mas_100m", "menos_1", {**risky, "p1": 1})
        self.assertEqual(short.limiting_factor, "veto")
        # "Vendo una parte" or "1 a 3" months are not vetoes.
        self.assertIsNone(evaluate(QUESTIONNAIRE, "mas_100m", "mas_3", {**risky, "p1": 2, "p6": 2}).veto)

    def test_knowledge_does_not_change_the_profile(self):
        base = evaluate(QUESTIONNAIRE, "1m_5m", "mas_3", answers("arriesgado", "moderado"))
        for k1, k2 in product(range(1, 5), range(1, 5)):
            result = evaluate(QUESTIONNAIRE, "1m_5m", "mas_3", answers("arriesgado", "moderado", k1=k1, k2=k2))
            self.assertEqual((result.final_level, result.limiting_factor, result.scores),
                             (base.final_level, base.limiting_factor, base.scores))

    def test_explanations_are_plain_language(self):
        horizon = evaluate(QUESTIONNAIRE, "1m_5m", "menos_1", answers("arriesgado", "arriesgado"))
        self.assertIn("menos de 1 año", explain(QUESTIONNAIRE, horizon))
        self.assertIn("Adverso al riesgo", explain(QUESTIONNAIRE, horizon))
        capacity = evaluate(QUESTIONNAIRE, "1m_5m", "mas_3", answers("arriesgado", "adverso"))
        self.assertIn("1,33 de 4", explain(QUESTIONNAIRE, capacity))
        tolerance = evaluate(QUESTIONNAIRE, "1m_5m", "mas_3", answers("adverso", "arriesgado"))
        self.assertIn("tolerancia al riesgo (promedio 1,20 de 4)", explain(QUESTIONNAIRE, tolerance))


class SuggestedRoleTests(unittest.TestCase):
    def test_rule(self):
        cases = {
            (3, 2): "inversionista",  # funds or stocks, knows what diversifying means
            (4, 2): "inversionista",
            (4, 1): "explorador",  # experienced but wrong answer
            (2, 2): "explorador",  # right answer, only savings/CDT
            (1, 4): "explorador",
        }
        for (k1, k2), role in cases.items():
            self.assertEqual(suggest_role(QUESTIONNAIRE, {"k1": k1, "k2": k2}), role, (k1, k2))
            result = evaluate(QUESTIONNAIRE, "1m_5m", "mas_3", answers(k1=k1, k2=k2))
            self.assertEqual(result.suggested_role, role)


if __name__ == "__main__":
    unittest.main()
