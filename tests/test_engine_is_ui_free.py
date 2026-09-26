"""The engine (and check_story) must stay importable without any UI
library -- a browser build runs it in Pyodide with only PyYAML installed.
Checked in a fresh interpreter, since this test process has already imported
Textual through the TUI tests."""

import subprocess
import sys


def test_engine_imports_no_ui_library():
    code = (
        "import sys, pkgutil, importlib, terminalgames.engine as e\n"
        "for m in pkgutil.iter_modules(e.__path__):\n"
        "    importlib.import_module('terminalgames.engine.' + m.name)\n"
        "import terminalgames.tools.check_story\n"
        "print(sorted(n for n in ('textual', 'rich') if n in sys.modules))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"
