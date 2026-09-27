"""Build the static browser version into OUT_DIR (default: _site/).

Copies the page assets from web/ and zips just the Python the page runs in
Pyodide -- sidechannel/__init__.py, engine/, stories/ and web_bridge.py,
never the Textual frontend. The zip's content hash is stamped into app.js
and the service worker (sw.js), so a new deploy is never served a stale
cached zip and replaces the offline cache.

    python web/build.py [OUT_DIR]
    python -m http.server -d _site 8000    # then open http://localhost:8000
"""

from __future__ import annotations

import hashlib
import io
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
PACKAGE_DIR = ROOT / "sidechannel"
ASSETS = ["index.html", "style.css", "favicon.svg", "favicon-32.png", "apple-touch-icon.png"]
STAMPED = ["app.js", "sw.js"]  # get the build id in place of __BUILD_ID__
PYTHON_PARTS = ["__init__.py", "engine", "stories", "web_bridge.py"]


def python_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for part in PYTHON_PARTS:
            src = PACKAGE_DIR / part
            files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
            for path in files:
                if "__pycache__" in path.parts:
                    continue
                # Fixed timestamp: the same sources always give the same zip (and hash).
                info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), date_time=(2000, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, path.read_bytes())
    return buffer.getvalue()


def compute_build_id(data: bytes) -> str:
    """A hash of everything the site ships -- the Python zip *and* every web
    file. The service worker is only replaced when sw.js changes, and this id
    is stamped into it, so a change to any shipped file must change the id
    (or offline players would keep the old page forever)."""
    digest = hashlib.sha256(data)
    for name in ASSETS + STAMPED:
        digest.update((WEB_DIR / name).read_bytes())
    return digest.hexdigest()[:12]


def build(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        shutil.copyfile(WEB_DIR / name, out_dir / name)
    data = python_zip()
    (out_dir / "sidechannel.zip").write_bytes(data)
    build_id = compute_build_id(data)
    for name in STAMPED:
        (out_dir / name).write_text((WEB_DIR / name).read_text().replace("__BUILD_ID__", build_id))
    print(f"Built {out_dir} (build {build_id})")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site")
