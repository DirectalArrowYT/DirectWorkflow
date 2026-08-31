"""Rig Helper - turn an imported Smash rig into something animatable.

WHY A SEPARATE ARMATURE. export_model.make_skel() walks every bone on the
armature it exports. Bones that aren't in the vanilla .nusktb are only kept
when their name starts with H_ or S_, so anything else is silently dropped in
the ORDER_* export modes and written into the skeleton in NO_LINK mode.
Neither is what you want from a control bone, and nothing in the export path
reads `use_deform`. Putting the controls on their own armature object sidesteps
the question entirely: the exporter never sees them, in any mode.

The control rig drives the Smash rig through drivers, so the Smash rig's pose
bones stay the things that actually carry the animation. Bake before export as
usual - the drivers only author the pose, they aren't part of it.

HAND CONTROLS. Smash names finger bones Finger{L,R}{finger}{segment}, where
finger is 1=index 2=middle 3=ring 4=pinky 5=thumb and segment is 1-3 from the
knuckle out (source/exo/exo_bone_align.py documents the pattern, and
expy_kit/rig_mapping/presets/SmashUltimate.py pins down which digit is which).

Each hand control carries six custom properties:

    curl      master open/close, 0 = flat, 1 = fully closed
    thumb     per-finger offsets added on top of the master, -1..1
    index     so you can straighten one finger out of a closed fist
    middle    or curl one further than the rest
    ring
    pinky

A driver on each finger bone's euler rotation reads (curl + <finger>) and
scales it by max_curl_degrees. Segment 1 gets slightly less rotation than 2
and 3, which is roughly how a real hand closes.
"""

import math

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty
from bpy.types import Operator, Panel

from ..blender_compat import assign_bone_to_collection, ensure_bone_collection

CTRL_RIG_SUFFIX = "_AnimRig"
CTRL_COLLECTION = "Hand Controls"

# name -> the digit number Smash uses in Finger{side}{digit}{segment}
FINGERS = (
    ("thumb", 5),
    ("index", 1),
    ("middle", 2),
    ("ring", 3),
    ("pinky", 4),
)
SEGMENTS = (1, 2, 3)
# Segment 1 is the knuckle and travels a little less than the two outer joints.
SEGMENT_WEIGHTS = {1: 0.8, 2: 1.0, 3: 1.0}

THUMB_DIGIT = 5

# How far each digit fans at spread = 1. Index and pinky swing outward hardest,
# the middle pair barely move, and the thumb pulls away from the palm.
SPREAD_FACTORS = {1: 1.0, 2: 0.35, 3: -0.35, 4: -1.0, THUMB_DIGIT: 0.8}

AXIS_INDEX = {'X': 0, 'Y': 1, 'Z': 2}


def hand_control_name(side):
    return f"CTRL_Hand_{side}"


def finger_bone_name(side, digit, segment):
    return f"Finger{side}{digit}{segment}"


def control_rig_name(source_arma):
    return f"{source_arma.name}{CTRL_RIG_SUFFIX}"


def find_control_rig(source_arma):
    return bpy.data.objects.get(control_rig_name(source_arma))


def has_any_finger_bones(arma):
    bones = arma.data.bones
    return any(
        finger_bone_name(side, digit, segment) in bones
        for side in ("L", "R")
        for _label, digit in FINGERS
        for segment in SEGMENTS
    )


def hand_across_vector(source, side):
    """Direction along the knuckles, index -> pinky, in armature space.

    Fingers close by rotating about this line, so it's the bend axis expressed
    in a space we can actually measure off the rest pose.
    """
    bones = source.data.bones
    index = bones.get(finger_bone_name(side, 1, 1))
    pinky = bones.get(finger_bone_name(side, 4, 1))
    if index is not None and pinky is not None:
        across = pinky.head_local - index.head_local
        if across.length > 1e-6:
            return across.normalized()

    # A rig missing the pinky still gives a usable line from index to middle.
    middle = bones.get(finger_bone_name(side, 2, 1))
    if index is not None and middle is not None:
        across = middle.head_local - index.head_local
        if across.length > 1e-6:
            return across.normalized()
    return None


