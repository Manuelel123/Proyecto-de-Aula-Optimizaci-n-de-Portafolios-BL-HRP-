"""Risk profile from the questionnaire answers (pure functions, no Flask).

Rules (data in ``config/cuestionario.yaml``):

1. Each answer is worth 1 to 4 (its position). The score of a block that
   affects the profile is the average of its answers; the block level is the
   highest level whose threshold the average reaches (exact arithmetic, so
   2.0 is already "moderado").
2. The horizon caps the profile (``nivel_maximo``; ``null`` = no cap).
3. Final profile = the lowest of the block levels and the horizon cap.
4. A veto answer forces the lowest level.
5. Limiting factor: a veto if any applies; otherwise the first factor of
   ``factor_limitante.desempate`` whose level equals the final one (the
   horizon only counts when it imposes a cap). With the shipped file the order
   is veto > horizonte > capacidad > tolerancia: objective constraints are
   reported before the personal preference.
6. Suggested role: ``si_cumple`` when the experience answer is at least
   ``experiencia_minima`` and the control question is answered correctly,
   else ``si_no``. Knowledge never changes the risk profile.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction

from optimizacion_portafolios.profiling.questionnaire import (
    HORIZON_FACTOR,
    OPTIONS_PER_QUESTION,
    VETO_FACTOR,
    Questionnaire,
    Veto,
)


class AnswerError(ValueError):
    """Missing or invalid answers."""


@dataclass(frozen=True)
class ProfileResult:
    version: str
    capital: str
    horizon: str
    answers: dict[str, int]
    scores: dict[str, float]  # block id -> average (scored blocks only)
    block_levels: dict[str, str]
    horizon_level: str  # the cap, or the highest level when there is none
    horizon_capped: bool
    final_level: str
    limiting_factor: str
    veto: Veto | None
    suggested_role: str
    vetoes: tuple[Veto, ...] = field(default=())


def _coerce_answer(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= OPTIONS_PER_QUESTION and str(value).strip() == str(number) else None


def clean_answers(questionnaire: Questionnaire, raw: Mapping) -> dict[str, int]:
    """Valid answers in ``raw`` (unknown questions and bad values are dropped)."""
    answers = {}
    for question_id in questionnaire.question_ids:
        if question_id in raw:
            value = _coerce_answer(raw[question_id])
            if value is not None:
                answers[question_id] = value
    return answers


def validate(questionnaire: Questionnaire, capital: str, horizon: str, answers: Mapping) -> dict[str, int]:
    """Clean answers of a complete questionnaire; ``AnswerError`` if anything is missing."""
    try:
        questionnaire.capital(capital)
    except KeyError as error:
        raise AnswerError("Elija un rango de capital.") from error
    try:
        questionnaire.horizon(horizon)
    except KeyError as error:
        raise AnswerError("Elija un horizonte de inversión.") from error
    cleaned = clean_answers(questionnaire, answers)
    missing = [question_id for question_id in questionnaire.question_ids if question_id not in cleaned]
    if missing:
        raise AnswerError("Faltan respuestas: " + ", ".join(missing) + ".")
    return cleaned


def block_score(questionnaire: Questionnaire, block_id: str, answers: Mapping[str, int]) -> Fraction:
    """Exact average of the answers of ``block_id``."""
    questions = questionnaire.block(block_id).questions
    return Fraction(sum(answers[question.id] for question in questions), len(questions))


def level_for_score(questionnaire: Questionnaire, score) -> str:
    score = Fraction(str(score)) if isinstance(score, float) else Fraction(score)
    level = questionnaire.levels[0].id
    for level_id, minimum in questionnaire.thresholds:
        if score >= minimum:
            level = level_id
    return level


def triggered_vetoes(questionnaire: Questionnaire, answers: Mapping[str, int]) -> tuple[Veto, ...]:
    return tuple(veto for veto in questionnaire.vetoes if answers.get(veto.question) == veto.option)


def suggest_role(questionnaire: Questionnaire, answers: Mapping[str, int]) -> str:
    rule = questionnaire.role_rule
    control = questionnaire.question(rule.control_question)
    experienced = answers.get(rule.experience_question, 0) >= rule.min_experience
    knows = answers.get(rule.control_question) == control.correct
    return rule.if_met if experienced and knows else rule.otherwise


def evaluate(questionnaire: Questionnaire, capital: str, horizon: str, answers: Mapping) -> ProfileResult:
    """Profile of a complete set of answers (raises ``AnswerError`` if incomplete)."""
    answers = validate(questionnaire, capital, horizon, answers)
    rank = questionnaire.level_rank
    scores = {block.id: block_score(questionnaire, block.id, answers) for block in questionnaire.scored_blocks}
    block_levels = {block_id: level_for_score(questionnaire, score) for block_id, score in scores.items()}
    cap = questionnaire.horizon(horizon).max_level
    horizon_level = cap or questionnaire.levels[-1].id
    candidates = {**block_levels, HORIZON_FACTOR: horizon_level}
    vetoes = triggered_vetoes(questionnaire, answers)
    if vetoes:
        final = questionnaire.levels[0].id
        factor = VETO_FACTOR
    else:
        final = min(candidates.values(), key=rank)
        factor = next(
            name
            for name in questionnaire.tiebreak
            if name != VETO_FACTOR
            and candidates[name] == final
            and (name != HORIZON_FACTOR or cap is not None)
        )
    return ProfileResult(
        version=questionnaire.version,
        capital=capital,
        horizon=horizon,
        answers=answers,
        scores={block_id: float(score) for block_id, score in scores.items()},
        block_levels=block_levels,
        horizon_level=horizon_level,
        horizon_capped=cap is not None,
        final_level=final,
        limiting_factor=factor,
        veto=vetoes[0] if vetoes else None,
        suggested_role=suggest_role(questionnaire, answers),
        vetoes=vetoes,
    )


def format_score(value: float) -> str:
    """``2.4`` -> ``"2,40"`` (Spanish decimal comma)."""
    return f"{value:.2f}".replace(".", ",")


def explain(questionnaire: Questionnaire, result: ProfileResult) -> str:
    """Plain-language reason for the final profile (shown to the user)."""
    if result.limiting_factor == VETO_FACTOR and result.veto is not None:
        return result.veto.explanation
    horizon_text = questionnaire.horizon(result.horizon).text
    values = {
        "perfil": questionnaire.level(result.final_level).label,
        "horizonte": horizon_text[:1].lower() + horizon_text[1:],
    }
    for block_id, score in result.scores.items():
        values[f"puntaje_{block_id}"] = format_score(score)
        values[f"nivel_{block_id}"] = questionnaire.level(result.block_levels[block_id]).label
    return questionnaire.explanations[result.limiting_factor].format_map(values)
