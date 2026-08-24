import bpy
import json
from bpy.types import Operator
from bpy.props import StringProperty
from mathutils import Quaternion


def _generate_unique_user_pose_name(ssp, base_name: str) -> str:
    existing = {item.name for item in ssp.user_pose_list}
    if base_name not in existing:
        return base_name
    i = 1
    while f"{base_name} ({i})" in existing:
        i += 1
    return f"{base_name} ({i})"


class SUB_OP_user_pose_add(Operator):
    bl_idname = "sub.user_pose_add"
    bl_label = "Add User Pose"
    bl_description = "Save transforms of selected bones at the current frame into the User Poses list"
    bl_options = {'REGISTER', 'UNDO'}

    pose_name: StringProperty(
        name="Pose Name",
        description="Optional name for the saved pose; leave empty to auto-name",
        default=""
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'ARMATURE' and context.mode == 'POSE' and bool(context.selected_pose_bones)

    def execute(self, context):
        armature = context.active_object
        ssp = context.scene.sub_scene_properties
        frame = context.scene.frame_current

        # Ensure we're in Pose Mode to select affected bones visibly
        prev_mode = context.mode
        if context.view_layer.objects.active != armature:
            context.view_layer.objects.active = armature
        if prev_mode != 'POSE':
            try:
                bpy.ops.object.mode_set(mode='POSE')
            except Exception:
                pass

        pose_data = {}
        for pbone in context.selected_pose_bones:
            # Rotation as quaternion [x,y,z,w]
            if pbone.rotation_mode == 'QUATERNION':
                q = pbone.rotation_quaternion.copy()
            else:
                q = pbone.rotation_euler.to_quaternion()

            transform_data = {
                "scale": [pbone.scale[0], pbone.scale[1], pbone.scale[2]],
                "rotation": [q.x, q.y, q.z, q.w],
                "translation": [pbone.location[0], pbone.location[1], pbone.location[2]],
                "flags": {
                    "override_translation": True,
                    "override_rotation": True,
                    "override_scale": True
                }
            }
            pose_data[pbone.name] = transform_data

        if not pose_data:
            self.report({'ERROR'}, "No pose bones found to save")
            return {'CANCELLED'}

        base_name = self.pose_name.strip() or f"Pose F{frame}"
        final_name = _generate_unique_user_pose_name(ssp, base_name)

        new_item = ssp.user_pose_list.add()
        new_item.name = final_name
        new_item.data = json.dumps(pose_data)
        ssp.user_pose_list_index = len(ssp.user_pose_list) - 1

        self.report({'INFO'}, f"Saved user pose '{final_name}' with {len(pose_data)} bones")
        return {'FINISHED'}


class SUB_OP_user_pose_remove(Operator):
    bl_idname = "sub.user_pose_remove"
    bl_label = "Remove User Pose"
    bl_description = "Remove the selected user pose from the list"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ssp = context.scene.sub_scene_properties
        return bool(ssp.user_pose_list) and (0 <= ssp.user_pose_list_index < len(ssp.user_pose_list))

    def execute(self, context):
        ssp = context.scene.sub_scene_properties
        idx = ssp.user_pose_list_index
        ssp.user_pose_list.remove(idx)
        ssp.user_pose_list_index = max(0, idx - 1)
        self.report({'INFO'}, "Removed user pose")
        return {'FINISHED'}


class SUB_OP_user_pose_apply_selected(Operator):
    bl_idname = "sub.user_pose_apply_selected"
    bl_label = "Apply User Pose"
    bl_description = "Apply the selected user pose to the current frame (keys only saved bones)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not (obj and obj.type == 'ARMATURE'):
            return False
        ssp = context.scene.sub_scene_properties
        return bool(ssp.user_pose_list) and (0 <= ssp.user_pose_list_index < len(ssp.user_pose_list))

    def execute(self, context):
        armature = context.active_object
        ssp = context.scene.sub_scene_properties
        frame = context.scene.frame_current

        item = ssp.user_pose_list[ssp.user_pose_list_index]
        if not item.data:
            self.report({'ERROR'}, "Selected user pose has no data")
            return {'CANCELLED'}

        try:
            pose_data = json.loads(item.data)
        except Exception as e:
            self.report({'ERROR'}, f"Invalid pose data: {e}")
            return {'CANCELLED'}

        apply_only_selected = getattr(ssp, 'user_pose_apply_only_selected', False)
        selected_names = set(p.name for p in context.selected_pose_bones) if apply_only_selected and context.mode == 'POSE' else None

        # If applying to all saved bones, clear selection to reflect affected bones
        if not apply_only_selected:
            try:
                for ebone in armature.data.bones:
                    ebone.select = False
            except Exception:
                pass

        applied = 0
        active_bone_name = None
        for bone_name, data in pose_data.items():
            if apply_only_selected and selected_names is not None and bone_name not in selected_names:
                continue
            if bone_name not in armature.pose.bones:
                continue
            pbone = armature.pose.bones[bone_name]

            # Apply translation
            t = data.get("translation", [0.0, 0.0, 0.0])
            pbone.location[0], pbone.location[1], pbone.location[2] = t[0], t[1], t[2]

            # Apply rotation
            r = data.get("rotation", [0.0, 0.0, 0.0, 1.0])
            q = Quaternion((r[3], r[0], r[1], r[2]))
            if pbone.rotation_mode == 'QUATERNION':
                pbone.rotation_quaternion = q
            else:
                pbone.rotation_euler = q.to_euler(pbone.rotation_mode)

            # Apply scale
            s = data.get("scale", [1.0, 1.0, 1.0])
            pbone.scale[0], pbone.scale[1], pbone.scale[2] = s[0], s[1], s[2]

            # Insert keyframes for just these bones
            pbone.keyframe_insert(data_path="location", frame=frame, group=pbone.name)
            if pbone.rotation_mode == 'QUATERNION':
                pbone.keyframe_insert(data_path="rotation_quaternion", frame=frame, group=pbone.name)
            else:
                pbone.keyframe_insert(data_path="rotation_euler", frame=frame, group=pbone.name)
            pbone.keyframe_insert(data_path="scale", frame=frame, group=pbone.name)

            applied += 1
            # Select affected bone only when applying to all
            if not apply_only_selected:
                try:
                    pbone.bone.select = True
                    if active_bone_name is None:
                        active_bone_name = bone_name
                except Exception:
                    pass

        context.view_layer.update()
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

        # Set an active bone for convenience
        if active_bone_name and not apply_only_selected:
            try:
                armature.data.bones.active = armature.data.bones.get(active_bone_name, None)
            except Exception:
                pass

        if applied == 0:
            self.report({'WARNING'}, "No matching bones found in the current armature for this pose")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Applied user pose '{item.name}' to {applied} bones at frame {frame}")
        return {'FINISHED'}


