"""fit_sss_mask_and_compensate on Dabi's Face, with the true target colours.

The target comes from a Face bake with compensation OFF, so COL is exactly the
master shader's colour. Everything is judged on a PNG round trip (8-bit, as it ships)
by simulating the game:

    albedo = mix(col, CV11, PRM.r * CV30.x)
    direct = mix(albedo * nDotL / pi, CV11 * blend * nDotLSkin, blend)   (ssbh_wgpu)

The claims under test: black lines stay black in albedo AND under light, bright skin
keeps its full mask, the mask is only ever lowered, and the result beats the plain
clamped compensation.
"""
import bpy, sys, os, shutil, tempfile, importlib
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = open(os.path.join(HERE, "sss_fit.txt"), "w", encoding="utf-8")
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

CV11 = np.array([0.25, 0.0333, 0.0])
CV30X, CV30Y = 0.5, 1.5
# Found by pattern, not name: the bake names the set after its material, and the Face
# object's material has changed (FaceBurnt) since the first bakes were named.
import glob as _glob
_cols = sorted(_glob.glob(os.path.join(HERE, "_target", "*_col.png")))
SRC_COL = _cols[0] if _cols else ""
SRC_PRM = SRC_COL[:-len("_col.png")] + "_prm.png" if SRC_COL else ""
tmp = tempfile.mkdtemp(prefix="sssfit_")

def load(path, cs):
    img = bpy.data.images.load(path)
    img.colorspace_settings.name = cs
    return img

def raw(path):
    img = load(path, 'Non-Color')
    a = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    return a.reshape(-1, 4)

def lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

def save(img, path):
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()

check(bool(SRC_COL) and os.path.exists(SRC_COL) and os.path.exists(SRC_PRM), "clean Face target bake present (%s)" % os.path.basename(SRC_COL))
if fails:
    w("RESULT: FAIL"); OUT.close(); raise SystemExit

target_raw = raw(SRC_COL)
prm_raw = raw(SRC_PRM)
opaque = target_raw[:, 3] > 0.0
T = lin(target_raw[opaque, :3].astype(np.float64))
lum = T @ np.array([0.2126, 0.7152, 0.0722])
w("Face covered texels: %d   target red linear p1/p5/p50: %s"
  % (opaque.sum(), np.round(np.percentile(T[:, 0], [1, 5, 50]), 4)))

def run(fit):
    col_p = os.path.join(tmp, "col_%d.png" % fit)
    prm_p = os.path.join(tmp, "prm_%d.png" % fit)
    shutil.copy2(SRC_COL, col_p)
    shutil.copy2(SRC_PRM, prm_p)
    ic, ip = load(col_p, 'sRGB'), load(prm_p, 'Non-Color')
    if fit:
        res = core.fit_sss_mask_and_compensate(ic, ip, tuple(CV11), CV30X)
    else:
        res = core.compensate_col_for_sss(ic, ip, tuple(CV11), CV30X)
    save(ic, col_p)
    save(ip, prm_p)
    bpy.data.images.remove(ic)
    bpy.data.images.remove(ip)
    return res, raw(col_p), raw(prm_p)

def game(col_rows, prm_rows):
    s = (prm_rows[:, 0] * CV30X)[:, None]
    albedo = s * CV11 + (1 - s) * lin(col_rows[:, :3].astype(np.float64))
    # Fully lit, nDotL = 1: the skin term adds CV11 light that does not depend on albedo.
    ndl_skin = np.clip(1.0 * CV30Y * 0.5 + 0.5, 0, 1)
    direct = (1 - s) * albedo / np.pi + s * (CV11 * s * ndl_skin)
    return albedo, direct

plain_res, plain_col, plain_prm = run(False)
fit_res, fit_col, fit_prm = run(True)
w("\nfit report: %s" % {k: round(v, 4) if isinstance(v, float) else v for k, v in fit_res.items()})

