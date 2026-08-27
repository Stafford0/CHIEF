import pytest

from chief.personas import CHIEF_PERSONA, ULTRON_PERSONA, load_persona


def test_versioned_personas_are_loaded() -> None:
    assert "primary operations partner" in CHIEF_PERSONA
    assert "zero tool access" in ULTRON_PERSONA
    assert "[[SILENT]]" in ULTRON_PERSONA


@pytest.mark.parametrize("filename", ["../chief_v1.md", "personas/chief_v1.md", "chief_v1.txt"])
def test_persona_loader_rejects_paths_and_non_markdown(filename: str) -> None:
    with pytest.raises(ValueError):
        load_persona(filename)
