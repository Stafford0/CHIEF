import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryCommand:
    """A user explicitly requesting that CHIEF remember something."""

    content: str


class MemoryCommandParser:
    """Detect explicit user requests to create persistent memory."""

    _PATTERNS = (
        r"^\s*chief[\s,:-]+remember\s+(?:that\s+)?(.+?)\s*$",
        r"^\s*please\s+remember\s+(?:that\s+)?(.+?)\s*$",
        r"^\s*remember\s+(?:that\s+)?(.+?)\s*$",
    )

    def parse(self, message: str) -> MemoryCommand | None:
        """Return a memory command only for explicit remember requests."""

        for pattern in self._PATTERNS:
            match = re.match(
                pattern,
                message,
                flags=re.IGNORECASE | re.DOTALL,
            )

            if match:
                content = match.group(1).strip()

                if content.lower() == "that":
                    continue

                if content:
                    return MemoryCommand(content=content)

        return None