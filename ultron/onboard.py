import os
import json
import hashlib
import tempfile
import shutil
from typing import Dict, Any, List, Optional
from rich.console import Console
from rich.panel import Panel

class ProjectMemoryManager:
    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.realpath(os.path.abspath(workspace_root))
        path_hash = hashlib.md5(self.workspace_root.encode("utf-8")).hexdigest()
        
        self.global_dir = os.path.join(
            os.path.expanduser("~"), ".ultron", "workspaces", path_hash
        )
        os.makedirs(self.global_dir, exist_ok=True)
        if os.name != 'nt':
            os.chmod(self.global_dir, 0o700)
            
        self.memory_path = os.path.join(self.global_dir, "project_memory.json")
        self.tasks_path = os.path.join(self.global_dir, "tasks.json")

    def load_memory(self) -> Dict[str, Any]:
        """Loads project memory from the global workspace cache."""
        if os.path.isfile(self.memory_path):
            try:
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
                
        # Return default structure
        return {
            "project_type": "Generic",
            "package_manager": "None",
            "entry_points": [],
            "commands": {
                "build": {"cmd": "", "status": "unverified"},
                "test": {"cmd": "", "status": "unverified"},
                "lint": {"cmd": "", "status": "unverified"},
                "format": {"cmd": "", "status": "unverified"},
                "run": {"cmd": "", "status": "unverified"}
            }
        }

    def save_memory(self, data: Dict[str, Any]):
        """Saves project memory atomically."""
        temp_fd, temp_path = tempfile.mkstemp(dir=self.global_dir)
        try:
            with open(temp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.chmod(temp_path, 0o600)
            shutil.move(temp_path, self.memory_path)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e

    def load_tasks(self) -> List[Dict[str, Any]]:
        """Loads checklist tasks from global session cache."""
        if os.path.isfile(self.tasks_path):
            try:
                with open(self.tasks_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def save_tasks(self, tasks: List[Dict[str, Any]]):
        """Saves tasks checklist to global cache."""
        temp_fd, temp_path = tempfile.mkstemp(dir=self.global_dir)
        try:
            with open(temp_fd, "w", encoding="utf-8") as f:
                json.dump(tasks, f, indent=2)
            os.chmod(temp_path, 0o600)
            shutil.move(temp_path, self.tasks_path)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e

    def run_deterministic_scan(self) -> Dict[str, Any]:
        """Scans workspace configuration signatures and constructs initial project facts."""
        facts = {
            "project_type": "Generic",
            "package_manager": "None",
            "entry_points": [],
            "commands": {
                "build": {"cmd": "", "status": "unverified"},
                "test": {"cmd": "", "status": "unverified"},
                "lint": {"cmd": "", "status": "unverified"},
                "format": {"cmd": "", "status": "unverified"},
                "run": {"cmd": "", "status": "unverified"}
            }
        }
        
        # 1. NodeJS Check
        if os.path.isfile(os.path.join(self.workspace_root, "package.json")):
            facts["project_type"] = "NodeJS / TypeScript"
            facts["package_manager"] = "npm"
            facts["commands"]["test"]["cmd"] = "npm test"
            facts["commands"]["build"]["cmd"] = "npm run build"
            facts["commands"]["lint"]["cmd"] = "npm run lint"
            facts["commands"]["run"]["cmd"] = "npm start"
            if os.path.isfile(os.path.join(self.workspace_root, "yarn.lock")):
                facts["package_manager"] = "yarn"
                facts["commands"]["test"]["cmd"] = "yarn test"
                facts["commands"]["build"]["cmd"] = "yarn build"
                facts["commands"]["lint"]["cmd"] = "yarn lint"
                facts["commands"]["run"]["cmd"] = "yarn start"
            elif os.path.isfile(os.path.join(self.workspace_root, "pnpm-lock.yaml")):
                facts["package_manager"] = "pnpm"
                facts["commands"]["test"]["cmd"] = "pnpm test"
                facts["commands"]["build"]["cmd"] = "pnpm build"
                facts["commands"]["lint"]["cmd"] = "pnpm lint"
                facts["commands"]["run"]["cmd"] = "pnpm start"
                
        # 2. Python Check
        elif (os.path.isfile(os.path.join(self.workspace_root, "requirements.txt")) or 
              os.path.isfile(os.path.join(self.workspace_root, "pyproject.toml")) or
              os.path.isfile(os.path.join(self.workspace_root, "setup.py"))):
            facts["project_type"] = "Python"
            facts["package_manager"] = "pip"
            # Choose pytest if python test folders exist, else unittest
            if os.path.isdir(os.path.join(self.workspace_root, "tests")):
                facts["commands"]["test"]["cmd"] = "pytest"
            else:
                facts["commands"]["test"]["cmd"] = "python -m unittest discover"
            facts["commands"]["lint"]["cmd"] = "flake8 . --exclude=.venv,venv"
            facts["commands"]["format"]["cmd"] = "black ."
            
        # 3. Rust Check
        elif os.path.isfile(os.path.join(self.workspace_root, "Cargo.toml")):
            facts["project_type"] = "Rust"
            facts["package_manager"] = "cargo"
            facts["commands"]["build"]["cmd"] = "cargo build"
            facts["commands"]["test"]["cmd"] = "cargo test"
            facts["commands"]["lint"]["cmd"] = "cargo clippy"
            facts["commands"]["format"]["cmd"] = "cargo fmt"
            facts["commands"]["run"]["cmd"] = "cargo run"
            
        # 4. Go Check
        elif os.path.isfile(os.path.join(self.workspace_root, "go.mod")):
            facts["project_type"] = "Go"
            facts["package_manager"] = "go"
            facts["commands"]["build"]["cmd"] = "go build ."
            facts["commands"]["test"]["cmd"] = "go test ./..."
            facts["commands"]["lint"]["cmd"] = "go vet ./..."
            facts["commands"]["format"]["cmd"] = "go fmt ./..."
            
        # 5. Makefile Fallback
        elif os.path.isfile(os.path.join(self.workspace_root, "Makefile")):
            facts["project_type"] = "C / C++ (Makefile)"
            facts["commands"]["build"]["cmd"] = "make"
            facts["commands"]["test"]["cmd"] = "make test"
            
        return facts

    def onboard(self, agent, console: Console):
        """
        Runs project discovery, updates metadata, and prompts local model for structural summary.
        If Ollama is offline, falls back gracefully to deterministic scanners.
        """
        console.print("[cyan]Running deterministic workspace scans...[/cyan]")
        facts = self.run_deterministic_scan()
        
        # Load any existing memory to avoid erasing already verified statuses
        existing = self.load_memory()
        for action, block in facts["commands"].items():
            ex_cmd = existing.get("commands", {}).get(action, {})
            if ex_cmd.get("cmd") == block["cmd"] and ex_cmd.get("status") == "verified":
                block["status"] = "verified"
                
        # If Ollama is available, ask it to summarize structure details
        is_online = agent.model.is_available()
        inferred_text = ""
        
        if is_online:
            console.print("[cyan]Analyzing project layout using local Ollama model...[/cyan]")
            # Build list of key files we found to send to Ollama
            manifest_files = [f for f in ["package.json", "Cargo.toml", "go.mod", "requirements.txt", "pyproject.toml", "setup.py", "Makefile", "CMakeLists.txt", "README.md"] 
                              if os.path.isfile(os.path.join(self.workspace_root, f))]
            
            # Read first 40 lines of README if it exists
            readme_sample = ""
            readme_path = os.path.join(self.workspace_root, "README.md")
            if os.path.isfile(readme_path):
                try:
                    with open(readme_path, "r", encoding="utf-8", errors="replace") as f:
                        readme_sample = "\n".join(f.read().splitlines()[:40])
                except Exception:
                    pass
            
            prompt = (
                f"You are a project onboarding assistant. The workspace contains these manifests: {', '.join(manifest_files)}.\n"
                f"We scanned and identified the project type as '{facts['project_type']}'.\n"
                f"Here is a snippet of README.md if present:\n{readme_sample}\n\n"
                f"Provide a brief, high-level structural explanation of this project (folders, entry points, design style). "
                f"Keep it under 1,500 characters. Address the developer directly."
            )
            
            try:
                chat_generator = agent.model.chat([{"role": "user", "content": prompt}], stream=True)
                while True:
                    try:
                        chunk = next(chat_generator)
                        if chunk["type"] == "content":
                            inferred_text += chunk["delta"]
                    except StopIteration:
                        break
            except Exception:
                inferred_text = "Failed to communicate with local Ollama model for architectural summary."
        else:
            console.print("[yellow]Ollama offline: Bypassing AI analysis, falling back to deterministic scanner.[/yellow]")
            inferred_text = "Ollama is currently offline. Architectural analysis bypassed."
            
        facts["architectural_summary"] = inferred_text.strip()
        self.save_memory(facts)
        
        # Display Onboarding outcome
        console.print("\n[bold green]* Onboarding scan complete![/bold green]")
        console.print(Panel(
            f"[bold white]Project Type:[/bold white] {facts['project_type']}\n"
            f"[bold white]Package Manager:[/bold white] {facts['package_manager']}\n"
            f"[bold white]Suggested Test Command:[/bold white] {facts['commands']['test']['cmd'] or 'None'}\n"
            f"[bold white]Suggested Build Command:[/bold white] {facts['commands']['build']['cmd'] or 'None'}\n\n"
            f"[bold cyan]--- AI Inferred Layout Summary ---[/bold cyan]\n{facts['architectural_summary']}",
            title="[bold green]Project Onboard Summary[/bold green]",
            border_style="green"
        ))
