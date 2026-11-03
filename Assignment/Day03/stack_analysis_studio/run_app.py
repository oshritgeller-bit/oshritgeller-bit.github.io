from __future__ import annotations

import importlib
import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
MAIN_FILE = PROJECT_ROOT / "main.py"
MINIMUM_PYTHON = (3, 11)  # napari 0.10 needs Python 3.11 or newer


def _version_tuple(version: str) -> tuple[int, ...]:
    match = re.match(r"(\d+(?:\.\d+)*)", version)
    if match is None:
        raise ValueError(f"Cannot interpret installed package version: {version}")
    return tuple(int(part) for part in match.group(1).split("."))


def _missing_requirements() -> list[str]:
    missing = []
    for raw_requirement in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        requirement = raw_requirement.partition("#")[0].strip()
        if not requirement:
            continue
        match = re.fullmatch(
            r"([A-Za-z0-9_.-]+)(?:\s*>=\s*([0-9.]+))?",
            requirement,
        )
        if match is None:
            raise ValueError(f"Unsupported requirement format: {requirement}")
        package, minimum_version = match.groups()
        try:
            installed_version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            missing.append(requirement)
            continue
        if minimum_version and _version_tuple(installed_version) < _version_tuple(
            minimum_version
        ):
            missing.append(requirement)

    try:
        importlib.import_module("PySide6.QtWebEngineWidgets")
        importlib.import_module("PySide6.QtWebChannel")
    except (ImportError, OSError):
        if not any(item.lower().startswith("pyside6") for item in missing):
            missing.append("PySide6>=6.5")
    return missing


def main() -> int:
    if sys.version_info < MINIMUM_PYTHON:
        print(
            "Stack Analysis Studio needs Python "
            f"{'.'.join(map(str, MINIMUM_PYTHON))} or newer; this is "
            f"{sys.version.split()[0]}.",
            file=sys.stderr,
        )
        return 1
    if not REQUIREMENTS_FILE.is_file():
        raise FileNotFoundError(f"Requirements file not found: {REQUIREMENTS_FILE}")
    if not MAIN_FILE.is_file():
        raise FileNotFoundError(f"Application entry point not found: {MAIN_FILE}")

    missing = _missing_requirements()
    if missing:
        print(f"Installing missing requirements: {', '.join(missing)}")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(REQUIREMENTS_FILE),
            ],
            cwd=PROJECT_ROOT,
            check=False,
        )
        if result.returncode:
            print(
                "Dependency installation failed. Check the output above and try "
                "again with a working internet connection.",
                file=sys.stderr,
            )
            return result.returncode

        still_missing = _missing_requirements()
        if still_missing:
            raise RuntimeError(
                "Requirements are still unavailable after installation: "
                + ", ".join(still_missing)
            )
    else:
        print("Requirements are already installed.")

    if sys.platform == "win32":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.is_file():
            raise FileNotFoundError(
                f"pythonw.exe was not found beside the active interpreter: {pythonw}"
            )
        creation_flags = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        subprocess.Popen(
            [str(pythonw), str(MAIN_FILE)],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
            close_fds=True,
        )
        return 0

    return subprocess.run(
        [sys.executable, str(MAIN_FILE)],
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
