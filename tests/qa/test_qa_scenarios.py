"""Run every QA scenario in ``tests/qa/scenarios.py`` (list: docs/qa-scenarios.md)."""

import pytest

from .scenarios import SCENARIOS


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            s,
            id=s.id,
            marks=[pytest.mark.xfail(reason=s.known_issue, strict=True)] if s.known_issue else [],
        )
        for s in SCENARIOS
    ],
)
def test_scenario(case):
    case.check()


def test_doc_matches_scenarios():
    from .make_doc import doc_is_current

    assert doc_is_current(), "docs/qa-scenarios.md is stale: python -m tests.qa.make_doc"
