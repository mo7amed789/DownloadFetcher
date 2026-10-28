from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION_FILE = ROOT / "version.json"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def version_key(value: str) -> tuple[int, ...]:
    parts = []
    for part in str(value).split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "DirectFetch-Updater/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def apply_update(manifest: dict) -> bool:
    package_url = str(manifest.get("package_url") or "")
    expected_hash = str(manifest.get("sha256") or "").lower()
    if not package_url.startswith(("https://", "http://")) or not expected_hash:
        return False
    with tempfile.TemporaryDirectory(prefix="directfetch-update-") as folder:
        package = Path(folder) / "update.zip"
        staging = Path(folder) / "staging"
        download(package_url, package)
        if sha256(package) != expected_hash:
            return False
        staging.mkdir()
        with zipfile.ZipFile(package) as archive:
            archive.extractall(staging)
        source_root = staging
        nested = list(staging.iterdir())
        if len(nested) == 1 and nested[0].is_dir():
            source_root = nested[0]
        allowed = {"app.py", "updater.py", "run.bat", "version.json", "static"}
        for item in source_root.iterdir():
            if item.name not in allowed:
                continue
            target = ROOT / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
    return True


def check_for_update() -> str:
    current = read_json(VERSION_FILE)
    manifest_url = str(current.get("update_manifest_url") or os.environ.get("DIRECTFETCH_UPDATE_MANIFEST_URL") or "")
    if not manifest_url.startswith(("https://", "http://")):
        return ""
    with tempfile.TemporaryDirectory(prefix="directfetch-manifest-") as folder:
        manifest_path = Path(folder) / "manifest.json"
        download(manifest_url, manifest_path)
        manifest = read_json(manifest_path)
    if version_key(manifest.get("version", "0.0.0")) <= version_key(current.get("version", "0.0.0")):
        return ""
    if apply_update(manifest):
        VERSION_FILE.write_text(json.dumps({**current, "version": manifest["version"]}, indent=2), encoding="utf-8")
        return str(manifest["version"])
    return ""


if __name__ == "__main__":
    try:
        time.sleep(1)
        updated = check_for_update()
        if updated:
            print(f"DirectFetch updated to {updated}")
    except Exception as exc:
        print(f"DirectFetch update skipped: {exc}", file=sys.stderr)
