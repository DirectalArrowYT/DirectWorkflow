import bpy
from bpy.types import Operator

from ..anim.fcurve_compat import get_fcurves, new_fcurve, remove_fcurve


def _resolve(operator, context):
    """(armature, action, hip_bone, trans_bone) or None, reporting what was missing.

    Shared by the Trans -> Hip operators. The Hip -> Trans pair above each carry their own copy
    of this; they are left as they are rather than rewritten underneath working code.
    """
    armature = context.active_object
    if not armature or armature.type != 'ARMATURE':
        operator.report({'ERROR'}, "No armature selected")
        return None

    hip_bone = None
    trans_bone = None
    for bone in armature.pose.bones:
        if "hip" in bone.name.lower():
            hip_bone = bone
        if "trans" in bone.name.lower():
            trans_bone = bone

    if not hip_bone:
        operator.report({'ERROR'}, "Hip bone not found in armature")
        return None
    if not trans_bone:
        operator.report({'ERROR'}, "Trans bone not found in armature")
        return None
    if not armature.animation_data or not armature.animation_data.action:
        operator.report({'ERROR'}, "No animation data found for armature")
        return None

    return armature, armature.animation_data.action, hip_bone, trans_bone


def _find_fcurve(action, path, index):
    for fc in get_fcurves(action):
        if fc.data_path == path and fc.array_index == index:
            return fc
    return None


def _move_channel(action, source_fc, target_path, target_index, convert):
    """Move every key from `source_fc` onto another channel, through `convert`.

    Returns the number of keys moved. The source curve is left in place for the caller to
    remove, so a failure part-way cannot lose the animation.
    """
    keys = [(kp.co[0], kp.co[1], kp.interpolation) for kp in source_fc.keyframe_points]
    if not keys:
        return 0

    target = _find_fcurve(action, target_path, target_index)
    if target is None:
        target = new_fcurve(action, target_path, index=target_index)
    else:
        target.keyframe_points.clear()

    for frame, value, interpolation in keys:
        kp = target.keyframe_points.insert(frame, convert(value))
        kp.interpolation = interpolation
    return len(keys)


def _zero_channel(bone, index, frame=1):
    bone.location[index] = 0
    bone.keyframe_insert(data_path="location", index=index, frame=frame, group=bone.name)


def _redraw_graph_editors(context):
    for area in context.screen.areas:
        if area.type == 'GRAPH_EDITOR':
            area.tag_redraw()

class SUB_OP_transfer_hip_animation(Operator):
    bl_idname = "sub.transfer_hip_animation"
    bl_label = "Transfer Hip Animation"
    bl_description = "Transfer X motion from Hip to Z of Trans bone, zero out Hip X"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'POSE' or context.mode == 'OBJECT') and context.active_object and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        armature = context.active_object
        if not armature or armature.type != 'ARMATURE':
            self.report({'ERROR'}, "No armature selected")
            return {'CANCELLED'}

        # Find hip and trans bones
        hip_bone = None
        trans_bone = None

        for bone in armature.pose.bones:
            if "hip" in bone.name.lower():
                hip_bone = bone
            if "trans" in bone.name.lower():
                trans_bone = bone

        if not hip_bone:
            self.report({'ERROR'}, "Hip bone not found in armature")
            return {'CANCELLED'}

        if not trans_bone:
            self.report({'ERROR'}, "Trans bone not found in armature")
            return {'CANCELLED'}

        # Get animation data
        if not armature.animation_data or not armature.animation_data.action:
            self.report({'ERROR'}, "No animation data found for armature")
            return {'CANCELLED'}

        action = armature.animation_data.action

        # Find hip X location fcurve
        hip_x_fcurve = None
        hip_path = f'pose.bones["{hip_bone.name}"].location'

        for fc in get_fcurves(action):
            if fc.data_path == hip_path and fc.array_index == 0:  # X location
                hip_x_fcurve = fc
                break

        if not hip_x_fcurve:
            self.report({'ERROR'}, "No X location keyframes found for Hip bone")
            return {'CANCELLED'}

        # Get cursor value for mirroring
        cursor_value = context.scene.cursor.location.x

        # Store hip X keyframes
        keyframes = []
        first_value = None

        for kp in hip_x_fcurve.keyframe_points:
            if first_value is None:
                first_value = kp.co[1]
            keyframes.append((kp.co[0], kp.co[1], kp.interpolation))

        if not keyframes:
            self.report({'ERROR'}, "No keyframes found on Hip bone X location")
            return {'CANCELLED'}

        # Shift values so first keyframe is at 0m
        for i in range(len(keyframes)):
            keyframes[i] = (keyframes[i][0], keyframes[i][1] - first_value, keyframes[i][2])

        # Create or get Trans Z fcurve
        trans_z_fcurve = None
        trans_path = f'pose.bones["{trans_bone.name}"].location'

        for fc in get_fcurves(action):
            if fc.data_path == trans_path and fc.array_index == 2:  # Z location
                trans_z_fcurve = fc
                break

        if not trans_z_fcurve:
            trans_z_fcurve = new_fcurve(action, trans_path, index=2)
        else:
            # Clear existing keyframes
            trans_z_fcurve.keyframe_points.clear()

        # Copy keyframes to Trans Z and mirror them over cursor value
        for frame, value, interpolation in keyframes:
            # Mirror value over cursor
            mirrored_value = 2 * cursor_value - value

            kp = trans_z_fcurve.keyframe_points.insert(frame, mirrored_value)
            kp.interpolation = interpolation

        # Remove hip X keyframes
        remove_fcurve(action, hip_x_fcurve)

        # Set hip X to 0 and keyframe it on frame 1
        hip_bone.location[0] = 0
        hip_bone.keyframe_insert(data_path="location", index=0, frame=1, group=hip_bone.name)

        # Update the view
        for area in context.screen.areas:
            if area.type == 'GRAPH_EDITOR':
                area.tag_redraw()

        self.report({'INFO'}, "Hip animation transferred to Trans bone Z")
        return {'FINISHED'}


