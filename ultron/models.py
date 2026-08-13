import json
import httpx
from typing import List, Dict, Any, Generator, Optional

class OllamaModel:
    def __init__(
        self, 
        model_name: str = "qwen2.5-coder:7b", 
        base_url: str = "http://localhost:11434",
        temperature: float = 0.0,
        num_ctx: int = 16384,
        keep_alive: str = "5m"
    ):
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.client = httpx.Client(timeout=120.0)  # Long timeout for local inference

    def is_available(self) -> bool:
        """Check if Ollama server is running and the model is loaded/available."""
        try:
            response = self.client.get(f"{self.base_url}/api/tags")
            if response.status_code == 200:
                models = [m["name"] for m in response.json().get("models", [])]
                for m in models:
                    # Exact match
                    if m == self.model_name:
                        return True
                    # If one has tag and other does not, compare base name
                    if ":" in m and ":" not in self.model_name:
                        if m.split(":")[0] == self.model_name:
                            return True
                    if ":" not in m and ":" in self.model_name:
                        if m == self.model_name.split(":")[0]:
                            return True
                return False
        except Exception:
            return False
        return False

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return the JSON schema definitions of tools supported by Ultron."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_dir",
                    "description": "List the contents of a directory (files and subdirectories) to understand the project structure.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The path to list, relative to the project root. Defaults to '.' (root)."
                            }
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "grep_search",
                    "description": "Find occurrences of a query pattern within workspace files (like grep/ripgrep).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The exact string or regex pattern to search for."
                            },
                            "path": {
                                "type": "string",
                                "description": "Optional subdirectory to restrict the search to."
                            }
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "view_file",
                    "description": "Read the content of a file. Use specific start/end lines for large files to preserve context size.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The path to the file, relative to the project root."
                            },
                            "start_line": {
                                "type": "integer",
                                "description": "Optional 1-indexed start line number."
                            },
                            "end_line": {
                                "type": "integer",
                                "description": "Optional 1-indexed end line number (inclusive)."
                            }
                        },
                        "required": ["path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Create a new file or completely overwrite an existing file with new content.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The path to write the file, relative to the project root."
                            },
                            "content": {
                                "type": "string",
                                "description": "The complete text content of the file."
                            }
                        },
                        "required": ["path", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "patch_file",
                    "description": "Edit an existing file by finding a specific block of code and replacing it. This is preferred over writing the whole file if the file is large.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The path of the file to modify, relative to the project root."
                            },
                            "search_content": {
                                "type": "string",
                                "description": "The exact block of code to find. Must match the file content exactly including whitespaces."
                            },
                            "replacement_content": {
                                "type": "string",
                                "description": "The block of code to replace search_content with."
                            }
                        },
                        "required": ["path", "search_content", "replacement_content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": "Run a shell/terminal command in the project root directory. Use this to compile code, run tests, install dependencies, or verify behavior.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The exact terminal command to execute (e.g. 'pytest', 'npm test', 'gcc main.c')."
                            }
                        },
                        "required": ["command"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "git_status",
                    "description": "Get the current git status of the project, including modified files, untracked files, and staged changes.",
                    "parameters": {
                        "type": "object",
                        "properties": {}
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "git_commit",
                    "description": "Commit staged changes to git with a descriptive commit message.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message": {
                                "type": "string",
                                "description": "The commit message (following conventional commit guidelines if appropriate)."
                            }
                        },
                        "required": ["message"]
                    }
                }
            }
        ]

    def chat(self, messages: List[Dict[str, Any]], stream: bool = True) -> Generator[Dict[str, Any], None, Dict[str, Any]]:
        """
        Sends messages and tools to Ollama.
        If stream=True:
            Yields dicts representing stream chunks.
            Returns the final compiled response message when done.
        """
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": stream,
            "tools": self.get_tool_definitions(),
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx
            },
            "keep_alive": self.keep_alive
        }

        if not stream:
            response = self.client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("message", {})

        # Streaming mode:
        # We need to yield chunks of text or tool calls as they arrive, and compile the final message.
        accumulated_message = {"role": "assistant", "content": ""}
        tool_calls_map = {}

        try:
            with self.client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    msg_chunk = chunk.get("message", {})
                    
                    # Extract content chunk
                    content = msg_chunk.get("content", "")
                    if content:
                        accumulated_message["content"] += content
                        yield {"type": "content", "delta": content}

                    # Extract tool calls chunk
                    tool_calls = msg_chunk.get("tool_calls", [])
                    for tc in tool_calls:
                        index = tc.get("index", 0)
                        if index not in tool_calls_map:
                            tool_calls_map[index] = {
                                "id": tc.get("id", f"call_{index}"),
                                "type": "function",
                                "function": {
                                    "name": tc["function"].get("name", ""),
                                    "arguments": tc["function"].get("arguments", "")
                                }
                            }
                        else:
                            # Append arguments if they come as string chunks
                            fn = tc.get("function", {})
                            args = fn.get("arguments", "")
                            if isinstance(args, str):
                                tool_calls_map[index]["function"]["arguments"] += args
                            elif isinstance(args, dict):
                                tool_calls_map[index]["function"]["arguments"] = args
        except KeyboardInterrupt:
            raise

        if tool_calls_map:
            final_calls = []
            for idx in sorted(tool_calls_map.keys()):
                call = tool_calls_map[idx]
                args = call["function"]["arguments"]
                if isinstance(args, str):
                    if args.strip():
                        try:
                            call["function"]["arguments"] = json.loads(args)
                        except json.JSONDecodeError:
                            call["function"]["arguments"] = {}
                    else:
                        call["function"]["arguments"] = {}
                final_calls.append(call)
            accumulated_message["tool_calls"] = final_calls
            yield {"type": "tool_calls", "tool_calls": final_calls}

        return accumulated_message
