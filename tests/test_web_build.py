"""web/build.py: the static browser build (assets + the Python zip Pyodide loads)."""

import io
import re
import runpy
import shutil
import zipfile
from pathlib import Path

BUILD = runpy.run_path(str(Path(__file__).parent.parent / "web" / "build.py"))


def test_build_writes_assets_and_stamps_the_build_id(tmp_path):
    BUILD["build"](tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {
        "index.html",
        "style.css",
        "app.js",
        "sw.js",
        "sidechannel.zip",
        "favicon.svg",
        "favicon-32.png",
        "apple-touch-icon.png",
    }
    assert (tmp_path / "favicon-32.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"  # copied as binary
    for name in ("app.js", "sw.js"):
        stamped = (tmp_path / name).read_text()
        assert "__BUILD_ID__" not in stamped
        assert 'const BUILD_ID = "' in stamped


def test_zip_holds_only_what_the_browser_runs():
    names = zipfile.ZipFile(io.BytesIO(BUILD["python_zip"]())).namelist()
    assert "sidechannel/web_bridge.py" in names
    assert "sidechannel/engine/session.py" in names
    assert "sidechannel/stories/story_01_zero_day/manifest.yaml" in names
    assert not [n for n in names if n.endswith(("tui.py", "main.py")) or "/tools/" in n or "__pycache__" in n]


def test_zip_is_reproducible():
    assert BUILD["python_zip"]() == BUILD["python_zip"]()


def test_page_and_service_worker_agree_on_the_pyodide_url():
    web = Path(__file__).parent.parent / "web"
    pattern = re.compile(r'const PYODIDE_URL = "([^"]+)";')
    page = pattern.search((web / "app.js").read_text())
    worker = pattern.search((web / "sw.js").read_text())
    assert page and worker and page.group(1) == worker.group(1)
    assert f'PYODIDE_CACHE = "pyodide-{page.group(1).split("/")[-3]}"' in (web / "sw.js").read_text()


def test_changing_any_shipped_web_file_changes_the_build_id(tmp_path, monkeypatch):
    """Otherwise sw.js stays byte-identical, the browser never installs a new
    worker, and offline players keep the old page forever."""
    web = tmp_path / "web"
    shutil.copytree(Path(__file__).parent.parent / "web", web)
    monkeypatch.setitem(BUILD["compute_build_id"].__globals__, "WEB_DIR", web)  # run_path returns a copy
    data = BUILD["python_zip"]()
    ids = {BUILD["compute_build_id"](data)}
    for name in ["index.html", "style.css", "app.js", "sw.js", "favicon.svg"]:
        (web / name).write_text((web / name).read_text() + "\n/* changed */\n")
        ids.add(BUILD["compute_build_id"](data))
    assert len(ids) == 6  # every edit produced a new id
