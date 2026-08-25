"""Mirror bones across X for Smash Ultimate rigs.

Two things Blender's own Symmetrize can't do for these rigs:

1. NAMING. Smash bones carry a bare side letter with no separator -
   ClavicleR, HandL, FingerL10, ToeR - and Blender's flipper only
   understands Foo.L / Foo_R style names, so it finds no pairs at all.

2. ROLL. A geometric mirror is NOT what Smash uses. Measured on an
   untouched vanilla import (source/model/import_model.py's output) the
   convention is:

       roll_mirrored = 180 deg - roll_source

   not the -roll_source a plain mirror produces. That is a pure mirror plus
   an extra 180 deg twist about the bone's own axis. On the vanilla rig 16
   of 17 L/R pairs follow it, and all 7 of the pairs whose head/tail are
   exact mirrors follow it - the cases where roll is unambiguous.

   Getting this wrong does not show up in the rest pose. It shows up once
   the rig is animated or an animation is retargeted onto it, because every
   mirrored bone's local axes are rolled 180 deg from what the game and the
   vanilla animations expect.
"""

import math
import re

import bpy
from bpy.props import EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator


# --------------------------------------------------------------------------- #
# name flipping - tuned to Smash's bare-suffix bones. First matching rule wins.
# --------------------------------------------------------------------------- #

# Pairs no positional rule can catch, because the side letter sits in the
# middle of the name. Matched as substrings, both directions.
DEFAULT_EXPLICIT = [
    ("SHair_R", "SHair_L"),
]


def _swap(c):
    return "L" if c in "Rr" else "R"


# Each rule flips ONLY the side token and leaves internal letters alone - the
# "L" in LLip (Lower Lip) and the "U" in ULip must survive untouched.
_RULES = [
    # prefix token with a separator:  L_UpperArm_1 , R-Foo , "L Foo"
    (re.compile(r"^([LR])([ _\-.])"), lambda m: _swap(m.group(1)) + m.group(2)),
    # suffix token with a separator (+ optional Blender .001 duplicate):
    #   Foo_L , Foo.R , Foo-L , "Foo R" , Foo_L.002
    (re.compile(r"([ _\-.])([LR])(\.\d+)?$"),
     lambda m: m.group(1) + _swap(m.group(2)) + (m.group(3) or "")),
    # bare trailing letter after a lowercase/digit (+ optional .001):
    #   ClavicleR , HandL , EyeL , Pinky3R , EyelidLowL.001
    (re.compile(r"([a-z0-9])([LR])(\.\d+)?$"),
     lambda m: m.group(1) + _swap(m.group(2)) + (m.group(3) or "")),
    # side token immediately before a trailing number:  FingerL10 , LLipR2
    (re.compile(r"([LR])(\d+)$"), lambda m: _swap(m.group(1)) + m.group(2)),
]


def flip_name(name, explicit=DEFAULT_EXPLICIT):
    """Opposite-side name, or the same name when it has no side token."""
    for a, b in explicit:
        if a and a in name:
            return name.replace(a, b)
        if b and b in name:
            return name.replace(b, a)
    for rx, fn in _RULES:
        new, n = rx.subn(fn, name)
        if n:
            return new
    return name


def parse_extra_pairs(text):
    """'SHair_R=SHair_L, Foo=Bar' -> [('SHair_R','SHair_L'), ('Foo','Bar')]."""
    pairs = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        a, b = chunk.split("=", 1)
        a, b = a.strip(), b.strip()
        if a and b:
            pairs.append((a, b))
    return pairs


# --------------------------------------------------------------------------- #
# roll conventions
# --------------------------------------------------------------------------- #

def _normalize_angle(a):
    """Wrap to (-pi, pi] so rolls stay in the range Blender shows."""
    while a <= -math.pi:
        a += 2.0 * math.pi
    while a > math.pi:
        a -= 2.0 * math.pi
    return a


def mirrored_roll(src_roll, convention):
    """Roll for the mirrored bone.

    SMASH: 180 deg - roll. Measured from vanilla Smash rigs - see module
           docstring. This is the one the game and its animations expect.
    PURE:  -roll. A plain geometric reflection, what Blender's Symmetrize
           does. Correct for ordinary rigs, wrong for Smash.
    """
    if convention == 'PURE':
        return _normalize_angle(-src_roll)
    return _normalize_angle(math.pi - src_roll)


# --------------------------------------------------------------------------- #
# core mirror pass
# --------------------------------------------------------------------------- #

def symmetrize_bones(arm_obj, source, center_eps, explicit, convention):
    """Mirror in Edit mode. Returns (created, updated, skipped) name lists."""
    prev_mode = arm_obj.mode
    if bpy.context.mode != "EDIT_ARMATURE":
        bpy.ops.object.mode_set(mode="EDIT")
    ebs = arm_obj.data.edit_bones

    if source == "SEL":
        sources = [b for b in ebs if b.select]
    elif source == "POS":
        sources = [b for b in ebs if b.head.x > center_eps]
    else:  # "NEG"
        sources = [b for b in ebs if b.head.x < -center_eps]

    created, updated, skipped = [], [], []
    dest_of = {}

    # PASS 1 - geometry
    for src in sources:
        dst_name = flip_name(src.name, explicit)
        if dst_name == src.name:
            skipped.append(src.name)
            continue
        dst = ebs.get(dst_name)
        if dst is None:
            dst = ebs.new(dst_name)
            created.append(dst_name)
        else:
            updated.append(dst_name)
        dst.use_connect = False
        h, t = src.head, src.tail
        dst.head = (-h.x, h.y, h.z)
        dst.tail = (-t.x, t.y, t.z)
        dst.roll = mirrored_roll(src.roll, convention)
        dst.use_deform = src.use_deform
        dst.envelope_distance = src.envelope_distance
        dst.head_radius = src.head_radius
        dst.tail_radius = src.tail_radius
        dest_of[dst_name] = src

    # PASS 2 - hierarchy + connect (every mirror now exists)
    for dst_name, src in dest_of.items():
        dst = ebs[dst_name]
        if src.parent is not None:
            pflip = flip_name(src.parent.name, explicit)
            dst.parent = ebs.get(pflip) or ebs.get(src.parent.name)
        dst.use_connect = src.use_connect

    if arm_obj.mode != prev_mode:
        bpy.ops.object.mode_set(mode=prev_mode)
    return created, updated, skipped