class SUB_OP_transfer_hip_jump_animation(Operator):
    bl_idname = "sub.transfer_hip_jump_animation"
    bl_label = "Transfer Hip Jump Animation"
    bl_description = "Transfer Hip Y motion to Trans X (vertical/jump), zeroing Hip Y"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'POSE' or context.mode == 'OBJECT') and context.active_object and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        armature = context.active_object
        if not armature or armature.type != 'ARMATURE':
            self.report({'ERROR'}, "No armature selected")
            return {'CANCELLED'}

        # Find hip and trans bones
        hip_bone = None
        trans_bone = None

        for bone in armature.pose.bones:
            if "hip" in bone.name.lower():
                hip_bone = bone
            if "trans" in bone.name.lower():
                trans_bone = bone

        if not hip_bone:
            self.report({'ERROR'}, "Hip bone not found in armature")
            return {'CANCELLED'}
        if not trans_bone:
            self.report({'ERROR'}, "Trans bone not found in armature")
            return {'CANCELLED'}

        # Get animation data
        if not armature.animation_data or not armature.animation_data.action:
            self.report({'ERROR'}, "No animation data found for armature")
            return {'CANCELLED'}

        action = armature.animation_data.action
        hip_path = f'pose.bones["{hip_bone.name}"].location'
        trans_path = f'pose.bones["{trans_bone.name}"].location'

        # Find hip Y location fcurve
        hip_y_fcurve = None
        for fc in get_fcurves(action):
            if fc.data_path == hip_path and fc.array_index == 1:  # Y location
                hip_y_fcurve = fc
                break

        if not hip_y_fcurve:
            self.report({'ERROR'}, "No Y location keyframes found for Hip bone")
            return {'CANCELLED'}

        # Store hip Y keyframes
        keyframes = []
        first_y = None
        for kp in hip_y_fcurve.keyframe_points:
            if first_y is None:
                first_y = kp.co[1]
            keyframes.append((kp.co[0], kp.co[1], kp.interpolation))

        if not keyframes:
            self.report({'ERROR'}, "No keyframes found on Hip bone Y location")
            return {'CANCELLED'}

        # Shift values so first keyframe is at 0
        for i in range(len(keyframes)):
            keyframes[i] = (keyframes[i][0], keyframes[i][1] - first_y, keyframes[i][2])

        # Create or get Trans X fcurve
        trans_x_fcurve = None
        for fc in get_fcurves(action):
            if fc.data_path == trans_path and fc.array_index == 0:  # X location
                trans_x_fcurve = fc
                break
        if not trans_x_fcurve:
            trans_x_fcurve = new_fcurve(action, trans_path, index=0)
        else:
            trans_x_fcurve.keyframe_points.clear()

        for frame, value, interpolation in keyframes:
            kp = trans_x_fcurve.keyframe_points.insert(frame, -value)
            kp.interpolation = interpolation

        # Remove hip Y keyframes
        remove_fcurve(action, hip_y_fcurve)

        # Set hip Y to 0 and keyframe it on frame 1
        hip_bone.location[1] = 0
        hip_bone.keyframe_insert(data_path="location", index=1, frame=1, group=hip_bone.name)

        # Update the view
        for area in context.screen.areas:
            if area.type == 'GRAPH_EDITOR':
                area.tag_redraw()

        self.report({'INFO'}, "Hip Y transferred to Trans X (jump/vertical)")
        return {'FINISHED'}


