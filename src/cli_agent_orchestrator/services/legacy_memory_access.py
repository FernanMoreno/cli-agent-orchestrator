"""Adapters that apply the common policy to legacy memory owners."""

import inspect
from functools import wraps

_TARGET_FIELDS = frozenset(
    {
        "scope",
        "scope_id",
        "key",
        "terminal_id",
        "source_key",
        "target_key",
        "id",
        "target_scope",
        "fmt",
        "format",
        "source_keys",
        "keys",
        "type",
        "origin",
        "status",
    }
)


def audited_legacy(action, *, owner=None):
    """Audit before entering an operation and before returning its result.

    Only identifiers participate in the target hash; content, prompts, credentials
    and exception diagnostics are never passed to the audit store.
    """

    def decorate(function):
        signature = inspect.signature(function)

        def operation(instance, args, kwargs):
            service = getattr(instance, owner) if owner else instance
            values = signature.bind(instance, *args, **kwargs).arguments
            target = {name: value for name, value in values.items() if name in _TARGET_FIELDS}
            filters = values.get("filters", {})
            target.update(
                {name: value for name, value in filters.items() if name in _TARGET_FIELDS}
            )
            context = values.get("terminal_context") or {}
            if isinstance(context, dict):
                target["context"] = {
                    name: context[name]
                    for name in ("terminal_id", "session_name", "agent_profile", "cwd")
                    if name in context
                }
            target["operation"] = function.__name__
            return service._legacy_operation(action, target)

        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def asynchronous(instance, *args, **kwargs):
                with operation(instance, args, kwargs):
                    return await function(instance, *args, **kwargs)

            return asynchronous

        @wraps(function)
        def synchronous(instance, *args, **kwargs):
            with operation(instance, args, kwargs):
                return function(instance, *args, **kwargs)

        return synchronous

    return decorate


def propagate_legacy_policy_failure(error):
    """Optional enrichments may degrade; authority/audit failures may not."""
    from cli_agent_orchestrator.services.knowledge_policy import (
        KnowledgeAccessDenied,
        LegacyMemoryAuditError,
    )

    if isinstance(error, (KnowledgeAccessDenied, LegacyMemoryAuditError)):
        raise error
