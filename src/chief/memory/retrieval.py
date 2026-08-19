import re
from dataclasses import dataclass

from chief.memory.schema import MemoryRecord
from chief.memory.store import MemoryStore


@dataclass(frozen=True)
class RetrievedMemory:
    memory: MemoryRecord
    score: float


class MemoryRetriever:
    """Retrieve memories relevant to the current request."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]+", text.lower())
            if len(token) > 2
        }

    def _score(self, query: str, memory: MemoryRecord) -> float:
        query_tokens = self._tokens(query)

        if not query_tokens:
            return 0.0

        memory_text = " ".join(
            [
                memory.content,
                " ".join(memory.tags),
                memory.memory_type.value,
            ]
        )

        memory_tokens = self._tokens(memory_text)

        overlap = query_tokens & memory_tokens
        lexical_score = len(overlap) / len(query_tokens)

        importance_bonus = memory.importance * 0.20
        confidence_bonus = memory.confidence * 0.10

        return lexical_score + importance_bonus + confidence_bonus

    def retrieve(
        self,
        query: str,
        limit: int = 5,
        minimum_score: float = 0.15,
    ) -> list[RetrievedMemory]:
        memories = self.store.list_active(limit=1000)

        scored = [
            RetrievedMemory(
                memory=memory,
                score=self._score(query, memory),
            )
            for memory in memories
        ]

        relevant = [
            result
            for result in scored
            if result.score >= minimum_score
        ]

        relevant.sort(
            key=lambda result: result.score,
            reverse=True,
        )

        return relevant[:limit]