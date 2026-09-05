"""Profile lookup: shape, monotonicity, and the specific failure that spazzed."""
import sys, types, importlib.util

def stub():
    bpy = types.ModuleType("bpy")
    props = types.ModuleType("bpy.props")
    for n in ("BoolProperty", "CollectionProperty", "FloatProperty", "IntProperty",
              "PointerProperty", "StringProperty", "EnumProperty", "FloatVectorProperty"):
        setattr(props, n, lambda *a, **k: None)
    tps = types.ModuleType("bpy.types")
    for n in ("Operator", "Panel", "PropertyGroup", "UIList"):
        setattr(tps, n, type(n, (), {}))
    bpy.props, bpy.types = props, tps
    bpy.utils = types.SimpleNamespace(register_class=lambda c: None,
                                      unregister_class=lambda c: None)
    bpy.ops = types.SimpleNamespace()
    sys.modules["bpy"], sys.modules["bpy.props"], sys.modules["bpy.types"] = bpy, props, tps
    mu = types.ModuleType("mathutils")
    class Vector(tuple):
        def __new__(cls, v): return super().__new__(cls, tuple(v))
        def __sub__(s, o): return Vector(a - b for a, b in zip(s, o))
        def __add__(s, o): return Vector(a + b for a, b in zip(s, o))
        def __mul__(s, k): return Vector(a * k for a in s)
        @property
        def length(s): return sum(a * a for a in s) ** 0.5
    mu.Vector = Vector
    sys.modules["mathutils"] = mu
stub()

BASE = (r"C:/Users/Braden Shoup/AppData/Roaming/Blender Foundation/Blender/4.5"
        r"/scripts/addons/smash-ultimate-blender-animation-workflow/source/extras/")
pkg = types.ModuleType("asbpkg"); pkg.__path__ = [BASE]; sys.modules["asbpkg"] = pkg
for mod in ("swing_profiles", "auto_swing_bones"):
    spec = importlib.util.spec_from_file_location("asbpkg." + mod, BASE + mod + ".py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["asbpkg." + mod] = m
    spec.loader.exec_module(m)
A = sys.modules["asbpkg.auto_swing_bones"]

fails = []
def check(c, label):
    if not c:
        fails.append(label)

print("=== Shigaraki's chains under the new profiles ===")
for part, n, label in (("Hair", 3, "hairf/hairl/hairr"), ("Hair", 4, "hairb/hairlb/hairrb"),
                       ("Hair", 2, "hairlt/hairrt/hairbt"), ("Mantle", 4, "mantlec/l/r"),
                       ("Scarf", 2, "scarf* (no vanilla Scarf profile)")):
    rows = [A.parameters_for(part, i, n) for i in range(n)]
    print("\n  %-7s n=%d  (%s)" % (part, n, label))
    print("     goal_strength : " + " ".join("%9.4g" % r["goal_strength"] for r in rows))
    print("     min_angle_z   : " + " ".join("%9.4g" % r["min_angle_z"] for r in rows))
    print("     max_angle_z   : " + " ".join("%9.4g" % r["max_angle_z"] for r in rows))
    print("     air_resistance: " + " ".join("%9.4g" % r["air_resistance"] for r in rows))
    print("     local_gravity : " + " ".join("%9.4g" % r["local_gravity"] for r in rows))
    print("     ground_hit    : " + " ".join("%9s" % r["ground_hit"] for r in rows))

    # goal strength must not rise toward the tip
    gs = [r["goal_strength"] for r in rows]
    check(all(a >= b - 1e-6 for a, b in zip(gs, gs[1:])),
          "%s n=%d goal_strength rises toward the tip: %s" % (part, n, gs))
    # the angle cone must not narrow toward the tip
    span = [r["max_angle_z"] - r["min_angle_z"] for r in rows]
    check(all(b >= a - 1e-6 for a, b in zip(span, span[1:])),
          "%s n=%d angle cone narrows toward the tip: %s" % (part, n, span))
    for r in rows:
        check(r["max_angle_z"] > r["min_angle_z"], "%s inverted Z limits" % part)
        check(r["max_angle_y"] > r["min_angle_y"], "%s inverted Y limits" % part)
        for k in A._PROFILE_FIELDS:
            check(r.get(k) is not None, "%s n=%d missing %s" % (part, n, k))

print("\n=== the exact regression: 3-bone hair ===")
rows = [A.parameters_for("Hair", i, 3) for i in range(3)]
gs = [r["goal_strength"] for r in rows]
print("  goal_strength now %s (was 50 / 36.7 / 23.3)" % gs)
print("  min_angle_z   now %s (was -20 / -20 / -20)" % [r["min_angle_z"] for r in rows])
check(gs[0] >= 400, "3-bone hair root goal_strength still too weak: %r" % gs[0])
check(rows[-1]["min_angle_z"] <= -90, "3-bone hair tip still clamped: %r" % rows[-1]["min_angle_z"])

print("\n=== resampling for lengths vanilla never ships ===")
for n in (5, 6, 7):
    rows = [A.parameters_for("Hair", i, n) for i in range(n)]
    gs = [round(r["goal_strength"], 1) for r in rows]
    print("  Hair n=%d goal_strength %s" % (n, gs))
    check(len(rows) == n, "Hair n=%d wrong row count" % n)
    check(all(a >= b - 1e-6 for a, b in zip(gs, gs[1:])), "Hair n=%d not monotonic" % n)

print("\n=== unknown part falls back without raising ===")
for part in ("Nonsense", "Scarf", "Ribbon", "Cloth"):
    try:
        r = A.parameters_for(part, 0, 3)
        print("  %-10s goal=%.4g air=%.4g" % (part, r["goal_strength"], r["air_resistance"]))
    except Exception as e:
        fails.append("%s raised %r" % (part, e))

print("\n=== single-bone chains ===")
r = A.parameters_for("Hair", 0, 1)
print("  Hair n=1 goal=%.4g ground_hit=%s" % (r["goal_strength"], r["ground_hit"]))
check(isinstance(r["ground_hit"], bool), "ground_hit is not a bool")

print("\nFAILURES: %d" % len(fails))
for f in fails:
    print("  - " + f)
print("RESULT: " + ("PASS" if not fails else "FAIL"))
