import bpy
from bpy.types import Operator

from ..blender_compat import is_armature_bone_selected


def set_object_mode_if_needed(context):
    active = context.view_layer.objects.active
    if active is not None and active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


class SUB_OT_copy_bone_rolls(Operator):
    """Copy source edit-bone rolls to same-named bones on the target armature"""
    bl_idname = "sub.copy_bone_rolls"
    bl_label = "Copy Matching Roll Values"
    bl_description = "Copy edit-bone roll values from the source armature to same-named bones on the target"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        ssp = getattr(context.scene, "sub_scene_properties", None)
        if ssp is None:
            return False
        source = ssp.roll_copy_source
        target = ssp.roll_copy_target
        return (
            source is not None
            and target is not None
            and source != target
            and source.type == "ARMATURE"
            and target.type == "ARMATURE"
        )

    def execute(self, context):
        ssp = context.scene.sub_scene_properties
        source = ssp.roll_copy_source
        target = ssp.roll_copy_target

        if source is None or target is None:
            self.report({"ERROR"}, "Choose both a source and target armature")
            return {"CANCELLED"}
        if source == target:
            self.report({"ERROR"}, "Source and target must be different armatures")
            return {"CANCELLED"}
        if source.data == target.data:
            self.report(
                {"ERROR"},
                "Source and target share the same armature data, so their rolls are already linked",
            )
            return {"CANCELLED"}
        if source.name not in context.view_layer.objects or target.name not in context.view_layer.objects:
            self.report({"ERROR"}, "Both armatures must be visible in the current view layer")
            return {"CANCELLED"}
        if target.data.library is not None:
            self.report({"ERROR"}, "The target armature data is linked and cannot be edited")
            return {"CANCELLED"}

        selected_only = ssp.roll_copy_selected_only
        selected_target_names = {
            bone.name for bone in target.data.bones if is_armature_bone_selected(target, bone)
        }
        if selected_only and not selected_target_names:
            self.report({"WARNING"}, "No target bones are selected")
            return {"CANCELLED"}

        view_layer = context.view_layer
        original_active = view_layer.objects.active
        original_mode = original_active.mode if original_active is not None else "OBJECT"
        original_selected = [obj for obj in view_layer.objects if obj.select_get()]

        copied = 0
        unmatched = 0

        try:
            set_object_mode_if_needed(context)

            for obj in view_layer.objects:
                obj.select_set(False)
            source.select_set(True)
            target.select_set(True)
            view_layer.objects.active = target

            # Bone roll is an EditBone property. Multi-object Edit Mode lets us
            # read the source and write the target without reconstructing roll
            # from matrices (which can introduce tiny numerical differences).
            bpy.ops.object.mode_set(mode="EDIT")

            source_bones = source.data.edit_bones
            target_bones = target.data.edit_bones

            for target_bone in target_bones:
                if selected_only and target_bone.name not in selected_target_names:
                    continue

                source_bone = source_bones.get(target_bone.name)
                if source_bone is None:
                    unmatched += 1
                    continue

                target_bone.roll = source_bone.roll
                copied += 1

            target.data.update_tag()

        except RuntimeError as error:
            self.report({"ERROR"}, f"Could not copy bone rolls: {error}")
            return {"CANCELLED"}
        finally:
            try:
                set_object_mode_if_needed(context)
                for obj in view_layer.objects:
                    obj.select_set(False)
                for obj in original_selected:
                    if obj.name in view_layer.objects:
                        obj.select_set(True)
                if original_active is not None and original_active.name in view_layer.objects:
                    view_layer.objects.active = original_active
                    if original_mode != "OBJECT":
                        original_active.select_set(True)
                        bpy.ops.object.mode_set(mode=original_mode)
            except RuntimeError:
                pass

        noun = "bone" if copied == 1 else "bones"
        message = f"Copied roll values to {copied} matching {noun}"
        if unmatched:
            message += f"; skipped {unmatched} without a source-name match"
        self.report({"INFO"}, message)
        return {"FINISHED"}


classes = (
    SUB_OT_copy_bone_rolls,
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
