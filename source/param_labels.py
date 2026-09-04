"""User-level ParamLabels.csv used for smash hash40 read/write.

The live file lives in a per-user folder outside the addon install so updates
cannot overwrite custom labels:

    Windows: %APPDATA%/Smash Ultimate Labels/ParamLabels.csv
    Other:   $XDG_CONFIG_HOME/Smash Ultimate Labels/ParamLabels.csv
             (falls back to ~/.config/Smash Ultimate Labels)

If the file is missing it is downloaded from CrusherD2/param-labels (patch-1).
The bundled copy shipped with the addon is only used as a fallback, and any
extra hashes already written there are merged in on first setup.
"""

from __future__ import annotations

import csv
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

LABELS_DIR_NAME = "Smash Ultimate Labels"
LABELS_FILENAME = "ParamLabels.csv"
DOWNLOAD_URL = (
    "https://raw.githubusercontent.com/CrusherD2/param-labels/refs/heads/patch-1/ParamLabels.csv"
)
_MIN_FILE_BYTES = 1024

_known_hashes: set[str] | None = None


def labels_directory() -> Path:
    if os.name == "nt":
        roaming = os.environ.get("APPDATA")
        if roaming:
            return Path(roaming) / LABELS_DIR_NAME
        return Path.home() / "AppData" / "Roaming" / LABELS_DIR_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / LABELS_DIR_NAME
    return Path.home() / ".config" / LABELS_DIR_NAME


def labels_path() -> Path:
    return labels_directory() / LABELS_FILENAME


def bundled_labels_path() -> Path:
    return Path(__file__).resolve().parent.parent / "dependencies" / "pyprc" / LABELS_FILENAME


def _is_usable_labels_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= _MIN_FILE_BYTES
    except OSError:
        return False


def _label_to_hex(label: str) -> str:
    from ..dependencies import pyprc

    hash_obj = pyprc.hash(label)
    if hasattr(hash_obj, "value"):
        hash_int = hash_obj.value
    elif hasattr(hash_obj, "__int__"):
        hash_int = hash_obj.__int__()
    else:
        text = str(hash_obj)
        hash_int = int(text, 16) if text.startswith("0x") else int(text)
    return f"0x{hash_int:010x}"


def _looks_like_hash40(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if len(value) != 12:
        return False
    return value.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in value[2:])


def _read_hash_set(path: Path) -> set[str]:
    hashes: set[str] = set()
    if not path.is_file():
        return hashes
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for row in csv.reader(handle):
            if row:
                hashes.add(row[0].strip())
    return hashes


def _reset_hash_cache() -> None:
    global _known_hashes
    _known_hashes = None


def _ensure_known_hashes(path: Path) -> set[str]:
    global _known_hashes
    if _known_hashes is None:
        _known_hashes = _read_hash_set(path)
    return _known_hashes


def _download_official(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        DOWNLOAD_URL,
        headers={"User-Agent": "SmashUltimateBlenderTools"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()
    if len(data) < _MIN_FILE_BYTES:
        raise RuntimeError(f"Downloaded ParamLabels.csv is too small ({len(data)} bytes)")
    fd, tmp_name = tempfile.mkstemp(
        prefix="ParamLabels_",
        suffix=".csv",
        dir=str(dest.parent),
    )
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
        os.replace(tmp_name, dest)
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def _copy_bundled(dest: Path) -> None:
    bundled = bundled_labels_path()
    if not _is_usable_labels_file(bundled):
        raise FileNotFoundError(
            f"Bundled ParamLabels.csv is missing or empty: {bundled}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bundled, dest)


def _merge_extra_labels(src: Path, dest: Path) -> int:
    if not src.is_file() or src.resolve() == dest.resolve():
        return 0
    dest_hashes = _read_hash_set(dest)
    extras: list[list[str]] = []
    with open(src, "r", encoding="utf-8", errors="replace") as handle:
        for row in csv.reader(handle):
            if not row:
                continue
            hash_hex = row[0].strip()
            if hash_hex and hash_hex not in dest_hashes:
                extras.append(row)
                dest_hashes.add(hash_hex)
    if not extras:
        return 0
    with open(dest, "a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(extras)
    return len(extras)


def ensure_param_labels() -> Path:
    """Return the user ParamLabels.csv path, creating it if needed."""
    dest = labels_path()
    if _is_usable_labels_file(dest):
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    try:
        _download_official(dest)
        print(f"Downloaded ParamLabels.csv to {dest}")
    except Exception as exc:
        errors.append(f"download failed: {exc}")
        try:
            _copy_bundled(dest)
            print(f"Copied bundled ParamLabels.csv to {dest}")
        except Exception as copy_exc:
            errors.append(f"bundled copy failed: {copy_exc}")
            raise RuntimeError(
                "Could not create ParamLabels.csv at "
                f"{dest} ({'; '.join(errors)})"
            ) from copy_exc

    bundled = bundled_labels_path()
    try:
        merged = _merge_extra_labels(bundled, dest)
        if merged:
            print(f"Merged {merged} extra hash label(s) from {bundled}")
    except Exception as exc:
        print(f"Warning: Could not merge bundled ParamLabels.csv extras: {exc}")

    _reset_hash_cache()
    return dest


def load_param_labels() -> Path:
    """Ensure the user labels file exists and load it into pyprc."""
    from ..dependencies import pyprc

    path = ensure_param_labels()
    pyprc.hash.load_labels(str(path))
    return path


def add_hash_label(label: str | None, *, reload: bool = False) -> bool:
    """Append hash40(label) to the user ParamLabels.csv if it is not already present."""
    if not label or not isinstance(label, str):
        return False
    if _looks_like_hash40(label):
        return True

    try:
        path = ensure_param_labels()
        hash_hex = _label_to_hex(label)
        known = _ensure_known_hashes(path)
        if hash_hex in known:
            return True
        with open(path, "a", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerow([hash_hex, label])
        known.add(hash_hex)
        print(f"Added hash to ParamLabels.csv: {hash_hex},{label}")
        if reload:
            load_param_labels()
        return True
    except Exception as exc:
        print(f"Error adding hash to ParamLabels.csv: {exc}")
        return False


def add_hash_labels(*labels: str, reload: bool = False) -> None:
    for label in labels:
        add_hash_label(label, reload=False)
    if reload:
        try:
            load_param_labels()
        except Exception as exc:
            print(f"Warning: Could not reload ParamLabels.csv: {exc}")
