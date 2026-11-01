"""Put Rot and LegC back at rest in existing .nuanmb files, keeping each pose (source/anim/locked_bones.py).

Run with Blender's Python (it needs mathutils):

    blender --background --python fix_locked_bones.py -- <model.nusktb> <file or folder> [--write] [--backup <dir>]

Without --write it only reports which animations would change. With --backup, each file is copied
there before it is overwritten.
"""

import importlib.util
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from dependencies import ssbh_data_py  # noqa: E402
from mathutils import Matrix  # noqa: E402

spec = importlib.util.spec_from_file_location("locked_bones", os.path.join(HERE, "source", "anim", "locked_bones.py"))
locked_bones = importlib.util.module_from_spec(spec)
spec.loader.exec_module(locked_bones)


def skeleton(path):
    """{bone: parent}, {bone: rest Matrix relative to its parent}. nusktb matrices are row-major
    with the translation in the last row."""
    skel = ssbh_data_py.skel_data.read_skel(path)
    names = [b.name for b in skel.bones]
    parents, rest = {}, {}
    for b in skel.bones:
        parents[b.name] = names[b.parent_index] if b.parent_index is not None else None
        rest[b.name] = Matrix([list(row) for row in b.transform]).transposed()
    return parents, rest


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    write = "--write" in args
    backup = None
    if "--backup" in args:
        backup = args[args.index("--backup") + 1]
        os.makedirs(backup, exist_ok=True)
    paths = [a for a in args if not a.startswith("--") and a != backup]
    skel_path, target = paths[0], paths[1]
    parents, rest = skeleton(skel_path)
    files = [target] if os.path.isfile(target) else sorted(
        os.path.join(target, f) for f in os.listdir(target) if f.endswith(".nuanmb"))
    count = 0
    for f in files:
        anim = ssbh_data_py.anim_data.read_anim(f)
        changed = locked_bones.lock_bones(anim, parents, rest, ssbh_data_py.anim_data.Transform)
        if not changed:
            continue
        count += 1
        print(f"{os.path.basename(f)}: " + "; ".join(f"{b} -> {', '.join(c) or 'no children'}" for b, c in changed.items()))
        if write:
            if backup:
                shutil.copy2(f, os.path.join(backup, os.path.basename(f)))
            anim.save(f)
    print(f"{count} of {len(files)} animations {'fixed' if write else 'would change'}")


main()
