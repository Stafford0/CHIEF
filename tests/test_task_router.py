from chief.core.task_router import (
    CODING_ROUTE,
    RESEARCH_ROUTE,
    SIGNALS_ROUTE,
    classify_task_specialty,
)


def test_classifies_coding_message() -> None:
    assert classify_task_specialty("There's a bug in this function, can you debug it?") == (
        CODING_ROUTE
    )


def test_classifies_research_message() -> None:
    assert classify_task_specialty("Do a deep dive on our top three competitors.") == (
        RESEARCH_ROUTE
    )


def test_classifies_signals_message() -> None:
    assert classify_task_specialty("What's the latest news on our main supplier?") == (
        SIGNALS_ROUTE
    )


def test_classification_is_case_insensitive() -> None:
    assert classify_task_specialty("REFACTOR this class") == CODING_ROUTE


def test_unmatched_message_returns_none() -> None:
    assert classify_task_specialty("Good morning, how are you today?") is None


def test_first_matching_category_wins_on_ambiguous_message() -> None:
    # Contains both a coding keyword ("bug") and a research keyword ("research"); coding is
    # checked first, so it should win deterministically rather than being ambiguous.
    assert classify_task_specialty("Research this bug for me.") == CODING_ROUTE
