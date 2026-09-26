"""web/build.py: the static browser build (assets + the Python zip Pyodide loads)."""

import io
import runpy
import zipfile
from pathlib import Path

BUILD = runpy.run_path(str(Path(__file__).parent.parent / "web" / "build.py"))


def test_build_writes_assets_and_stamps_the_build_id(tmp_path):
    BUILD["build"](tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {"index.html", "style.css", "app.js", "terminalgames.zip"}
    app_js = (tmp_path / "app.js").read_text()
    assert "__BUILD_ID__" not in app_js
    assert 'const BUILD_ID = "' in app_js


def test_zip_holds_only_what_the_browser_runs():
    names = zipfile.ZipFile(io.BytesIO(BUILD["python_zip"]())).namelist()
    assert "terminalgames/web_bridge.py" in names
    assert "terminalgames/engine/session.py" in names
    assert "terminalgames/stories/story_01_zero_day/manifest.yaml" in names
    assert not [n for n in names if n.endswith(("tui.py", "main.py")) or "/tools/" in n or "__pycache__" in n]


def test_zip_is_reproducible():
    assert BUILD["python_zip"]() == BUILD["python_zip"]()
