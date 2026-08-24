"""Load a vendored .pyd/.so for the current OS and CPython ABI."""
from __future__ import annotations

import ctypes
import importlib.util
import os
import sys
from pathlib import Path


def _add_windows_dll_directories(*extra: Path) -> None:
    """Make python3.dll / VC runtime visible to extension modules.

    Blender 4.x keeps python3.dll next to blender.exe, not next to
    python.exe. ABI3 wheels (pyprc) need that DLL on the search path.
    """
    if not sys.platform.startswith('win') or not hasattr(os, 'add_dll_directory'):
        return
    seen: set[str] = set()
    candidates = [Path(p) for p in extra if p]
    exe_dir = Path(sys.executable).resolve().parent
    folder = exe_dir
    for _ in range(5):
        candidates.append(folder)
        folder = folder.parent
    for folder in candidates:
        try:
            resolved = folder.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen or not resolved.is_dir():
            continue
        python3_dll = resolved / 'python3.dll'
        is_extra = extra and folder in extra
        if not is_extra and not python3_dll.is_file():
            continue
        seen.add(key)
        try:
            os.add_dll_directory(str(resolved))
        except OSError:
            continue
        if python3_dll.is_file():
            try:
                ctypes.WinDLL(str(python3_dll))
            except OSError:
                pass


def abi_tag() -> str:
    return f'cp{sys.version_info.major}{sys.version_info.minor}'


def _platform_bases(package_dir: Path) -> list[Path]:
    plat = sys.platform
    if plat.startswith('win'):
        return [package_dir / 'win']
    if plat.startswith('lin'):
        return [package_dir / 'linux']
    if plat.startswith('dar'):
        return [package_dir / 'macos' / 'arm64', package_dir / 'macos' / 'x86']
    return []


def _extension() -> str:
    return '.pyd' if sys.platform.startswith('win') else '.so'


def candidate_paths(package_dir: Path, module_name: str) -> list[Path]:
    """Prefer ABI-specific folders, then abi3, then the legacy flat layout."""
    ext = _extension()
    abi = abi_tag()
    filename = f'{module_name}{ext}'
    paths: list[Path] = []
    for base in _platform_bases(package_dir):
        paths.append(base / abi / filename)
        paths.append(base / 'abi3' / filename)
        paths.append(base / filename)
    return paths


def load_native(module_name: str, package_globals: dict | None = None) -> None:
    """Import the matching native module and star-export it into the caller package."""
    if package_globals is None:
        # Caller's globals: the package __init__ that invoked us.
        package_globals = sys._getframe(1).f_globals

    package_file = package_globals.get('__file__')
    if not package_file:
        raise ImportError(f'Cannot locate package directory for {module_name}')
    package_dir = Path(package_file).resolve().parent
    _add_windows_dll_directories()

    last_error: BaseException | None = None
    tried: list[str] = []
    for path in candidate_paths(package_dir, module_name):
        tried.append(str(path))
        if not path.is_file():
            continue
        _add_windows_dll_directories(path.parent)
        # The C extension exports PyInit_<module_name>, so the spec name
        # must be exactly ssbh_data_py / pyprc (not a helper alias).
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            continue
        try:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:
            last_error = exc
            continue

        names = getattr(module, '__all__', None)
        if names is None:
            names = [name for name in dir(module) if not name.startswith('_')]
        for name in names:
            package_globals[name] = getattr(module, name)
        return

    abi = abi_tag()
    detail = f' Last load error: {last_error}' if last_error else ''
    raise ImportError(
        f'No {module_name} binary for {sys.platform} / Python '
        f'{sys.version_info.major}.{sys.version_info.minor} ({abi}). '
        f'This addon ships cp310 (Blender 4.0–4.1), cp311 (Blender 4.2–4.5), '
        f'and cp313 (Blender 5.x) builds. Looked for: {tried}.{detail}'
    )