alb_plain, dir_plain = game(plain_col[opaque], plain_prm[opaque])
alb_fit, dir_fit = game(fit_col[opaque], fit_prm[opaque])
alb_none, dir_none = game(target_raw[opaque], prm_raw[opaque])
err = lambda a: float(np.abs(a - T).mean())
w("in-game albedo error (mean |linear|): uncompensated %.4f | plain clamped %.4f | fitted %.4f"
  % (err(alb_none), err(alb_plain), err(alb_fit)))

check(err(alb_fit) < 0.01, "fitted albedo is essentially exact after 8-bit round trip (%.4f)" % err(alb_fit))
check(err(alb_fit) < 0.5 * err(alb_plain),
      "fitted beats plain clamped compensation by at least half (%.4f vs %.4f)"
      % (err(alb_fit), err(alb_plain)))

# The lines: the darkest texels of the target.
lines = lum < np.percentile(lum, 2)
w("\nlines (darkest 2%%, %d texels, target luminance <= %.4f):" % (lines.sum(), lum[lines].max()))
mask_lines = fit_prm[opaque][lines, 0]
w("   mask median %.3f   albedo red now %.4f (uncompensated %.4f, plain %.4f)"
  % (np.median(mask_lines), np.median(alb_fit[lines, 0]), np.median(alb_none[lines, 0]),
     np.median(alb_plain[lines, 0])))
w("   lit red on lines   now %.4f (uncompensated %.4f, plain %.4f)"
  % (np.median(dir_fit[lines, 0]), np.median(dir_none[lines, 0]), np.median(dir_plain[lines, 0])))
check(np.median(mask_lines) < 0.05, "SSS mask is ~0 on the lines (%.3f)" % np.median(mask_lines))
check(np.median(alb_fit[lines, 0]) <= np.median(T[lines, 0]) + 0.003,
      "line albedo is back to the target's darkness (%.4f vs target %.4f)"
      % (np.median(alb_fit[lines, 0]), np.median(T[lines, 0])))
check(np.median(dir_fit[lines, 0]) < 0.25 * np.median(dir_plain[lines, 0]),
      "lit lines lose the red light the skin term added (%.4f vs %.4f)"
      % (np.median(dir_fit[lines, 0]), np.median(dir_plain[lines, 0])))

# Bright skin keeps its subsurface.
bright = T[:, 0] > 0.2
if bright.any():
    mb = fit_prm[opaque][bright, 0]
    w("\nbright skin (target red > 0.2, %d texels): mask median %.3f" % (bright.sum(), np.median(mb)))
    check(np.median(mb) > 0.98, "bright skin keeps its full SSS mask (%.3f)" % np.median(mb))

w("\noverall fitted mask: median %.3f   p10 %.3f   share below 0.1: %.1f%%"
  % (np.median(fit_prm[opaque][:, 0]), np.percentile(fit_prm[opaque][:, 0], 10),
     100 * (fit_prm[opaque][:, 0] < 0.1).mean()))
check(bool((fit_prm[opaque][:, 0] <= prm_raw[opaque][:, 0] + 1.0 / 255 + 1e-6).all()),
      "mask is only ever lowered, never raised")
unc = ~opaque
check(np.abs(fit_prm[unc] - prm_raw[unc]).max() < 1.0 / 255 + 1e-6 and
      np.abs(fit_col[unc] - target_raw[unc]).max() < 1.0 / 255 + 1e-6,
      "uncovered texels untouched")
check(np.abs(fit_prm[:, 1:] - prm_raw[:, 1:]).max() < 1.0 / 255 + 1e-6,
      "PRM G/B/A (roughness, AO, specular) untouched")

shutil.rmtree(tmp, ignore_errors=True)
w("\nFAILURES: %d" % len(fails))
for f in fails:
    w("  - " + f)
w("RESULT: " + ("PASS" if not fails else "FAIL"))
OUT.close()
