"""The AI side of the workflow: five agents, each one model call with its own prompt.

The prompts, schemas and checks are shared (workflow.py); each provider only supplies a small
ModelClient (clients/). Add a provider by writing a client and registering it in build_client.
"""

from __future__ import annotations

from .base import AIWorkflow, PlanItem, Review, SetPhoto


def build_workflow(settings: dict, usage_log=None) -> AIWorkflow:
    from .clients import build_client
    from .workflow import AgentWorkflow

    return AgentWorkflow(settings, client=build_client(settings.get("provider", "claude")), usage_log=usage_log)


__all__ = ["AIWorkflow", "PlanItem", "Review", "SetPhoto", "build_workflow"]
