"""compensate_col_for_sss on Dabi's real skin bakes, checked by simulating the game.

The claim under test is not "the function runs" but "after the game blends in
CustomVector11, the compensated texture shows the intended colour better than the
uncompensated one" - measured on a PNG round trip, so 8-bit quantisation and the
sRGB assumption about byte-image .pixels are both inside the test.
"""
import bpy, sys, os, shutil, tempfile, importlib
import numpy as np

OUT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sss_comp.txt"),
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

D = r"Z:/Documents/Hype Bros Studios/Dabi/Blender/bakes"
CV11 = (0.25, 0.0333, 0.0)
tmp = tempfile.mkdtemp(prefix="ssscomp_")

def load(path, cs):
    img = bpy.data.images.load(path)
    img.colorspace_settings.name = cs
    return img

def raw(path):
    """Stored values, as the game's texture unit would read them before decode."""
    img = load(path, 'Non-Color')
    a = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    return a.reshape(-1, 4)

def lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

for name, expect_max_after in (("Face", 0.045), ("Neck", 0.015), ("Skin", 0.03)):
    w("\n===== %s" % name)
    src_col = os.path.join(D, "def_%s_dabi_col.png" % name)
    src_prm = os.path.join(D, "def_%s_dabi_prm.png" % name)
    col_path = os.path.join(tmp, "%s_col.png" % name)
    shutil.copy2(src_col, col_path)

    original = raw(col_path)
    prm = raw(src_prm)
    s = np.clip(prm[:, 0] * 0.5, 0, 1)
    opaque = original[:, 3] > 0.0
    target = lin(original[opaque, :3].astype(np.float64))
    sb = s[opaque][:, None]

    img_col = load(col_path, 'sRGB')
    img_prm = load(src_prm, 'Non-Color')
    n, clip, before, after = core.compensate_col_for_sss(img_col, img_prm, CV11, 0.5)
    w("  reported: %d px, %.1f%% clamped, error %.4f -> %.4f" % (n, 100 * clip, before, after))
    img_col.filepath_raw = col_path
    img_col.file_format = 'PNG'
    img_col.save()
    bpy.data.images.remove(img_col)
    bpy.data.images.remove(img_prm)

    # Round trip through the file, then do what the game does.
    written = raw(col_path)
    shipped = lin(written[opaque, :3].astype(np.float64))
    game_after = sb * np.array(CV11) + (1 - sb) * shipped
    game_before = sb * np.array(CV11) + (1 - sb) * target
    err_after = float(np.abs(game_after - target).mean())
    err_before = float(np.abs(game_before - target).mean())
    w("  measured on the saved PNG: error %.4f -> %.4f" % (err_before, err_after))

    check(n > 0, "%s: pixels were compensated" % name)
    check(err_after < 0.5 * err_before,
          "%s: in-game error at least halved (%.4f -> %.4f)" % (name, err_before, err_after))
    check(err_after <= expect_max_after,
          "%s: in-game error within the measured bound (%.4f <= %.3f)"
          % (name, err_after, expect_max_after))
    check(abs(err_after - after) < 0.01,
          "%s: the report agrees with the round trip (%.4f vs %.4f) - so .pixels on a "
          "byte image really is sRGB-encoded" % (name, after, err_after))

    # Texels the mask does not reach must come out byte-identical.
    untouched = (s <= 1e-6)
    diff = np.abs(written[untouched] - original[untouched]).max() if untouched.any() else 0.0
    check(diff < 1.0 / 255.0 + 1e-6,
          "%s: PRM.r = 0 texels unchanged (%d px, max diff %.4f)"
          % (name, int(untouched.sum()), diff))
    check(np.abs(written[:, 3] - original[:, 3]).max() < 1.0 / 255.0 + 1e-6,
          "%s: alpha untouched" % name)

w("\n===== zero strength is a no-op")
col_path = os.path.join(tmp, "zero_col.png")
shutil.copy2(os.path.join(D, "def_Skin_dabi_col.png"), col_path)
original = raw(col_path)
img_col = load(col_path, 'sRGB')
img_prm = load(os.path.join(D, "def_Skin_dabi_prm.png"), 'Non-Color')
n, clip, before, after = core.compensate_col_for_sss(img_col, img_prm, CV11, 0.0)
check(n == 0, "CV30.x = 0 changes nothing (%d px)" % n)
bpy.data.images.remove(img_col)
bpy.data.images.remove(img_prm)

shutil.rmtree(tmp, ignore_errors=True)
w("\nFAILURES: %d" % len(fails))
for f in fails:
    w("  - " + f)
w("RESULT: " + ("PASS" if not fails else "FAIL"))
OUT.close()
