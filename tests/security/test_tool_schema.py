"""
test_tool_schema.py - Security tests for strict tool JSON schemas (additionalProperties: False).
"""
import pytest
from ultron.tool_registry import ToolRegistry


def test_tool_registry_schemas_strict():
    registry = ToolRegistry.build_default()
    schemas = registry.get_json_schemas()
    
    assert len(schemas) >= 7
    for tool_json in schemas:
        fn = tool_json["function"]
        params = fn["parameters"]
        # Must enforce strict additionalProperties: False
        assert params.get("additionalProperties") is False, f"Tool '{fn['name']}' missing additionalProperties: False"
