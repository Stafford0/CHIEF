from chief.memory.commands import MemoryCommandParser


def test_parses_chief_remember_command() -> None:
    parser = MemoryCommandParser()

    command = parser.parse("Chief, remember that Parcel Signals launches September 1.")

    assert command is not None
    assert command.content == "Parcel Signals launches September 1."


def test_parses_simple_remember_command() -> None:
    parser = MemoryCommandParser()

    command = parser.parse("Remember my preferred database is PostgreSQL.")

    assert command is not None
    assert command.content == "my preferred database is PostgreSQL."


def test_parses_polite_remember_command() -> None:
    parser = MemoryCommandParser()

    command = parser.parse("Please remember that CHIEF runs locally.")

    assert command is not None
    assert command.content == "CHIEF runs locally."


def test_parser_is_case_insensitive() -> None:
    parser = MemoryCommandParser()

    command = parser.parse("CHIEF, REMEMBER THAT Project Atlas is priority one.")

    assert command is not None
    assert command.content == "Project Atlas is priority one."


def test_does_not_store_normal_statement() -> None:
    parser = MemoryCommandParser()

    command = parser.parse("I might use PostgreSQL.")

    assert command is None


def test_does_not_store_memory_question() -> None:
    parser = MemoryCommandParser()

    command = parser.parse("Do you remember what database we discussed?")

    assert command is None


def test_does_not_store_recall_request() -> None:
    parser = MemoryCommandParser()

    command = parser.parse("What do you remember about Parcel Signals?")

    assert command is None


def test_rejects_empty_memory_command() -> None:
    parser = MemoryCommandParser()

    command = parser.parse("Chief, remember that")

    assert command is None


def test_parses_correction_command() -> None:
    from chief.memory.commands import CorrectMemoryCommand

    parser = MemoryCommandParser()

    command = parser.parse(
        'Chief, correct "Project Atlas uses BLUE-731." to "Project Atlas uses RED-942."'
    )

    assert isinstance(command, CorrectMemoryCommand)
    assert command.old_content == "Project Atlas uses BLUE-731."
    assert command.new_content == "Project Atlas uses RED-942."


def test_parses_forget_command() -> None:
    from chief.memory.commands import ForgetMemoryCommand

    parser = MemoryCommandParser()

    command = parser.parse('Chief, forget "Project Atlas uses RED-942."')

    assert isinstance(command, ForgetMemoryCommand)
    assert command.content == "Project Atlas uses RED-942."


def test_correction_requires_quotes() -> None:
    parser = MemoryCommandParser()

    command = parser.parse(
        "Chief, correct Project Atlas uses BLUE-731 to Project Atlas uses RED-942"
    )

    assert command is None


def test_forget_requires_quotes() -> None:
    parser = MemoryCommandParser()

    command = parser.parse("Chief, forget Project Atlas uses RED-942.")

    assert command is None


def test_rejects_incomplete_correction() -> None:
    parser = MemoryCommandParser()

    command = parser.parse('Chief, correct "Project Atlas uses BLUE-731."')

    assert command is None


def test_normal_conversation_still_not_a_command() -> None:
    parser = MemoryCommandParser()

    command = parser.parse("I need to correct the Project Atlas access code later.")

    assert command is None
