import os
import tempfile
import unittest
from ultron.context import ContextManager
from ultron.tools import ToolManager

class TestUltronCore(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.workspace = self.test_dir.name
        self.context = ContextManager(self.workspace)
        self.tools = ToolManager(self.workspace)
        
    def tearDown(self):
        self.test_dir.cleanup()
        
    def test_safe_path(self):
        # Should resolve within workspace
        safe = self.tools._resolve_safe_path("sub/file.py")
        self.assertTrue(safe.startswith(self.workspace))
        
        # Should raise permission error for directory traversal
        with self.assertRaises(PermissionError):
            self.tools._resolve_safe_path("../../outside.py")

    def test_file_operations(self):
        # Write file
        write_res = self.tools.write_file("test.py", "print('hello')\n")
        self.assertIn("Successfully wrote", write_res)
        
        # Read file
        view_res = self.tools.view_file("test.py")
        self.assertIn("print('hello')", view_res)
        
        # Patch file
        patch_res = self.tools.patch_file("test.py", "print('hello')", "print('world')")
        self.assertIn("Successfully patched", patch_res)
        
        # Read again to verify patch
        view_res_2 = self.tools.view_file("test.py")
        self.assertIn("print('world')", view_res_2)
        self.assertNotIn("print('hello')", view_res_2)

    def test_context_management(self):
        # Create a dummy file
        self.tools.write_file("app.py", "import os\n")
        
        # Add to context
        added = self.context.add_file("app.py")
        self.assertTrue(added)
        self.assertIn("app.py", self.context.pinned_files)
        
        # Build prompt
        prompt = self.context.build_context_prompt()
        self.assertIn("=== ACTIVE CONTEXT FILES ===", prompt)
        self.assertIn("--- File: app.py ---", prompt)
        self.assertIn("import os", prompt)
        
        # Drop from context
        dropped = self.context.drop_file("app.py")
        self.assertTrue(dropped)
        self.assertNotIn("app.py", self.context.pinned_files)

if __name__ == "__main__":
    unittest.main()
