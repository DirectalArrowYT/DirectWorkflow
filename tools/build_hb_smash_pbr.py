"""Build the standalone "HB Smash PBR" Blender add-on from source/bake_texs.

    python3 tools/build_hb_smash_pbr.py            # every platform's texture tool
    python3 tools/build_hb_smash_pbr.py --platform linux

Writes dist/hb_smash_pbr/ (point Blender's add-on folder at it with a symlink
to work on it live after each rebuild) and dist/hb_smash_pbr.zip (Edit >
Preferences > Add-ons > Install from Disk).

The package stays the single source: smash_bridge.py picks _vendor/ and bin/
when it is not inside DirectWorkflow, so nothing here is edited by hand.
"""
import argparse
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "source"
DIST = ROOT / "dist"
NAME = "hb_smash_pbr"

VENDOR = {
    "material_grouping.py": SOURCE / "material_grouping.py",
    "shader_info.py": SOURCE / "model/material/shader_info.py",
    "shader_file/shader_info.json.gz": SOURCE / "model/material/shader_file/shader_info.json.gz",
    "shader_file/shader_usage.json.gz": SOURCE / "model/material/shader_file/shader_usage.json.gz",
}
TEX_TOOL = ROOT / "dependencies/ultimate_tex"
PLATFORMS = ("linux", "win", "macos")


def build(platforms):
    out = DIST / NAME
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(SOURCE / "bake_texs", out,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    vendor = out / "_vendor"
    for rel, src in VENDOR.items():
        dest = vendor / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    (vendor / "__init__.py").write_text(
        '"""Copies of DirectWorkflow modules, made by tools/build_hb_smash_pbr.py."""\n')

    for name in platforms:
        shutil.copytree(TEX_TOOL / name, out / "bin" / name)
    for license_file in TEX_TOOL.glob("LICENSE*"):
        shutil.copy2(license_file, out / "bin" / license_file.name)

    zip_path = DIST / f"{NAME}.zip"
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo.from_file(path, Path(NAME) / path.relative_to(out))
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, path.read_bytes())
    return out, zip_path


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", choices=PLATFORMS, action="append",
                        help="texture tool to bundle (repeatable; default all)")
    args = parser.parse_args()
    out, zip_path = build(args.platform or PLATFORMS)
    size = zip_path.stat().st_size / 1e6
    print(f"{out}\n{zip_path} ({size:.1f} MB)")


if __name__ == "__main__":
    main()
