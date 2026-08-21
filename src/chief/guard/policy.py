from dataclasses import dataclass
from enum import Enum

from chief.tools.base import ToolDefinition, ToolRisk


class PolicyDecision(str, Enum):
    """Decision returned by CHIEF's tool permission policy."""

    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyResult:
    """Permission decision with a human-readable reason."""

    decision: PolicyDecision
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == PolicyDecision.ALLOW


@dataclass(frozen=True)
class ToolPolicy:
    """Default safety policy for CHIEF tool execution.

    Safe tools run automatically. Controlled tools require approval unless
    explicitly enabled. Sensitive tools always require explicit approval.
    """

    allow_controlled: bool = False

    def evaluate(
        self,
        definition: ToolDefinition,
        *,
        approved: bool = False,
    ) -> PolicyResult:
        if definition.risk == ToolRisk.SAFE and not definition.requires_approval:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="Safe tool is permitted automatically.",
            )

        if definition.risk == ToolRisk.CONTROLLED:
            if approved or self.allow_controlled:
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason="Controlled tool execution is permitted.",
                )

            return PolicyResult(
                decision=PolicyDecision.REQUIRE_APPROVAL,
                reason="Controlled tool requires approval under current policy.",
            )

        if definition.risk == ToolRisk.SENSITIVE or definition.requires_approval:
            if approved:
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    reason="Sensitive tool received explicit approval.",
                )

            return PolicyResult(
                decision=PolicyDecision.REQUIRE_APPROVAL,
                reason="Sensitive tool requires explicit user approval.",
            )

        return PolicyResult(
            decision=PolicyDecision.DENY,
            reason="Tool risk classification is not permitted by policy.",
        )
