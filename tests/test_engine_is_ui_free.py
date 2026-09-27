"""The engine, check_story and the browser build's web_bridge must stay
importable without any UI library -- the browser build runs them in Pyodide
with only PyYAML installed. Checked in a fresh interpreter, since this test
process has already imported Textual through the TUI tests."""

import subprocess
import sys


def test_engine_imports_no_ui_library():
    code = (
        "import sys, pkgutil, importlib, sidechannel.engine as e\n"
        "for m in pkgutil.iter_modules(e.__path__):\n"
        "    importlib.import_module('sidechannel.engine.' + m.name)\n"
        "import sidechannel.tools.check_story, sidechannel.web_bridge\n"
        "print(sorted(n for n in ('textual', 'rich') if n in sys.modules))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"
