"""The no-data warning must fire on FaceBurnt/SkinBurnt and stay off data-backed skin."""
import bpy, sys, os, importlib, traceback

OUT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_nodata.txt"),
           "w", encoding="utf-8")
def w(*a):
    print(*a, file=OUT); OUT.flush()

fails = []
def check(cond, label):
    w(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)

addon = next(m for n, m in sys.modules.items()
             if n.endswith("smash-ultimate-blender-animation-workflow"))
core = importlib.import_module(addon.__name__ + ".source.bake_texs.core")

KEY = "reads PRM.r as METALNESS"
for name, expect in (("FaceBurnt", True), ("SkinBurnt", True),
                     ("Face", False), ("Skin", False), ("Neck", False)):
    m = bpy.data.materials.get(name)
    if m is None:
        check(False, "%s exists" % name)
        continue
    try:
        ctx = core.MatlContext(m)
    except Exception:
        traceback.print_exc(file=OUT)
        check(False, "%s: MatlContext raised" % name)
        continue
    hit = [n for n in ctx.notes if KEY in n]
    w("\n%s: subsurface=%s shader=%r" % (name, ctx.is_subsurface, ctx.shader_label))
    for n in ctx.notes:
        w("   note: " + n[:170])
    check(bool(hit) == expect, "%s: metalness warning %s" % (name, "present" if expect else "absent"))
    if expect and hit:
        check("(1.00)" in hit[0], "%s: warning quotes the real SSS Mask value" % name)
    if not expect:
        check(core.sss_params([m]) is not None, "%s: still compensated from its data" % name)
    else:
        check(core.sss_params([m]) is None, "%s: correctly not compensated" % name)

w("\nFAILURES: %d" % len(fails))
for f in fails:
    w("  - " + f)
w("RESULT: " + ("PASS" if not fails else "FAIL"))
OUT.close()