class SUB_OP_transfer_trans_animation_to_hip(Operator):
    bl_idname = "sub.transfer_trans_animation_to_hip"
    bl_label = "Transfer Trans Animation to Hip"
    bl_description = (
        "Reverse of Transfer Hip Animation: move Z motion from Trans back to X of Hip, "
        "mirrored over the 3D cursor, and zero out Trans Z"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.mode in {'POSE', 'OBJECT'}
            and context.active_object
            and context.active_object.type == 'ARMATURE'
        )

    def execute(self, context):
        resolved = _resolve(self, context)
        if resolved is None:
            return {'CANCELLED'}
        _armature, action, hip_bone, trans_bone = resolved

        trans_path = f'pose.bones["{trans_bone.name}"].location'
        hip_path = f'pose.bones["{hip_bone.name}"].location'

        trans_z = _find_fcurve(action, trans_path, 2)
        if not trans_z:
            self.report({'ERROR'}, "No Z location keyframes found for Trans bone")
            return {'CANCELLED'}

        # Mirroring over the cursor is its own inverse: 2C - (2C - v) is v. So the forward
        # transfer's formula reverses it unchanged, and the cursor has to be where it was when
        # the animation went the other way for a round trip to land back on itself.
        cursor = context.scene.cursor.location.x
        moved = _move_channel(action, trans_z, hip_path, 0, lambda v: 2 * cursor - v)
        if moved == 0:
            self.report({'ERROR'}, "No keyframes found on Trans bone Z location")
            return {'CANCELLED'}

        # Deliberately no re-zeroing to the first key. The forward transfer already shifted the
        # curve so its first frame sat at zero, and that original offset is not recorded
        # anywhere -- so it cannot be restored, and shifting again would throw away a Trans
        # offset the animator put there on purpose.
        remove_fcurve(action, trans_z)
        _zero_channel(trans_bone, 2)

        _redraw_graph_editors(context)
        self.report({'INFO'}, f"Trans Z transferred to Hip X ({moved} keyframes)")
        return {'FINISHED'}


class SUB_OP_transfer_trans_jump_animation_to_hip(Operator):
    bl_idname = "sub.transfer_trans_jump_animation_to_hip"
    bl_label = "Transfer Trans Jump Animation to Hip"
    bl_description = (
        "Reverse of Transfer Hip Jump Animation: move X motion from Trans back to Y of Hip "
        "(vertical/jump), and zero out Trans X"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.mode in {'POSE', 'OBJECT'}
            and context.active_object
            and context.active_object.type == 'ARMATURE'
        )

    def execute(self, context):
        resolved = _resolve(self, context)
        if resolved is None:
            return {'CANCELLED'}
        _armature, action, hip_bone, trans_bone = resolved

        trans_path = f'pose.bones["{trans_bone.name}"].location'
        hip_path = f'pose.bones["{hip_bone.name}"].location'

        trans_x = _find_fcurve(action, trans_path, 0)
        if not trans_x:
            self.report({'ERROR'}, "No X location keyframes found for Trans bone")
            return {'CANCELLED'}

        # Negation is its own inverse, so this is the forward transfer's formula unchanged.
        moved = _move_channel(action, trans_x, hip_path, 1, lambda v: -v)
        if moved == 0:
            self.report({'ERROR'}, "No keyframes found on Trans bone X location")
            return {'CANCELLED'}

        remove_fcurve(action, trans_x)
        _zero_channel(trans_bone, 0)

        _redraw_graph_editors(context)
        self.report({'INFO'}, f"Trans X transferred to Hip Y ({moved} keyframes)")
        return {'FINISHED'}
