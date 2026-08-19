from chief.core.identity import SYSTEM_IDENTITY


def test_system_identity_contains_chief_identity() -> None:
    assert "You are CHIEF" in SYSTEM_IDENTITY
    assert "Cognitive Hub for Intelligence, Execution & Foresight" in SYSTEM_IDENTITY
    assert "CHIEF ZERO" in SYSTEM_IDENTITY


def test_system_identity_requires_capability_honesty() -> None:
    assert "CAPABILITY HONESTY" in SYSTEM_IDENTITY
    assert "Never claim access" in SYSTEM_IDENTITY


def test_system_identity_separates_chief_from_provider() -> None:
    assert "CHIEF is not the underlying language model" in SYSTEM_IDENTITY
    assert "Models are intelligence providers" in SYSTEM_IDENTITY