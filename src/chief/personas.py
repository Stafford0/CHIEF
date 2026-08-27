from pathlib import Path

_PERSONA_ROOT = Path(__file__).resolve().parents[2] / "personas"


def load_persona(filename: str) -> str:
    """Load a version-controlled runtime persona without accepting arbitrary paths."""

    if not filename or Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("Persona filename must be a Markdown basename.")
    path = _PERSONA_ROOT / filename
    try:
        content = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required persona file is missing: {filename}") from exc
    if not content:
        raise RuntimeError(f"Required persona file is empty: {filename}")
    return content


CHIEF_PERSONA = load_persona("chief_v1.md")
ULTRON_PERSONA = load_persona("ultron_mcu.md")