def finger_bend_axis(source, side, digit, across):
    """Armature-space axis this digit rotates about when it closes.

    A finger's three segments lie in the plane it bends through, so the normal
    of that plane IS the bend axis - and it's measured from the digit itself
    rather than assumed from the hand. That matters for the thumb, which
    opposes the other fingers and does not share their axis.

    Smash rest poses usually carry a little curl, which is what makes the
    cross product well conditioned. When a digit is modelled dead straight
    there is no plane to read, so fall back: the thumb turns about the palm
    normal, everything else about the knuckle line.
    """
    bones = source.data.bones
    segments = [bones.get(finger_bone_name(side, digit, s)) for s in SEGMENTS]

    if all(segments):
        first = segments[1].head_local - segments[0].head_local
        second = segments[2].head_local - segments[1].head_local
        if first.length > 1e-6 and second.length > 1e-6:
            normal = first.normalized().cross(second.normalized())
            # sin(5 deg) - below this the chain is effectively straight and the
            # normal is just noise.
            if normal.length > 0.087:
                return normal.normalized()

    root = segments[0]
    if digit == THUMB_DIGIT and root is not None and across is not None:
        along = root.tail_local - root.head_local
        if along.length > 1e-6:
            palm_normal = across.cross(along.normalized())
            if palm_normal.length > 1e-6:
                return palm_normal.normalized()

    return across


def axis_from_vector(bone, vector, exclude=None):
    """(axis index, sign) for the bone-local axis closest to `vector`."""
    local = bone.matrix_local.to_3x3().inverted() @ vector
    components = {0: local.x, 1: local.y, 2: local.z}
    if exclude is not None:
        components.pop(exclude, None)
    axis = max(components, key=lambda i: abs(components[i]))
    return axis, (1.0 if components[axis] >= 0.0 else -1.0)


def detect_bone_curl_axis(bone, bend_vector):
    """Curl axis, restricted to X or Z since local Y runs down the bone."""
    return axis_from_vector(bone, bend_vector, exclude=1)


def define_slider(pose_bone, name, default, soft_min, soft_max, description):
    """Custom property + its UI range, across the 3.x/4.x id_properties API."""
    pose_bone[name] = default
    try:
        ui = pose_bone.id_properties_ui(name)
        ui.update(min=soft_min, max=soft_max, soft_min=soft_min,
                  soft_max=soft_max, default=default, description=description)
    except AttributeError:
        # Blender 2.9x kept the ranges in a parallel "_RNA_UI" dict.
        rna_ui = pose_bone.get('_RNA_UI')
        if rna_ui is None:
            pose_bone['_RNA_UI'] = rna_ui = {}
        rna_ui[name] = {
            "min": soft_min, "max": soft_max,
            "soft_min": soft_min, "soft_max": soft_max,
            "description": description,
        }


