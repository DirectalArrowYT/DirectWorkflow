import bpy
from bpy.types import Operator


def _find_hip_trans(context):
    armature = context.active_object
    hip_bone = None
    trans_bone = None
    for bone in armature.pose.bones:
        if "hip" in bone.name.lower():
            hip_bone = bone
        if "trans" in bone.name.lower():
            trans_bone = bone
    return hip_bone, trans_bone


class SUB_OP_transfer_hip_animation(Operator):
    bl_idname = "sub.transfer_hip_animation"
    bl_label = "Transfer Hip Animation"
    bl_description = "Transfer Hip X motion to Trans Z (forward/side-to-side), zeroing Hip X"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'POSE' or context.mode == 'OBJECT') and context.active_object and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        armature = context.active_object
        if not armature or armature.type != 'ARMATURE':
            self.report({'ERROR'}, "No armature selected")
            return {'CANCELLED'}

        hip_bone, trans_bone = _find_hip_trans(context)

        if not hip_bone:
            self.report({'ERROR'}, "Hip bone not found in armature")
            return {'CANCELLED'}
        if not trans_bone:
            self.report({'ERROR'}, "Trans bone not found in armature")
            return {'CANCELLED'}

        if not armature.animation_data or not armature.animation_data.action:
            self.report({'ERROR'}, "No animation data found for armature")
            return {'CANCELLED'}

        action = armature.animation_data.action
        hip_path = f'pose.bones["{hip_bone.name}"].location'
        trans_path = f'pose.bones["{trans_bone.name}"].location'

        hip_x_fcurve = None
        for fc in action.fcurves:
            if fc.data_path == hip_path and fc.array_index == 0:
                hip_x_fcurve = fc
                break

        if not hip_x_fcurve:
            self.report({'ERROR'}, "No X location keyframes found for Hip bone")
            return {'CANCELLED'}

        cursor_value = context.scene.cursor.location.x

        keyframes = []
        first_x = None
        for kp in hip_x_fcurve.keyframe_points:
            if first_x is None:
                first_x = kp.co[1]
            keyframes.append((kp.co[0], kp.co[1], kp.interpolation))

        if not keyframes:
            self.report({'ERROR'}, "No keyframes found on Hip bone X location")
            return {'CANCELLED'}

        for i in range(len(keyframes)):
            keyframes[i] = (keyframes[i][0], keyframes[i][1] - first_x, keyframes[i][2])

        trans_z_fcurve = None
        for fc in action.fcurves:
            if fc.data_path == trans_path and fc.array_index == 2:
                trans_z_fcurve = fc
                break
        if not trans_z_fcurve:
            trans_z_fcurve = action.fcurves.new(trans_path, index=2)
        else:
            trans_z_fcurve.keyframe_points.clear()

        for frame, value, interpolation in keyframes:
            kp = trans_z_fcurve.keyframe_points.insert(frame, 2 * cursor_value - value)
            kp.interpolation = interpolation

        action.fcurves.remove(hip_x_fcurve)
        hip_bone.location[0] = 0
        hip_bone.keyframe_insert(data_path="location", index=0, frame=1, group=hip_bone.name)

        for area in context.screen.areas:
            if area.type == 'GRAPH_EDITOR':
                area.tag_redraw()

        self.report({'INFO'}, "Hip X transferred to Trans Z (forward)")
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

        hip_bone, trans_bone = _find_hip_trans(context)

        if not hip_bone:
            self.report({'ERROR'}, "Hip bone not found in armature")
            return {'CANCELLED'}
        if not trans_bone:
            self.report({'ERROR'}, "Trans bone not found in armature")
            return {'CANCELLED'}

        if not armature.animation_data or not armature.animation_data.action:
            self.report({'ERROR'}, "No animation data found for armature")
            return {'CANCELLED'}

        action = armature.animation_data.action
        hip_path = f'pose.bones["{hip_bone.name}"].location'
        trans_path = f'pose.bones["{trans_bone.name}"].location'

        hip_y_fcurve = None
        for fc in action.fcurves:
            if fc.data_path == hip_path and fc.array_index == 1:
                hip_y_fcurve = fc
                break

        if not hip_y_fcurve:
            self.report({'ERROR'}, "No Y location keyframes found for Hip bone")
            return {'CANCELLED'}

        keyframes = []
        first_y = None
        for kp in hip_y_fcurve.keyframe_points:
            if first_y is None:
                first_y = kp.co[1]
            keyframes.append((kp.co[0], kp.co[1], kp.interpolation))

        if not keyframes:
            self.report({'ERROR'}, "No keyframes found on Hip bone Y location")
            return {'CANCELLED'}

        for i in range(len(keyframes)):
            keyframes[i] = (keyframes[i][0], keyframes[i][1] - first_y, keyframes[i][2])

        trans_x_fcurve = None
        for fc in action.fcurves:
            if fc.data_path == trans_path and fc.array_index == 0:
                trans_x_fcurve = fc
                break
        if not trans_x_fcurve:
            trans_x_fcurve = action.fcurves.new(trans_path, index=0)
        else:
            trans_x_fcurve.keyframe_points.clear()

        for frame, value, interpolation in keyframes:
            kp = trans_x_fcurve.keyframe_points.insert(frame, -value)
            kp.interpolation = interpolation

        action.fcurves.remove(hip_y_fcurve)
        hip_bone.location[1] = 0
        hip_bone.keyframe_insert(data_path="location", index=1, frame=1, group=hip_bone.name)

        for area in context.screen.areas:
            if area.type == 'GRAPH_EDITOR':
                area.tag_redraw()

        self.report({'INFO'}, "Hip Y transferred to Trans X (jump/vertical)")
        return {'FINISHED'}