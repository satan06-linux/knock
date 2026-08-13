import os
import shutil
import hashlib
import json
import tempfile
from typing import Dict, Any, Optional, List
from rich.console import Console
from rich.prompt import Confirm
from rich.panel import Panel
from rich.text import Text

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

    def record_before_edit(self, rel_path: str):
        """
        Record the pre-edit state of a file if it hasn't been recorded in this task yet.
        """
        norm_rel = rel_path.replace(os.sep, "/")
        if norm_rel in self.current_task_files:
            return # Already recorded the initial state for this task
            
        abs_path = os.path.join(self.workspace_root, rel_path)
        existed = os.path.isfile(abs_path)
        
        backup_id = hashlib.md5(norm_rel.encode("utf-8")).hexdigest()
        backup_file = os.path.join(self.checkpoint_dir, f"backup_{backup_id}.tmp")
        
        original_content = None
        mode = None
        
        if existed:
            mode = os.stat(abs_path).st_mode
            with open(abs_path, "rb") as f:
                original_content = f.read()
                
            # Write backup file atomically with restricted permissions
            temp_fd, temp_path = tempfile.mkstemp(dir=self.checkpoint_dir)
            try:
                os.write(temp_fd, original_content)
                os.close(temp_fd)
                # Restrict permissions
                os.chmod(temp_path, 0o600)
                shutil.move(temp_path, backup_file)
            except Exception as e:
                # Cleanup if failed
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise e
        
        self.current_task_files[norm_rel] = {
            "existed": existed,
            "backup_file": backup_file if existed else None,
            "original_mode": mode,
            "post_edit_hash": None # Will be filled after successful edit
        }

    def record_after_edit(self, rel_path: str):
        """
        Record the SHA-256 hash of a file after it has been edited by Ultron.
        """
        norm_rel = rel_path.replace(os.sep, "/")
        if norm_rel not in self.current_task_files:
            return
            
        abs_path = os.path.join(self.workspace_root, rel_path)
        if os.path.isfile(abs_path):
            self.current_task_files[norm_rel]["post_edit_hash"] = self._get_sha256(abs_path)
        else:
            # File was deleted (e.g. by a tool, though rare)
            self.current_task_files[norm_rel]["post_edit_hash"] = None

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

    def undo(self, console: Console) -> bool:
        """
        Performs the undo operation. Restores modified files and deletes created files.
        Checks post-edit hashes to prevent overwriting user modifications.
        """
        checkpoint = self.get_latest_checkpoint()
        if not checkpoint:
            console.print("[yellow]No checkpoints available to undo.[/yellow]")
            return False
            
        console.print("[bold cyan]Reverting latest Ultron task changes...[/bold cyan]")
        
        undone_files = []
        failed_files = []
        
        for rel_path, meta in checkpoint.items():
            abs_path = os.path.join(self.workspace_root, rel_path)
            existed_before = meta["existed"]
            backup_file = meta["backup_file"]
            post_edit_hash = meta["post_edit_hash"]
            
            # Check current status of the file
            current_exists = os.path.isfile(abs_path)
            
            # Conflict Check: Does it match the post-edit state of Ultron?
            is_conflict = False
            if current_exists:
                current_hash = self._get_sha256(abs_path)
                if current_hash != post_edit_hash:
                    is_conflict = True
            else:
                # File was deleted by user in the meantime
                if post_edit_hash is not None:
                    is_conflict = True
                    
            if is_conflict:
                console.print(f"\n[bold red]Conflict Detected:[/bold red] File '{rel_path}' was modified by you after Ultron's edits.")
                
                # Show unified diff between Ultron's version (post-edit) and current version
                ultron_version = ""
                if post_edit_hash is not None and backup_file and os.path.isfile(backup_file):
                    # Try to reconstruct the edit? Actually, displaying that a conflict exists is safer.
                    pass
                
                # Prompt user for action
                force = Confirm.ask(f"[bold yellow]Force revert '{rel_path}' anyway (overwriting your changes)?[/bold yellow]")
                if not force:
                    console.print(f"[yellow]Skipped reverting '{rel_path}'.[/yellow]")
                    failed_files.append(rel_path)
                    continue
            
            # Perform restoration
            try:
                if existed_before:
                    # Restore original file
                    if not backup_file or not os.path.isfile(backup_file):
                        console.print(f"[red]Error: Backup file for '{rel_path}' is missing. Cannot restore.[/red]")
                        failed_files.append(rel_path)
                        continue
                        
                    # Atomic write
                    dir_name = os.path.dirname(abs_path)
                    os.makedirs(dir_name, exist_ok=True)
                    temp_fd, temp_path = tempfile.mkstemp(dir=dir_name)
                    try:
                        with open(backup_file, "rb") as f_backup:
                            os.write(temp_fd, f_backup.read())
                        os.close(temp_fd)
                        shutil.move(temp_path, abs_path)
                        # Restore file mode
                        if meta.get("original_mode") is not None:
                            os.chmod(abs_path, meta["original_mode"])
                    except Exception as e:
                        if os.path.exists(temp_path):
                            os.remove(temp_path)
                        raise e
                    console.print(f"[green]* Restored '{rel_path}' to pre-task state.[/green]")
                else:
                    # File was newly created by Ultron, delete it
                    if os.path.isfile(abs_path):
                        os.remove(abs_path)
                    console.print(f"[green]* Deleted created file '{rel_path}'.[/green]")
                undone_files.append(rel_path)
            except Exception as e:
                console.print(f"[red]Failed to revert '{rel_path}': {str(e)}[/red]")
                failed_files.append(rel_path)
                
        # Remove metadata file since it is applied
        if os.path.isfile(self.metadata_path):
            os.remove(self.metadata_path)
            
        # Clean up backup tmp files
        for rel_path, meta in checkpoint.items():
            backup_file = meta.get("backup_file")
            if backup_file and os.path.isfile(backup_file):
                try:
                    os.remove(backup_file)
                except Exception:
                    pass
                    
        if undone_files:
            console.print("\n[bold green]✓ Revert complete.[/bold green]")
        if failed_files:
            console.print(f"[yellow]Note: {len(failed_files)} files could not be automatically reverted due to user decisions.[/yellow]")
            
        return len(failed_files) == 0
