"""
resource_guard.py - Execution Budget and Resource Limits Enforcer.

Enforces execution budgets outside the AI model's control:
- max_process_time (seconds)
- max_output_bytes
- max_file_size
- max_files_created
- max_total_workspace_delta
- max_tool_calls
"""
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ResourceLimits:
    """Enforceable task budget limits."""
    max_process_time: int = 180              # seconds per subprocess execution
    max_output_bytes: int = 100 * 1024       # 100 KB max output capture per tool
    max_file_size: int = 5 * 1024 * 1024     # 5 MB max per created file
    max_files_created: int = 50              # max new files per task
    max_workspace_delta_bytes: int = 10 * 1024 * 1024 # 10 MB total workspace delta
    max_tool_calls: int = 40                 # max tool calls per task session


class ResourceExceededError(PermissionError):
    """Raised when a task exceeds its allocated ResourceLimits budget."""
    pass


class ResourceGuard:
    """
    Enforces resource budgets programmatically outside model control.
    The model cannot modify its own budget limits.
    """

    def __init__(self, limits: Optional[ResourceLimits] = None):
        self.limits = limits or ResourceLimits()
        self.reset()

    def reset(self):
        """Reset usage metrics for a new task session."""
        self.tool_call_count: int = 0
        self.files_created_count: int = 0
        self.total_bytes_written: int = 0

    def check_tool_call(self):
        """Verify task tool-call count budget."""
        self.tool_call_count += 1
        if self.tool_call_count > self.limits.max_tool_calls:
            raise ResourceExceededError(
                f"Task budget exceeded: maximum allowed tool calls ({self.limits.max_tool_calls}) reached."
            )

    def check_file_creation(self, content_length: int):
        """Verify single file size and total file creation count budgets."""
        if content_length > self.limits.max_file_size:
            raise ResourceExceededError(
                f"Resource budget exceeded: requested file size ({content_length} bytes) "
                f"exceeds maximum allowed per-file limit ({self.limits.max_file_size} bytes)."
            )

        if self.files_created_count + 1 > self.limits.max_files_created:
            raise ResourceExceededError(
                f"Resource budget exceeded: maximum allowed created files ({self.limits.max_files_created}) reached."
            )

        if self.total_bytes_written + content_length > self.limits.max_workspace_delta_bytes:
            raise ResourceExceededError(
                f"Resource budget exceeded: total workspace delta bytes limit ({self.limits.max_workspace_delta_bytes}) reached."
            )

    def record_file_creation(self, content_length: int):
        """Record successful file creation in usage state."""
        self.files_created_count += 1
        self.total_bytes_written += content_length

    def get_process_timeout(self) -> int:
        """Return maximum process execution timeout in seconds."""
        return self.limits.max_process_time

    def check_workspace_delta(self, additional_bytes: int):
        """Check if adding additional_bytes exceeds total workspace delta limit."""
        if self.total_bytes_written + additional_bytes > self.limits.max_workspace_delta_bytes:
            raise ResourceExceededError(
                f"Resource budget exceeded: total workspace delta bytes limit "
                f"({self.limits.max_workspace_delta_bytes} bytes) reached."
            )

    def record_workspace_delta(self, delta_bytes: int):
        """Record workspace delta bytes in usage state."""
        self.total_bytes_written += max(0, delta_bytes)

    def truncate_output(self, output: str) -> str:
        """Truncate command or file read output if it exceeds max_output_bytes."""
        if not output:
            return ""

        output_bytes = output.encode("utf-8", errors="replace")
        if len(output_bytes) <= self.limits.max_output_bytes:
            return output

        truncated_bytes = output_bytes[:self.limits.max_output_bytes]
        truncated_str = truncated_bytes.decode("utf-8", errors="ignore")
        return (
            f"{truncated_str}\n\n"
            f"[... Truncated by ResourceGuard: output exceeded {self.limits.max_output_bytes} bytes limit ...]"
        )
