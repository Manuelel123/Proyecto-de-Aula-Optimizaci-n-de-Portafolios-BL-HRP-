"""Risk profiling: questionnaire loading (YAML) and profile calculation.

Pure package (no Flask). The questionnaire lives in ``config/cuestionario.yaml``;
see ``questionnaire`` (loading and validation) and ``scoring`` (rules).
"""

from optimizacion_portafolios.profiling.questionnaire import (
    DEFAULT_PATH,
    HORIZON_FACTOR,
    PRIOR_STEP,
    SUGGESTABLE_ROLES,
    VETO_FACTOR,
    Questionnaire,
    QuestionnaireError,
    load_questionnaire,
    parse_questionnaire,
)
from optimizacion_portafolios.profiling.scoring import (
    AnswerError,
    ProfileResult,
    block_score,
    clean_answers,
    evaluate,
    explain,
    format_score,
    level_for_score,
    suggest_role,
)

__all__ = [
    "DEFAULT_PATH",
    "HORIZON_FACTOR",
    "PRIOR_STEP",
    "SUGGESTABLE_ROLES",
    "VETO_FACTOR",
    "AnswerError",
    "ProfileResult",
    "Questionnaire",
    "QuestionnaireError",
    "block_score",
    "clean_answers",
    "evaluate",
    "explain",
    "format_score",
    "level_for_score",
    "load_questionnaire",
    "parse_questionnaire",
    "suggest_role",
]
