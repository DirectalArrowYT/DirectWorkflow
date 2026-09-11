"""pbr_range_notes must flag Dabi's real problems and stay quiet on vanilla.

Checked against the actual shipped data rather than synthetic arrays: Dabi's bakes
(which are byte-identical to what ships) and decoded vanilla PRMs from 10 fighters.
"""
import bpy, sys, os, glob, importlib
from pathlib import Path
import numpy as np

OUT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pbr_notes.txt"),
           "w", encoding="utf-8")
def w(*a):
    print(*a, file=OUT); OUT.flush(); print(*a)

fails = []
def check(cond, label):
    w(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)

addon = next(m for n, m in sys.modules.items()
             if n.endswith("smash-ultimate-blender-animation-workflow"))
core = importlib.import_module(addon.__name__ + ".source.bake_texs.core")
conv = importlib.import_module(addon.__name__ + ".source.model.material.texture.convert_nutexb_to_png")

def load(path):
    img = bpy.data.images.load(path)
    img.colorspace_settings.name = 'Non-Color'
    return img

def alpha_mask(col_path):
    img = load(col_path)
    a = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    return a.reshape(-1, 4)[:, 3] > 0.0

def notes_for(prm_path, col_path, sss):
    img = load(prm_path)
    covered = alpha_mask(col_path) if col_path else None
    out = core.pbr_range_notes(img, covered, sss)
    bpy.data.images.remove(img)
    return out


MIRROR = "roughness below"
METAL = "is metal with roughness"
FLAT_AO = "AO) is flat"
SPEC = "specular above"

def has(notes, word):
    return any(word in n for n in notes)

BK = r"Z:/Documents/Hype Bros Studios/Dabi/Blender/bakes"
w("=== Dabi ===")
expect = {
    # stem: (subsurface?, must flag, must not flag)
    "alp_Hair":    (False, [MIRROR, FLAT_AO], []),
    "def_Clothes": (False, [METAL], [FLAT_AO]),
    "def_Face":    (True,  [], [MIRROR, METAL, FLAT_AO, SPEC]),
    "def_Neck":    (True,  [], [MIRROR, METAL, FLAT_AO, SPEC]),
    "def_Skin":    (True,  [], [MIRROR, METAL, FLAT_AO, SPEC]),
}
for stem, (sss, must, must_not) in expect.items():
    notes = notes_for(os.path.join(BK, stem + "_dabi_prm.png"),
                      os.path.join(BK, stem + "_dabi_col.png"), sss)
    w("\n  %s -> %d note(s)" % (stem, len(notes)))
    for n in notes:
        w("     " + n[:150])
    for word in must:
        check(has(notes, word), "%s: flags '%s'" % (stem, word))
    for word in must_not:
        check(not has(notes, word), "%s: does not flag '%s'" % (stem, word))

w("\n=== vanilla (false-positive rate) ===")
DEC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dec2")
flagged = {}
total = 0
for f in ("snake", "ike", "cloud", "lucina", "edge", "marth", "pit", "link", "wolf", "ridley"):
    base = r"Z:/Documents/SmashArc/fighter/%s/model/body/c00" % f
    for prm_n in sorted(glob.glob(base + "/*_prm.nutexb"))[:4]:
        stem = Path(prm_n).stem[:-4]
        prm_png = os.path.join(DEC, "%s_%s_prm.png" % (f, stem))
        col_png = os.path.join(DEC, "%s_%s_col.png" % (f, stem))
        if not os.path.exists(prm_png):
            try:
                conv.convert_nutexb_to_png(Path(prm_n), Path(prm_png))
            except Exception:
                continue
        col_n = base + "/" + stem + "_col.nutexb"
        if not os.path.exists(col_png) and os.path.exists(col_n):
            try:
                conv.convert_nutexb_to_png(Path(col_n), Path(col_png))
            except Exception:
                pass
        if not os.path.exists(prm_png):
            continue
        total += 1
        use_col = col_png if os.path.exists(col_png) else None
        try:
            notes = notes_for(prm_png, use_col, "skin" in stem)
        except Exception:
            notes = notes_for(prm_png, None, "skin" in stem)
        if notes:
            flagged["%s/%s" % (f, stem)] = notes
w("  vanilla textures checked: %d, flagged: %d" % (total, len(flagged)))
for k, notes in flagged.items():
    for n in notes:
        w("   %-26s %s" % (k, n.split(" - ")[0][11:]))
check(total >= 25, "enough vanilla textures to mean something (%d)" % total)
mirror_flags = [k for k, n in flagged.items() if has(n, MIRROR)]
check(all(("eye" in k or "wolf" in k) for k in mirror_flags),
      "mirror roughness only on vanilla's genuinely glossy parts - eyes, Wolf's visor (%s)"
      % mirror_flags)
metal_flags = [k for k, n in flagged.items() if has(n, METAL)]
check(not metal_flags, "mirror metal never fires on vanilla (%s)" % metal_flags)
spec_flags = [k for k, n in flagged.items() if has(n, SPEC)]
# Vanilla genuinely crosses the line in two places - Marth's body at 0.98 and a third of
# Ridley's skin at 0.70 - so what is asserted is a low rate and that each note is true.
check(len(spec_flags) <= 2, "high specular on at most 2 vanilla textures (%s)" % spec_flags)
for k, notes in flagged.items():
    for n in notes:
        if n.startswith("PBR check: specular above"):
            med = float(n.split("texels median ")[1].split(",")[0])
            check(med > 0.3, "%s: flagged texels really are above the line (median %.2f)"
                  % (k, med))
ao_flags = [k for k, n in flagged.items() if has(n, FLAT_AO)]
w("  flat-AO notes on vanilla (informational - vanilla does ship this): %s" % ao_flags)
# Every note must describe what triggered it; the old specular wording printed a median
# (0.15 on Ridley) that sat below the 0.25 line it claimed to have crossed.
for k, notes in flagged.items():
    for n in notes:
        if n.startswith("PBR check: specular above"):
            share = float(n.split(" on ")[1].split("%")[0])
            check(share >= 25.0, "%s: specular note reports the share that triggered it (%.0f%%)"
                  % (k, share))

w("\nFAILURES: %d" % len(fails))
for f in fails:
    w("  - " + f)
w("RESULT: " + ("PASS" if not fails else "FAIL"))
OUT.close()
