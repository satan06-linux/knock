"""
test_command_security.py - Adversarial Command Security & ResourceGuard tests.
"""
import pytest
import os
from ultron.command_security import CommandSecurityLayer, CommandCapability
from ultron.scope_manager import ScopeDecisionResult
from ultron.resource_guard import ResourceGuard, ResourceLimits, ResourceExceededError


def test_command_security_classification(tmp_path):
    csl = CommandSecurityLayer(str(tmp_path))

    # Read command
    d1 = csl.evaluate("cat README.md", is_interactive=True)
    assert d1.capability == CommandCapability.READ_COMMAND
    assert d1.decision == ScopeDecisionResult.ALLOW

    # Package command
    d2 = csl.evaluate("pip install requests", is_interactive=True)
    assert d2.capability == CommandCapability.PACKAGE_COMMAND
    assert d2.requires_explicit_approval is True

    # Destructive command
    d3 = csl.evaluate("rm -rf /", is_interactive=True)
    assert d3.capability == CommandCapability.DESTRUCTIVE_COMMAND
    assert d3.requires_explicit_approval is True

    # Privileged command -> BLOCK
    d4 = csl.evaluate("sudo rm -rf /", is_interactive=True)
    assert d4.capability == CommandCapability.PRIVILEGED_COMMAND
    assert d4.decision == ScopeDecisionResult.BLOCK


def test_command_security_unknown_fail_closed(tmp_path):
    csl = CommandSecurityLayer(str(tmp_path))

    # Unknown command in non-interactive mode -> BLOCK
    d1 = csl.evaluate("custom_internal_tool_xyz --flag", is_interactive=False)
    assert d1.capability == CommandCapability.UNKNOWN_COMMAND
    assert d1.decision == ScopeDecisionResult.BLOCK

    # Unknown command in interactive mode -> ASK
    d2 = csl.evaluate("custom_internal_tool_xyz --flag", is_interactive=True)
    assert d2.capability == CommandCapability.UNKNOWN_COMMAND
    assert d2.decision == ScopeDecisionResult.ASK


def test_command_security_path_escape(tmp_path):
    csl = CommandSecurityLayer(str(tmp_path))

    # Command with path traversal escape -> BLOCK
    d = csl.evaluate("cat ../../etc/passwd", is_interactive=True)
    assert d.has_filesystem_escape is True
    assert d.decision == ScopeDecisionResult.BLOCK


def test_resource_guard_limits():
    limits = ResourceLimits(max_tool_calls=2, max_output_bytes=50)
    guard = ResourceGuard(limits)

    # Tool call count check
    guard.check_tool_call()
    guard.check_tool_call()
    with pytest.raises(ResourceExceededError):
        guard.check_tool_call()

    # Truncation check
    long_output = "A" * 100
    truncated = guard.truncate_output(long_output)
    assert "Truncated by ResourceGuard" in truncated
    assert len(truncated) < len(long_output) + 200