class SUB_OP_build_hand_control_rig(Operator):
    """Build a separate control armature that drives this rig's fingers"""
    bl_idname = "sub.build_hand_control_rig"
    bl_label = "Build Hand Control Rig"
    bl_options = {'REGISTER', 'UNDO'}

    curl_axis: EnumProperty(
        name="Curl Axis",
        description="Local rotation axis the finger joints bend on. Smash rigs vary; if the fingers splay sideways instead of closing, try another axis",
        items=(
            ('AUTO', 'Auto', 'Work the axis out per bone from the rest pose'),
            ('X', 'X', 'Bend around local X'),
            ('Y', 'Y', 'Bend around local Y'),
            ('Z', 'Z', 'Bend around local Z'),
        ),
        default='AUTO',
    )
    max_curl_degrees: FloatProperty(
        name="Max Curl",
        description="Rotation applied to a joint at curl = 1",
        default=90.0, min=0.0, max=180.0,
    )
    invert_curl: BoolProperty(
        name="Invert Curl",
        description="Flip the direction of the bend if the fingers open instead of closing",
        default=False,
    )
    max_spread_degrees: FloatProperty(
        name="Max Spread",
        description="Rotation applied to a knuckle at spread = 1",
        default=12.0, min=0.0, max=90.0,
    )
    create_ik_bones: BoolProperty(
        name="Also Create IK Bones",
        description="Run the arm/leg IK generator on the Smash rig as well. That builds its own bones on the Smash armature and is unrelated to this control rig",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == 'ARMATURE' and context.mode == 'OBJECT'

    def invoke(self, context, _event):
        # Without this the options only exist in the F9 redo panel, which is
        # easy to miss entirely.
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        source = context.object

        if not has_any_finger_bones(source):
            self.report({'ERROR'},
                        f"'{source.name}' has no Finger* bones - is this a Smash rig?")
            return {'CANCELLED'}

        if self.create_ik_bones:
            # Runs against context.object, which is still the Smash rig here.
            try:
                bpy.ops.sub.create_ik_bones()
            except Exception as e:
                self.report({'WARNING'}, f"IK bone creation failed: {e}")

        ctrl = self.build_control_armature(context, source)
        self.add_hand_controls(context, ctrl, source)
        self.drive_fingers(ctrl, source)

        context.view_layer.objects.active = ctrl
        self.report({'INFO'},
                    f"Built '{ctrl.name}'. Select its hand bones and use the sidebar "
                    f"Item tab to reach the curl sliders.")
        return {'FINISHED'}

    def build_control_armature(self, context, source):
        existing = find_control_rig(source)
        if existing is not None:
            return existing

        arma_data = bpy.data.armatures.new(control_rig_name(source))
        ctrl = bpy.data.objects.new(control_rig_name(source), arma_data)

        # Same collection and same transform as the rig it drives, so the
        # controls sit where the character actually is.
        for collection in source.users_collection:
            collection.objects.link(ctrl)
        if not ctrl.users_collection:
            context.scene.collection.objects.link(ctrl)
        ctrl.matrix_world = source.matrix_world.copy()
        ctrl.show_in_front = True

        ensure_bone_collection(arma_data, CTRL_COLLECTION)
        return ctrl

    def add_hand_controls(self, context, ctrl, source):
        context.view_layer.objects.active = ctrl
        bpy.ops.object.mode_set(mode='EDIT')

        for side in ("L", "R"):
            name = hand_control_name(side)
            if name in ctrl.data.edit_bones:
                continue

            anchor = source.data.bones.get(finger_bone_name(side, 1, 1))
            if anchor is None:
                continue

            # Sit the control just off the knuckles, pointing the same way the
            # index finger does so it reads as belonging to that hand.
            head = anchor.head_local.copy()
            direction = (anchor.tail_local - anchor.head_local)
            length = max(direction.length, 0.01)
            offset = direction.normalized() * length * 2.0

            bone = ctrl.data.edit_bones.new(name)
            bone.head = head + offset
            bone.tail = head + offset * 2.0
            bone.use_deform = False

        bpy.ops.object.mode_set(mode='OBJECT')

        for side in ("L", "R"):
            pose_bone = ctrl.pose.bones.get(hand_control_name(side))
            if pose_bone is None:
                continue

            collection = ensure_bone_collection(ctrl.data, CTRL_COLLECTION)
            if collection is not None:
                assign_bone_to_collection(collection, ctrl.data.bones[pose_bone.name])

            pose_bone.rotation_mode = 'XYZ'
            # Range runs both ways so a rig whose fingers bend the wrong
            # direction can just be driven negative, no rebuild needed.
            define_slider(pose_bone, 'curl', 0.0, -1.0, 1.0,
                          "Open (0) to closed (1) for the whole hand")
            define_slider(pose_bone, 'spread', 0.0, -1.0, 1.0,
                          "Fan the fingers apart (1) or squeeze them together (-1)")
            for label, _digit in FINGERS:
                define_slider(pose_bone, label, 0.0, -1.0, 1.0,
                              f"Offset added to the master curl for the {label}")

    def drive_fingers(self, ctrl, source):
        user_sign = -1.0 if self.invert_curl else 1.0
        max_curl = math.radians(self.max_curl_degrees)
        max_spread = math.radians(self.max_spread_degrees)

        for side in ("L", "R"):
            control = hand_control_name(side)
            if control not in ctrl.pose.bones:
                continue

            across = hand_across_vector(source, side)
            if across is None and self.curl_axis == 'AUTO':
                self.report({'WARNING'},
                            f"Couldn't measure the {side} hand's knuckle line; "
                            f"falling back to local X.")

            for label, digit in FINGERS:
                bend_vector = (finger_bend_axis(source, side, digit, across)
                               if self.curl_axis == 'AUTO' else None)

                for segment in SEGMENTS:
                    bone_name = finger_bone_name(side, digit, segment)
                    pose_bone = source.pose.bones.get(bone_name)
                    if pose_bone is None:
                        continue

                    bone = source.data.bones[bone_name]

                    if bend_vector is not None:
                        curl_axis, curl_sign = detect_bone_curl_axis(bone, bend_vector)
                    elif self.curl_axis == 'AUTO':
                        curl_axis, curl_sign = 0, 1.0
                    else:
                        curl_axis, curl_sign = AXIS_INDEX[self.curl_axis], 1.0

                    # Drivers can't touch a quaternion channel usefully, and
                    # imported Smash rigs come in as quaternion.
                    pose_bone.rotation_mode = 'XYZ'

                    data_path = f'pose.bones["{bone_name}"].rotation_euler'
                    source.animation_data_create()
                    # Clear every axis, not just the ones being written - a
                    # rebuild on a different axis would otherwise leave the
                    # previous driver behind still bending the finger.
                    for stale in range(3):
                        try:
                            source.driver_remove(data_path, stale)
                        except Exception:
                            pass

                    weight = (SEGMENT_WEIGHTS[segment] * max_curl
                              * curl_sign * user_sign)
                    self.add_driver(
                        source, ctrl, control, data_path, curl_axis,
                        f'(curl + offset) * {weight:.6f}',
                        (('curl', 'curl'), ('offset', label)))

                    # Splay happens at the knuckle only; the outer joints of a
                    # real finger have no sideways freedom.
                    if segment != 1 or bend_vector is None:
                        continue

                    along = bone.tail_local - bone.head_local
                    if along.length <= 1e-6:
                        continue
                    spread_vector = bend_vector.cross(along.normalized())
                    if spread_vector.length <= 1e-6:
                        continue

                    spread_axis, spread_sign = axis_from_vector(
                        bone, spread_vector.normalized(), exclude=curl_axis)
                    factor = SPREAD_FACTORS.get(digit, 0.0)
                    if factor == 0.0:
                        continue

                    spread_weight = max_spread * factor * spread_sign
                    self.add_driver(
                        source, ctrl, control, data_path, spread_axis,
                        f'spread * {spread_weight:.6f}',
                        (('spread', 'spread'),))

    @staticmethod
    def add_driver(source, ctrl, control, data_path, axis, expression, variables):
        fcurve = source.driver_add(data_path, axis)
        driver = fcurve.driver
        driver.type = 'SCRIPTED'
        for var_name, prop in variables:
            var = driver.variables.new()
            var.name = var_name
            var.type = 'SINGLE_PROP'
            target = var.targets[0]
            target.id = ctrl
            target.data_path = f'pose.bones["{control}"]["{prop}"]'
        driver.expression = expression


class SUB_OP_remove_hand_control_rig(Operator):
    """Delete the control armature and the finger drivers it feeds"""
    bl_idname = "sub.remove_hand_control_rig"
    bl_label = "Remove Hand Control Rig"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == 'ARMATURE' and context.mode == 'OBJECT'

    def execute(self, context):
        source = context.object
        # Selecting the control rig itself is the natural thing to do, so
        # accept either side of the pair.
        if source.name.endswith(CTRL_RIG_SUFFIX):
            driven_name = source.name[:-len(CTRL_RIG_SUFFIX)]
            ctrl, source = source, bpy.data.objects.get(driven_name, source)
        else:
            ctrl = find_control_rig(source)

        removed = 0
        for side in ("L", "R"):
            for _label, digit in FINGERS:
                for segment in SEGMENTS:
                    bone_name = finger_bone_name(side, digit, segment)
                    if bone_name not in source.pose.bones:
                        continue
                    data_path = f'pose.bones["{bone_name}"].rotation_euler'
                    for axis in range(3):
                        try:
                            if source.driver_remove(data_path, axis):
                                removed += 1
                        except Exception:
                            pass

        if ctrl is not None:
            bpy.data.objects.remove(ctrl, do_unlink=True)

        context.view_layer.objects.active = source
        self.report({'INFO'}, f"Removed the control rig and {removed} finger driver(s).")
        return {'FINISHED'}


class SUB_PT_rig_helper(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Rig Helper'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        obj = context.object

        if obj is None or obj.type != 'ARMATURE':
            layout.label(text="Select an armature.", icon='INFO')
            return

        if obj.name.endswith(CTRL_RIG_SUFFIX):
            layout.label(text="This is a control rig.", icon='CON_ARMATURE')
            layout.operator(SUB_OP_remove_hand_control_rig.bl_idname, icon='TRASH')
            pose_bone = context.active_pose_bone
            if pose_bone is not None and 'curl' in pose_bone:
                column = layout.column(align=True)
                column.prop(pose_bone, '["curl"]', text="Curl")
                if 'spread' in pose_bone:
                    column.prop(pose_bone, '["spread"]', text="Spread")
                column.separator()
                for label, _digit in FINGERS:
                    if label in pose_bone:
                        column.prop(pose_bone, f'["{label}"]', text=label.title())
            else:
                layout.label(text="Select a hand control in Pose Mode.", icon='INFO')
            return

        if not has_any_finger_bones(obj):
            layout.label(text="No Finger* bones on this armature.", icon='ERROR')
            return

        if find_control_rig(obj) is None:
            layout.operator(SUB_OP_build_hand_control_rig.bl_idname, icon='ADD')
        else:
            layout.label(text=f"Driven by {control_rig_name(obj)}", icon='CHECKMARK')
            layout.operator(SUB_OP_remove_hand_control_rig.bl_idname, icon='TRASH')
