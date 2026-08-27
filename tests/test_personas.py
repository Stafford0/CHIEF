from importlib.resources import files

import pytest

from chief.personas import CHIEF_PERSONA, ULTRON_PERSONA, load_persona


def test_versioned_personas_are_loaded() -> None:
    assert "primary operations partner" in CHIEF_PERSONA
    assert "Author only CHIEF's contribution" in CHIEF_PERSONA
    assert "zero tool access" in ULTRON_PERSONA
    assert "architectural permission boundary" in ULTRON_PERSONA
    assert "Author only Ultron's contribution" in ULTRON_PERSONA
    assert "[[SILENT]]" in ULTRON_PERSONA
    assert files("chief.persona_files").joinpath("chief_v1.md").is_file()


@pytest.mark.parametrize("filename", ["../chief_v1.md", "personas/chief_v1.md", "chief_v1.txt"])
def test_persona_loader_rejects_paths_and_non_markdown(filename: str) -> None:
    with pytest.raises(ValueError):
        load_persona(filename)
