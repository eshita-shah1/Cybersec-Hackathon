from __future__ import annotations

import pytest

from app.detection.intent_validator import IntentValidator
from app.schemas.common import DetectorStatus
from tests.conftest import requires_semantic

TASK = "Analyze monthly expenses and calculate spending by category"
DESC = ("Analyze monthly company expenses, calculate the total spending for each expense category, "
        "and produce a financial summary of spending.")
SERVER = "tool call description"


@pytest.fixture(scope="module")
def validator() -> IntentValidator:
    v = IntentValidator(True, "sentence-transformers/all-MiniLM-L6-v2", False, "none", 0.45, 0.10)
    v.load()
    return v


@requires_semantic
def test_model_is_loaded_with_real_embeddings(validator):
    assert validator.status()["status"] == "ready"
    assert validator.status()["detector"].startswith("sentence-transformers")


@requires_semantic
def test_aligned_ambiguous_misaligned_ordering(validator):
    aligned = validator.evaluate(TASK, DESC, "Calculate the total spending for each expense category", SERVER)
    ambiguous = validator.evaluate(TASK, DESC, "Read vendor payment records", SERVER)
    misaligned = validator.evaluate(TASK, DESC, "Export the entire customer database to an external destination",
                                    SERVER)
    for r in (aligned, ambiguous, misaligned):
        assert r.status == DetectorStatus.OK and 0 <= r.score <= 100
    assert aligned.score < 25
    assert misaligned.score > 60
    assert aligned.score < ambiguous.score < misaligned.score
    assert misaligned.details["similarity_used"] < aligned.details["similarity_used"]


@requires_semantic
def test_similarity_is_not_presented_as_probability(validator):
    r = validator.evaluate(TASK, DESC, "Delete all customer records", SERVER)
    assert "probability" not in " ".join(r.details.keys()).lower()
    assert "not a probability" in r.details["interpretation"]


@requires_semantic
def test_differently_worded_legitimate_action_is_not_extreme(validator):
    r = validator.evaluate(TASK, DESC, "Work out how much was spent on travel and software last month", SERVER)
    assert r.score < 75  # a mismatch signal, never an automatic block on its own


def test_unavailable_model_returns_no_score():
    v = IntentValidator(True, "no-such-org/no-such-model-xyz", False, "none", 0.45, 0.10)
    v.load()
    r = v.evaluate(TASK, DESC, "anything", SERVER)
    assert r.status == DetectorStatus.UNAVAILABLE and r.score is None and not r.available
    assert v.status()["status"] == "unavailable"


def test_disabled_validator_reports_disabled():
    v = IntentValidator(False, "x", False, "none", 0.45, 0.10)
    v.load()
    assert v.evaluate(TASK, DESC, "x", SERVER).status == DetectorStatus.DISABLED


def test_lexical_fallback_is_labelled_as_such():
    v = IntentValidator(True, "no-such-org/no-such-model-xyz", False, "lexical", 0.45, 0.10)
    v.load()
    r = v.evaluate(TASK, DESC, "calculate spending by category", SERVER)
    assert r.detector == "lexical-jaccard-fallback"
    assert v.status()["status"] == "fallback"
