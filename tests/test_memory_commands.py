from chief.memory.commands import MemoryCommandParser


def test_parses_chief_remember_command() -> None:
    parser = MemoryCommandParser()

    command = parser.parse(
        "Chief, remember that Parcel Signals launches September 1."
    )

    assert command is not None
    assert command.content == "Parcel Signals launches September 1."


def test_parses_simple_remember_command() -> None:
    parser = MemoryCommandParser()

    command = parser.parse(
        "Remember my preferred database is PostgreSQL."
    )

    assert command is not None
    assert command.content == "my preferred database is PostgreSQL."


def test_parses_polite_remember_command() -> None:
    parser = MemoryCommandParser()

    command = parser.parse(
        "Please remember that CHIEF runs locally."
    )

    assert command is not None
    assert command.content == "CHIEF runs locally."


def test_parser_is_case_insensitive() -> None:
    parser = MemoryCommandParser()

    command = parser.parse(
        "CHIEF, REMEMBER THAT Project Atlas is priority one."
    )

    assert command is not None
    assert command.content == "Project Atlas is priority one."


def test_does_not_store_normal_statement() -> None:
    parser = MemoryCommandParser()

    command = parser.parse(
        "I might use PostgreSQL."
    )

    assert command is None


def test_does_not_store_memory_question() -> None:
    parser = MemoryCommandParser()

    command = parser.parse(
        "Do you remember what database we discussed?"
    )

    assert command is None


def test_does_not_store_recall_request() -> None:
    parser = MemoryCommandParser()

    command = parser.parse(
        "What do you remember about Parcel Signals?"
    )

    assert command is None


def test_rejects_empty_memory_command() -> None:
    parser = MemoryCommandParser()

    command = parser.parse("Chief, remember that")

    assert command is None