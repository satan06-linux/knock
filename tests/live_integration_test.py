import os
import shutil
import tempfile
import unittest
from ultron.agent import UltronAgent

class TestUltronLiveIntegration(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        os.system(f"git init {self.workspace}")
        os.system(f"git -C {self.workspace} config user.email 'live@ultron.ai'")
        os.system(f"git -C {self.workspace} config user.name 'Ultron Live'")
        
        self.agent = UltronAgent(
            workspace_root=self.workspace,
            model_name="qwen2.5-coder:7b",
            auto_approve=True,
            auto_commit=True
        )

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_live_agent_loop(self):
        # Check if Ollama is available
        if not self.agent.model.is_available():
            self.skipTest("Ollama is offline or model 'qwen2.5-coder:7b' is not loaded.")
            
        print("\n[INFO] Running live Ollama integration test...")
        prompt = "Create a file named hello.py that contains a simple function 'add(a, b)' returning their sum, then calls it. Write only the file and do nothing else."
        
        self.agent.run(prompt)
        
        hello_path = os.path.join(self.workspace, "hello.py")
        self.assertTrue(os.path.isfile(hello_path), "Live agent failed to write hello.py!")
        
        with open(hello_path, "r") as f:
            content = f.read()
        self.assertIn("def add", content)
        
        # Verify auto-commit succeeded since it was not pre-dirty
        git_log = os.popen(f"git -C {self.workspace} log --oneline").read()
        self.assertIn("ultron: auto-commit", git_log)

if __name__ == "__main__":
    unittest.main()
