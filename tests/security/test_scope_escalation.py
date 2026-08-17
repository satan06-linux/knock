"""
test_scope_escalation.py - Security tests for ScopeManager dynamic scope boundaries.
"""
import pytest
from ultron.scope_manager import ScopeManager, ScopeDecisionResult


def test_scope_manager_initial_scope(tmp_path):
    sm = ScopeManager(str(tmp_path))
    sm.set_initial_scope(["src/app.py"])

    # Initial file -> ALLOW
    d1 = sm.evaluate("src/app.py", "MODIFY")
    assert d1.decision == ScopeDecisionResult.ALLOW

    # Unrelated file without evidence -> ASK / BLOCK
    d2 = sm.evaluate("secrets/db.py", "MODIFY")
    assert d2.decision in (ScopeDecisionResult.ASK, ScopeDecisionResult.BLOCK)
