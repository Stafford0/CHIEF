import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryCommand:
    """A user explicitly requesting that CHIEF remember something."""

    content: str


@dataclass(frozen=True)
class CorrectMemoryCommand:
    """A user explicitly requesting correction of a memory."""

    old_content: str
    new_content: str


@dataclass(frozen=True)
class ForgetMemoryCommand:
    """A user explicitly requesting that CHIEF forget a memory."""

    content: str


class MemoryCommandParser:
    """Detect explicit user requests that modify persistent memory."""

    _REMEMBER_PATTERNS = (
        r"^\s*chief[\s,:-]+remember\s+(?:that\s+)?(.+?)\s*$",
        r"^\s*please\s+remember\s+(?:that\s+)?(.+?)\s*$",
        r"^\s*remember\s+(?:that\s+)?(.+?)\s*$",
    )

    _CORRECT_PATTERN = (
        r"^\s*(?:chief[\s,:-]+)?correct\s+"
        r'"([^"]+)"\s+to\s+"([^"]+)"\s*$'
    )

    _FORGET_PATTERN = r'^\s*(?:chief[\s,:-]+)?forget\s+"([^"]+)"\s*$'

    def parse(
        self,
        message: str,
    ) -> MemoryCommand | CorrectMemoryCommand | ForgetMemoryCommand | None:
        """Parse explicit persistent-memory commands."""

        correction = re.match(
            self._CORRECT_PATTERN,
            message,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if correction:
            old_content = correction.group(1).strip()
            new_content = correction.group(2).strip()

            if old_content and new_content:
                return CorrectMemoryCommand(
                    old_content=old_content,
                    new_content=new_content,
                )

        forgetting = re.match(
            self._FORGET_PATTERN,
            message,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if forgetting:
            content = forgetting.group(1).strip()

            if content:
                return ForgetMemoryCommand(
                    content=content,
                )

        for pattern in self._REMEMBER_PATTERNS:
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
                    return MemoryCommand(
                        content=content,
                    )

        return None
