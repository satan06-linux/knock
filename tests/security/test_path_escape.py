"""
test_path_escape.py - Security tests for workspace containment, symlink safety, and path traversal.
"""
import pytest
import os
from ultron.security import validate_path


def test_valid_workspace_path(tmp_path):
    root = str(tmp_path)
    file_path = os.path.join(root, "valid.txt")
    with open(file_path, "w") as f:
        f.write("hello")
    
    resolved = validate_path("valid.txt", root)
    assert resolved == os.path.realpath(file_path)


def test_path_traversal_escape(tmp_path):
    root = str(tmp_path)
    with pytest.raises(PermissionError):
        validate_path("../../etc/passwd", root)


def test_sensitive_file_deny_list(tmp_path):
    root = str(tmp_path)
    env_file = os.path.join(root, ".env")
    with open(env_file, "w") as f:
        f.write("SECRET=123")

    with pytest.raises(PermissionError):
        validate_path(".env", root)