def audit_roll_convention(arm_obj, explicit, eps=1e-5):
    """Which convention does this rig's existing L/R pairs already follow?

    Only pairs whose head/tail really are exact mirrors are counted - roll is
    ambiguous when the two sides sit at different angles, and a rig posed
    asymmetrically would otherwise wash the answer out.

    Returns (n_smash, n_pure, n_comparable, n_pairs).
    """
    prev_mode = arm_obj.mode
    if bpy.context.mode != "EDIT_ARMATURE":
        bpy.ops.object.mode_set(mode="EDIT")
    ebs = arm_obj.data.edit_bones

    n_smash = n_pure = n_comparable = n_pairs = 0
    for bone in ebs:
        other_name = flip_name(bone.name, explicit)
        if other_name == bone.name or other_name not in ebs:
            continue
        if bone.head.x < 0:            # count each pair once
            continue
        other = ebs[other_name]
        n_pairs += 1
        dh = (bone.head.x + other.head.x, bone.head.y - other.head.y, bone.head.z - other.head.z)
        dt = (bone.tail.x + other.tail.x, bone.tail.y - other.tail.y, bone.tail.z - other.tail.z)
        if max(abs(v) for v in dh + dt) > eps:
            continue
        n_comparable += 1
        if abs(_normalize_angle(other.roll - mirrored_roll(bone.roll, 'SMASH'))) < 1e-3:
            n_smash += 1
        if abs(_normalize_angle(other.roll - mirrored_roll(bone.roll, 'PURE'))) < 1e-3:
            n_pure += 1

    if arm_obj.mode != prev_mode:
        bpy.ops.object.mode_set(mode=prev_mode)
    return n_smash, n_pure, n_comparable, n_pairs


# --------------------------------------------------------------------------- #
# operators
# --------------------------------------------------------------------------- #

class SUB_OT_bone_symmetrize(Operator):
    """Mirror bones across X using Smash's naming and roll convention"""
    bl_idname = "sub.bone_symmetrize"
    bl_label = "Symmetrize Bones"
    bl_description = ("Mirror bones across X. Understands Smash's bare L/R bone names and "
                      "applies the roll convention the game expects, which a plain mirror does not")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "ARMATURE"

    def execute(self, context):
        ssp = context.scene.sub_scene_properties
        arm_obj = context.active_object

        arm_obj.select_set(True)
        context.view_layer.objects.active = arm_obj

        explicit = parse_extra_pairs(ssp.bone_sym_extra_pairs) + DEFAULT_EXPLICIT
        try:
            created, updated, skipped = symmetrize_bones(
                arm_obj, ssp.bone_sym_source, ssp.bone_sym_center_eps,
                explicit, ssp.bone_sym_convention)
        except Exception as ex:
            self.report({"ERROR"}, f"Symmetrize failed: {ex}")
            return {"CANCELLED"}

        if not (created or updated):
            self.report({"WARNING"},
                        "No bones mirrored - check the source side, or your selection")
            return {"CANCELLED"}

        self.report({"INFO"},
                    f"Mirrored {len(created) + len(updated)} bones "
                    f"({len(created)} new, {len(updated)} updated, "
                    f"{len(skipped)} centre-skipped) using the "
                    f"{'Smash' if ssp.bone_sym_convention == 'SMASH' else 'pure mirror'} roll convention")
        return {"FINISHED"}


class SUB_OT_bone_symmetry_audit(Operator):
    """Report which roll convention this rig's existing L/R pairs follow"""
    bl_idname = "sub.bone_symmetry_audit"
    bl_label = "Check Rig's Roll Convention"
    bl_description = ("Compare the rig's existing L/R bone pairs against both conventions "
                      "and report which one it currently follows, without changing anything")
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "ARMATURE"

    def execute(self, context):
        ssp = context.scene.sub_scene_properties
        arm_obj = context.active_object
        explicit = parse_extra_pairs(ssp.bone_sym_extra_pairs) + DEFAULT_EXPLICIT
        try:
            n_smash, n_pure, n_comparable, n_pairs = audit_roll_convention(arm_obj, explicit)
        except Exception as ex:
            self.report({"ERROR"}, f"Audit failed: {ex}")
            return {"CANCELLED"}

        if n_comparable == 0:
            self.report({"WARNING"},
                        f"{n_pairs} L/R pair(s) found, but none have exactly mirrored "
                        "head/tail, so their roll can't be compared")
            return {"FINISHED"}

        verdict = ("Smash convention" if n_smash > n_pure else
                   "pure mirror - NOT what Smash expects" if n_pure > n_smash else
                   "inconclusive")
        self.report({"INFO"} if n_smash >= n_pure else {"WARNING"},
                    f"{arm_obj.name}: of {n_comparable} comparable pair(s) "
                    f"(out of {n_pairs}), {n_smash} match Smash (180-roll) and "
                    f"{n_pure} match a pure mirror (-roll) -> {verdict}")
        return {"FINISHED"}


classes = (
    SUB_OT_bone_symmetrize,
    SUB_OT_bone_symmetry_audit,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
