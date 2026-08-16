import os
import uuid
import shutil
import hashlib
import json
import tempfile
from datetime import datetime
from typing import Dict, Any, Optional, List
from rich.console import Console
from rich.prompt import Confirm
from rich.panel import Panel
from rich.text import Text


# Operation states (P0.7)
OP_PLANNED     = "PLANNED"
OP_APPROVED    = "APPROVED"
OP_STARTED     = "STARTED"
OP_COMPLETED   = "COMPLETED"
OP_FAILED      = "FAILED"
OP_ROLLED_BACK = "ROLLED_BACK"
OP_CONFLICTED  = "CONFLICTED"


class CheckpointManager:
    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.realpath(os.path.abspath(workspace_root))
        
        # Unique project ID based on path hash
        path_hash = hashlib.md5(self.workspace_root.encode("utf-8")).hexdigest()
        self.checkpoint_dir = os.path.join(
            os.path.expanduser("~"), ".ultron", "checkpoints", path_hash
        )
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        # Restrict permissions of checkpoints directory (owner only)
        if os.name != 'nt':
            os.chmod(self.checkpoint_dir, 0o700)
            
        self.current_task_files: Dict[str, Dict[str, Any]] = {}
        self.metadata_path = os.path.join(self.checkpoint_dir, "metadata.json")

        # P0.7: Transaction/operation log
        self._task_id: str = ""
        self._transaction_id: str = ""
        self._operation_log: List[Dict[str, Any]] = []

    def _get_sha256(self, file_path: str) -> str:
        """Calculate the SHA-256 hash of a file's contents."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _get_string_sha256(self, content: str) -> str:
        """Calculate SHA-256 hash of a string."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def start_task(self):
        """Start a new task checkpoint session, clearing previous in-memory state."""
        self.current_task_files.clear()
        self._task_id = str(uuid.uuid4())[:8]
        self._transaction_id = str(uuid.uuid4())[:8]
        self._operation_log.clear()

    def start_transaction(self) -> str:
        """Start a new transaction within the current task. Returns transaction_id."""
        self._transaction_id = str(uuid.uuid4())[:8]
        return self._transaction_id

    def _log_operation(self, rel_path: str, op_type: str, state: str,
                       before_hash: Optional[str] = None, after_hash: Optional[str] = None,
                       reason: str = ""):
        """Record an operation in the operation log."""
        self._operation_log.append({
            "operation_id": str(uuid.uuid4())[:8],
            "task_id": self._task_id,
            "transaction_id": self._transaction_id,
            "timestamp": datetime.now().isoformat(),
            "file": rel_path,
            "operation_type": op_type,
            "state": state,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "reason": reason,
        })

    def record_before_edit(self, rel_path: str):
        """
        Record the pre-edit state of a file if it hasn't been recorded in this task yet.
        """
        norm_rel = rel_path.replace(os.sep, "/")
        if norm_rel in self.current_task_files:
            return
            
        abs_path = os.path.join(self.workspace_root, rel_path)
        existed = os.path.isfile(abs_path)
        
        backup_id = hashlib.md5(norm_rel.encode("utf-8")).hexdigest()
        backup_file = os.path.join(self.checkpoint_dir, f"backup_{backup_id}.tmp")
        
        original_content = None
        mode = None
        before_hash = None
        
        if existed:
            mode = os.stat(abs_path).st_mode
            with open(abs_path, "rb") as f:
                original_content = f.read()
            before_hash = hashlib.sha256(original_content).hexdigest()
                
            temp_fd, temp_path = tempfile.mkstemp(dir=self.checkpoint_dir)
            try:
                os.write(temp_fd, original_content)
                os.close(temp_fd)
                os.chmod(temp_path, 0o600)
                shutil.move(temp_path, backup_file)
            except Exception as e:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise e
        
        self.current_task_files[norm_rel] = {
            "existed": existed,
            "backup_file": backup_file if existed else None,
            "original_mode": mode,
            "post_edit_hash": None,
        }

        # P0.7: Log operation
        self._log_operation(norm_rel, "WRITE", OP_STARTED, before_hash=before_hash)

    def record_after_edit(self, rel_path: str):
        """Record the SHA-256 hash of a file after it has been edited by Ultron."""
        norm_rel = rel_path.replace(os.sep, "/")
        if norm_rel not in self.current_task_files:
            return
            
        abs_path = os.path.join(self.workspace_root, rel_path)
        after_hash = None
        if os.path.isfile(abs_path):
            after_hash = self._get_sha256(abs_path)
        self.current_task_files[norm_rel]["post_edit_hash"] = after_hash

        # P0.7: Update operation log state
        self._log_operation(norm_rel, "WRITE", OP_COMPLETED, after_hash=after_hash)

    def save_task_checkpoint(self):
        """Save the in-memory task checkpoint to disk metadata."""
        if not self.current_task_files:
            return
            
        # Write metadata file atomically
        temp_fd, temp_path = tempfile.mkstemp(dir=self.checkpoint_dir)
        try:
            with open(temp_fd, "w", encoding="utf-8") as f:
                json.dump(self.current_task_files, f, indent=2)
            # Restrict permissions
            os.chmod(temp_path, 0o600)
            shutil.move(temp_path, self.metadata_path)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e

    def get_latest_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Load the latest saved checkpoint metadata from disk."""
        if not os.path.isfile(self.metadata_path):
            return None
        try:
            with open(self.metadata_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def get_operation_log(self) -> List[Dict[str, Any]]:
        """Return the current task's operation log (P0.7)."""
        return list(self._operation_log)

    def rollback_operation(self, operation_id: str, console: Console) -> bool:
        """Roll back a single operation by ID (P0.7)."""
        op = next((o for o in self._operation_log if o["operation_id"] == operation_id), None)
        if not op:
            console.print(f"[red]Operation {operation_id} not found.[/red]")
            return False
        rel_path = op["file"]
        return self._restore_file(rel_path, op, console)

    def _restore_file(self, rel_path: str, meta: Dict[str, Any], console: Console) -> bool:
        """Internal: restore a single file from backup. Returns True on success."""
        abs_path = os.path.join(self.workspace_root, rel_path)
        existed_before = meta.get("existed", meta.get("existed_before", False))
        backup_file = meta.get("backup_file")
        post_edit_hash = meta.get("post_edit_hash")

        # P0.7 TOCTOU protection: current hash must match post-edit hash
        if os.path.isfile(abs_path):
            current_hash = self._get_sha256(abs_path)
            if post_edit_hash and current_hash != post_edit_hash:
                console.print(f"\n[bold red]Conflict:[/bold red] '{rel_path}' was modified after Ultron's edit.")
                console.print("[dim]Current file differs from Ultron's last version.[/dim]")
                force = Confirm.ask(f"[bold yellow]Force revert '{rel_path}' (overwrites your changes)?[/bold yellow]")
                if not force:
                    self._log_operation(rel_path, "ROLLBACK", OP_CONFLICTED)
                    return False

        try:
            if existed_before:
                if not backup_file or not os.path.isfile(backup_file):
                    console.print(f"[red]Backup missing for '{rel_path}'.[/red]")
                    return False
                dir_name = os.path.dirname(abs_path)
                os.makedirs(dir_name, exist_ok=True)
                temp_fd, temp_path = tempfile.mkstemp(dir=dir_name)
                try:
                    with open(backup_file, "rb") as f_bk:
                        os.write(temp_fd, f_bk.read())
                    os.close(temp_fd)
                    shutil.move(temp_path, abs_path)
                    if meta.get("original_mode"):
                        os.chmod(abs_path, meta["original_mode"])
                except Exception as e:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise e
                console.print(f"[green]✓ Restored '{rel_path}'[/green]")
            else:
                if os.path.isfile(abs_path):
                    os.remove(abs_path)
                console.print(f"[green]✓ Deleted created file '{rel_path}'[/green]")
            self._log_operation(rel_path, "ROLLBACK", OP_ROLLED_BACK)
            return True
        except Exception as e:
            console.print(f"[red]Failed to restore '{rel_path}': {e}[/red]")
            self._log_operation(rel_path, "ROLLBACK", OP_FAILED)
            return False

    def undo(self, console: Console) -> bool:
        """Performs the undo operation using _restore_file for each changed file."""
        checkpoint = self.get_latest_checkpoint()
        if not checkpoint:
            console.print("[yellow]No checkpoints available to undo.[/yellow]")
            return False

        console.print("[bold cyan]Reverting latest Ultron task changes...[/bold cyan]")
        undone, failed = [], []

        for rel_path, meta in checkpoint.items():
            success = self._restore_file(rel_path, meta, console)
            (undone if success else failed).append(rel_path)

        # Cleanup metadata + backups
        if os.path.isfile(self.metadata_path):
            os.remove(self.metadata_path)
        for meta in checkpoint.values():
            bf = meta.get("backup_file")
            if bf and os.path.isfile(bf):
                try:
                    os.remove(bf)
                except Exception:
                    pass

        if undone:
            console.print("\n[bold green]✓ Revert complete.[/bold green]")
        if failed:
            console.print(f"[yellow]{len(failed)} file(s) could not be reverted.[/yellow]")
        return len(failed) == 0
