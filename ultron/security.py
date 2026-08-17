import os
import fnmatch
from typing import List, Tuple

DENY_PATTERNS = [
    ".git/*",
    ".git",
    ".env",
    ".env.*",
    "*credential*"
]

def is_denied(rel_path: str) -> bool:
    """Check if the relative path matches any sensitive file patterns."""
    # Normalize separators to forward slashes for matching
    rel_path_norm = rel_path.replace(os.sep, "/").lower()
    
    # Split into parts to check segments
    parts = rel_path_norm.split("/")
    
    # 1. Reject any path containing .git directory (e.g. .git/config)
    if ".git" in parts:
        return True
        
    # 2. Reject env and credentials pattern matching
    for pattern in DENY_PATTERNS:
        pattern_lower = pattern.lower()
        # Match against full relative path
        if fnmatch.fnmatch(rel_path_norm, pattern_lower):
            return True
        # Match against individual path parts (e.g. credentials inside a folder)
        for part in parts:
            if fnmatch.fnmatch(part, pattern_lower):
                return True
                
    return False

def validate_path(path: str, workspace_root: str) -> str:
    """
    Safely resolves target path relative to workspace_root.
    Protects against directory traversal, symlinks/junction escapes, Windows case sensitivity, and multi-drive escapes.
    Returns resolved absolute path.
    """
    workspace_abs = os.path.realpath(os.path.abspath(workspace_root))
    
    # If path is empty, return workspace root
    if not path:
        return workspace_abs
        
    # Resolve target path absolutely and resolve symlinks/junctions
    if os.path.isabs(path):
        resolved = os.path.realpath(os.path.abspath(path))
    else:
        resolved = os.path.realpath(os.path.abspath(os.path.join(workspace_abs, path)))
        
    # Drive/Containment Check
    try:
        # On Windows, os.path.commonpath raises ValueError if on different drives
        common = os.path.commonpath([workspace_abs, resolved])
    except ValueError:
        raise PermissionError("Access Denied: Target path resides on a different drive.")
        
    # Normalise case for Windows drive/prefix comparison
    is_windows = os.name == 'nt'
    workspace_check = workspace_abs.lower() if is_windows else workspace_abs
    common_check = common.lower() if is_windows else common
    
    # Must be subpath or identical to workspace root
    if common_check != workspace_check:
        raise PermissionError(f"Access Denied: Path '{path}' resolves outside the workspace.")
        
    # Calculate relative path from workspace root
    rel_path = os.path.relpath(resolved, workspace_abs)
    
    # Check sensitive files deny list (skip root folder itself)
    if rel_path != "." and is_denied(rel_path):
        raise PermissionError(f"Access Denied: Path '{rel_path}' is matched by the sensitive-file deny list.")
        
    return resolved

def is_side_effect_command(cmd: str, workspace_root: str = ".") -> Tuple[bool, str]:
    """Checks if a command targets dependency install, migrations, servers, network, or destruction using CommandSecurityLayer."""
    from ultron.command_security import CommandSecurityLayer, CommandCapability
    
    csl = CommandSecurityLayer(workspace_root)
    decision = csl.evaluate(cmd, is_interactive=True)
    
    if decision.capability == CommandCapability.DESTRUCTIVE_COMMAND:
        return True, "destructive filesystem operation"
    if decision.capability == CommandCapability.PACKAGE_COMMAND:
        return True, "dependency installation"
    if decision.capability == CommandCapability.NETWORK_COMMAND:
        return True, "network/remote connectivity"
    if decision.capability == CommandCapability.PRIVILEGED_COMMAND:
        return True, "privileged execution"
    if decision.capability == CommandCapability.UNKNOWN_COMMAND:
        return True, "unrecognized shell command"

    cmd_lower = cmd.lower()
    if any(k in cmd_lower for k in ["migrate", "alembic upgrade", "db push", "db seed"]):
        return True, "database migration/mutation"
    if any(k in cmd_lower for k in ["run dev", "npm start", "yarn start", "pnpm start", "python app.py", "python main.py", "cargo run", "go run"]):
        return True, "server/application execution"

    return decision.requires_explicit_approval, decision.reason
