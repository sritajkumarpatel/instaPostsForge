"""The AI side of the workflow: five agents, each one Claude call with its own prompt.

Swap in another provider by implementing AIWorkflow and registering it in build_workflow.
"""

from __future__ import annotations

from ..config import ConfigError
from .base import AIWorkflow, PlanItem, Review


def build_workflow(settings: dict, usage_log=None) -> AIWorkflow:
    provider = settings.get("provider", "claude")
    if provider == "claude":
        from .claude import ClaudeWorkflow

        return ClaudeWorkflow(settings, usage_log=usage_log)
    raise ConfigError(f"Unknown AI provider '{provider}'.")


__all__ = ["AIWorkflow", "PlanItem", "Review", "build_workflow"]
