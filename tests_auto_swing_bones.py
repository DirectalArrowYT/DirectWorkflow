"""Pure-logic tests: no Blender data needed, only the naming/parameter functions."""
import sys, importlib, importlib.util, types, os

# Stub bpy hard enough that the module imports outside Blender.
def stub():
    bpy = types.ModuleType("bpy")
    class _P:
        def __init__(s,*a,**k): pass
    def prop(*a, **k): return None
    props = types.ModuleType("bpy.props")
    for n in ("BoolProperty","CollectionProperty","FloatProperty","IntProperty",
              "PointerProperty","StringProperty","EnumProperty","FloatVectorProperty"):
        setattr(props, n, prop)
    tps = types.ModuleType("bpy.types")
    for n in ("Operator","Panel","PropertyGroup","UIList"):
        setattr(tps, n, type(n, (), {}))
    bpy.props, bpy.types = props, tps
    bpy.utils = types.SimpleNamespace(register_class=lambda c: None, unregister_class=lambda c: None)
    bpy.ops = types.SimpleNamespace()
    sys.modules["bpy"], sys.modules["bpy.props"], sys.modules["bpy.types"] = bpy, props, tps
    mu = types.ModuleType("mathutils")
    class Vector(tuple):
        def __new__(cls, v): return super().__new__(cls, tuple(v))
        def __sub__(s,o): return Vector(a-b for a,b in zip(s,o))
        def __add__(s,o): return Vector(a+b for a,b in zip(s,o))
        def __mul__(s,k): return Vector(a*k for a in s)
        @property
        def length(s): return sum(a*a for a in s) ** 0.5
    mu.Vector = Vector
    sys.modules["mathutils"] = mu
stub()

# auto_swing_bones imports swing_profiles relatively, so load both inside a
# throwaway package rather than as loose files.
BASE = (r"C:/Users/Braden Shoup/AppData/Roaming/Blender Foundation/Blender/4.5"
        r"/scripts/addons/smash-ultimate-blender-animation-workflow/source/extras/")
pkg = types.ModuleType("asbpkg"); pkg.__path__ = [BASE]
sys.modules["asbpkg"] = pkg
for _mod in ("swing_profiles", "auto_swing_bones"):
    _spec = importlib.util.spec_from_file_location("asbpkg." + _mod, BASE + _mod + ".py")
    _m = importlib.util.module_from_spec(_spec)
    sys.modules["asbpkg." + _mod] = _m
    _spec.loader.exec_module(_m)
A = sys.modules["asbpkg.auto_swing_bones"]

fails = []
def eq(got, want, label):
    if got != want:
        fails.append(f"  {label}\n     got  {got!r}\n     want {want!r}")

# --- classification: the Shigaraki rig's modded names ---
eq(A.classify_bone("S_AC_LB_hair_A"), ("Hair","LB"), "modded hair, direction token")
eq(A.classify_bone("S_AC_BT_hair_B_null"), ("Hair","BT"), "null terminal still classifies")
eq(A.classify_bone("AC_F_scarf_A"), ("Scarf","F"), "unprefixed scarf")
eq(A.classify_bone("AC_scarf"), ("Scarf",""), "scarf hub, no direction")
# --- vanilla / Smash style ---
eq(A.classify_bone("S_HairLB1"), ("Hair","LB"), "vanilla camel + index")
eq(A.classify_bone("S_MantleC5_null"), ("Mantle","C"), "vanilla mantle terminal")
eq(A.classify_bone("S_HairB2_null"), ("Hair","B"), "vanilla hair terminal")
eq(A.classify_bone("S_ShirttailFL1"), ("ShirtTail","FL"), "shirttail beats tail")
eq(A.classify_bone("S_ArmHairBL1"), ("ArmHair","BL"), "armhair beats hair")
eq(A.classify_bone("s_hairlb1"), ("Hair","LB"), "lowercase fused direction")
eq(A.classify_bone("S_Belt1"), ("Belt",""), "no direction")
eq(A.classify_bone("S_HeadbandL3"), ("Headband","L"), "headband")
# --- non-candidates ---
eq(A.classify_bone("Head"), (None,""), "plain bone rejected")
eq(A.classify_bone("ClavicleL"), (None,""), "clavicle rejected")
eq(A.classify_bone("Trans"), (None,""), "trans rejected")
eq(A.classify_bone("FingerL11"), (None,""), "finger rejected")

# --- generated names ---
eq(A.swing_bone_name("Hair","LB",1,False), "S_HairLB1", "name root")
eq(A.swing_bone_name("Hair","LB",5,True), "S_HairLB5_null", "name terminal")
eq(A.swing_bone_name("Belt","",2,True), "S_Belt2_null", "name no direction")

# --- parameters: shape of the ramp ---
p0 = A.parameters_for("Hair", 0, 4)
p3 = A.parameters_for("Hair", 3, 4)
if not p0["goal_strength"] > p3["goal_strength"]:
    fails.append("  goal_strength must fall root->tip")
if not p0["wind_affect"] < p3["wind_affect"]:
    fails.append("  wind_affect must climb root->tip")
eq(p0["ground_hit"], False, "ground_hit off at root")
eq(p3["ground_hit"], True,  "ground_hit on at tip")
eq(A.parameters_for("Hair",0,1)["ground_hit"], False, "single-bone chain leaves ground_hit off")
# unknown part falls back rather than raising
if A.parameters_for("Nonsense",0,2).get("air_resistance") is None:
    fails.append("  unknown part did not fall back")
# every profile row must carry every field
for part, lengths in A.SWING_PROFILES.items():
    for n, rows in lengths.items():
        if len(rows) != n:
            fails.append(f"  SWING_PROFILES[{part}][{n}] has {len(rows)} rows")
        for r in rows:
            if len(r) != len(A._PROFILE_FIELDS):
                fails.append(f"  SWING_PROFILES[{part}][{n}] row width {len(r)}")

# --- proposed names / _null handling ---
class B:
    def __init__(s,n): s.name=n
c = A.ProposedChain([B("S_AC_LB_hair_A"),B("S_AC_LB_hair_B"),B("S_AC_LB_hair_D_null")],"Hair","LB")
eq(c.needs_new_null(), False, "existing _null detected")
eq(c.proposed_names(False), ["S_HairLB1","S_HairLB2","S_HairLB3_null"], "reuses the null slot")
c2 = A.ProposedChain([B("AC_F_scarf_A"),B("AC_F_scarf_B")],"Scarf","F")
eq(c2.needs_new_null(), True, "missing _null detected")
eq(c2.proposed_names(True), ["S_ScarfF1","S_ScarfF2","S_ScarfF3_null"],
   "appends a null so no existing bone loses its simulation")

print("FAILURES:", len(fails))
for f in fails: print(f)
print("RESULT:", "PASS" if not fails else "FAIL")
