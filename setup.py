from setuptools import setup, find_packages

setup(
    name="ultron-cli",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "rich>=13.0.0",
        "prompt_toolkit>=3.0.0",
        "httpx>=0.24.0",
        "click>=8.0.0",
        "keyring>=24.0.0",
    ],
    entry_points={
        "console_scripts": [
            "ultron=ultron.cli:main",
            "ultron-ci=ultron.headless:headless_cli_main",
            "ultron-recover=ultron.recovery_bootstrap:cli_main",
        ],
    },
    author="Ultron AI Team",
    description="Advanced local AI coding agent running on Ollama qwen2.5-coder",
    python_requires=">=3.11",
)
