from __future__ import annotations

from typing import Any

from chief.intelligence.evidence import ReconEvidenceService
from chief.tools.base import Tool, ToolDefinition, ToolResult, ToolRisk


class ReconEvidenceTool(Tool):
    """Governed read-only search and page evidence capability for RECON."""

    def __init__(self, service: ReconEvidenceService) -> None:
        self.service = service
        self._definition = ToolDefinition(
            name="recon.evidence",
            description=(
                "Read-only web evidence collection. Searches when CHIEF_BRAVE_SEARCH_API_KEY is "
                "configured and can always inspect explicitly supplied public seed URLs."
            ),
            risk=ToolRisk.SAFE,
            requires_approval=False,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 600},
                    "seed_urls": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 4000},
                        "maxItems": 8,
                    },
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                    "freshness": {
                        "type": ["string", "null"],
                        "enum": ["pd", "pw", "pm", "py", None],
                    },
                },
                "additionalProperties": False,
            },
            side_effects=False,
            idempotent=True,
            timeout_seconds=120,
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def validate(self, arguments: dict[str, Any]) -> None:
        super().validate(arguments)
        unknown = set(arguments) - {"query", "seed_urls", "max_results", "freshness"}
        if unknown:
            raise ValueError(f"Unsupported RECON evidence arguments: {sorted(unknown)}")
        query = arguments.get("query", "")
        seed_urls = arguments.get("seed_urls", [])
        max_results = arguments.get("max_results", 5)
        freshness = arguments.get("freshness")
        if not isinstance(query, str):
            raise TypeError("query must be a string.")
        if not isinstance(seed_urls, list) or any(not isinstance(item, str) for item in seed_urls):
            raise TypeError("seed_urls must be a list of strings.")
        if len(seed_urls) > 8:
            raise ValueError("seed_urls cannot contain more than 8 URLs.")
        if not isinstance(max_results, int) or isinstance(max_results, bool):
            raise TypeError("max_results must be an integer.")
        if not 1 <= max_results <= 10:
            raise ValueError("max_results must be between 1 and 10.")
        if freshness is not None and freshness not in {"pd", "pw", "pm", "py"}:
            raise ValueError("freshness must be pd, pw, pm, py, or null.")
        if not query.strip() and not any(item.strip() for item in seed_urls):
            raise ValueError("A search query or seed URL is required.")

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        bundle = self.service.gather(
            query=arguments.get("query", ""),
            seed_urls=arguments.get("seed_urls", []),
            max_results=arguments.get("max_results", 5),
            freshness=arguments.get("freshness"),
        )
        return ToolResult(
            success=True,
            content=(
                f"Collected {len(bundle.pages)} read-only evidence page(s)"
                + (
                    f" using {bundle.search_provider} search."
                    if bundle.search_provider and bundle.search_available
                    else "."
                )
            ),
            data=bundle.model_dump(mode="json"),
        )
