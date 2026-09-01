from __future__ import annotations

from chief.models.base import ModelPrivacy, RouteRequirements

# Named specialty routes: callers pass one of these to generate_model() to delegate a task to
# whichever configured provider is optimized for it. Local Ollama is tagged "general" and always
# accepted as the fallback (see RouteRequirements.accepts), so every route stays usable even when
# its specialist provider is unconfigured or down.
CODING_ROUTE = RouteRequirements(
    allowed_privacy=frozenset(ModelPrivacy), specialties=frozenset({"coding"})
)
RESEARCH_ROUTE = RouteRequirements(
    allowed_privacy=frozenset(ModelPrivacy), specialties=frozenset({"research"})
)
SIGNALS_ROUTE = RouteRequirements(
    allowed_privacy=frozenset(ModelPrivacy), specialties=frozenset({"signals"})
)
VOICE_ROUTE = RouteRequirements(
    allowed_privacy=frozenset(ModelPrivacy), specialties=frozenset({"voice"})
)

# Ordered so a message matching more than one category resolves the same way every time.
_SPECIALTY_ROUTES: tuple[tuple[str, RouteRequirements], ...] = (
    ("coding", CODING_ROUTE),
    ("research", RESEARCH_ROUTE),
    ("signals", SIGNALS_ROUTE),
)

_SPECIALTY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "coding": (
        "bug",
        "codebase",
        "coding",
        "compile",
        "debug",
        "exception",
        "git ",
        "pull request",
        "pytest",
        "refactor",
        "regex",
        "repo",
        "sql query",
        "stack trace",
        "unit test",
        "write code",
        "write a function",
    ),
    "research": (
        "compare options",
        "competitor",
        "deep dive",
        "due diligence",
        "industry trend",
        "literature review",
        "market analysis",
        "research",
        "whitepaper",
    ),
    "signals": (
        "breaking",
        "current price",
        "happening right now",
        "latest news",
        "real-time",
        "stock price",
        "this week's",
        "today's",
    ),
}


def classify_task_specialty(message: str) -> RouteRequirements | None:
    """Recognize a narrow set of explicit specialty signals in a chat message.

    Deterministic and code-owned, matching DeterministicToolPlanner's approach to tool intent:
    a fixed keyword map rather than an extra model call just to pick a route. Returns None when
    no specialty is recognized, so the caller falls back to CHIEF's default, unrestricted route.
    """

    lowered = message.casefold()
    for specialty, route in _SPECIALTY_ROUTES:
        if any(keyword in lowered for keyword in _SPECIALTY_KEYWORDS[specialty]):
            return route
    return None
