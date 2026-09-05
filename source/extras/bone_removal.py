'''
Adapted from https://blender.stackexchange.com/a/226914/132453
'''
import bpy
from bpy.types import Operator

class SUB_OT_remove_selected_bones(Operator):
    bl_idname = "sub.remove_selected_bones"
    bl_label = "Remove Selected Bones"
    bl_description = "Removes selected bones and transfers their weights to their parents"
    bl_options = {'REGISTER', 'UNDO'}

    def transfer_weights(self, source, target, obj):
        source_group = obj.vertex_groups.get(source.name)
        if source_group is None:
            return
        source_i = source_group.index
        target_group = obj.vertex_groups.get(target.name)
        if target_group is None:
            target_group = obj.vertex_groups.new(name=target.name)
            
        for v in obj.data.vertices:
            for g in v.groups:
                if g.group == source_i:
                    target_group.add((v.index,), g.weight, 'ADD')
        obj.vertex_groups.remove(source_group)

    def remove_bone(self, source, target):
        for o in bpy.data.objects:
            self.transfer_weights(source, target, o)
        edit_bone = bpy.context.object.data.edit_bones.get(source.name)
        bpy.context.object.data.edit_bones.remove(edit_bone)

    def find_parent_not_in_collection(self, bone, collection):
        if bone.parent in collection:
            return self.find_parent_not_in_collection(bone.parent, collection)
        else:
            return bone.parent

    @classmethod
    def poll(cls, context):
        return (context.active_object 
                and context.active_object.type == 'ARMATURE'
                and context.active_object.mode == 'EDIT')

    def execute(self, context):
        selected_bones = [bone for bone in context.object.data.edit_bones if bone.select]
        for selected_bone in selected_bones:
            target = self.find_parent_not_in_collection(selected_bone, selected_bones)
            self.remove_bone(selected_bone, target)
        return {'FINISHED'}


WEIGHT_EPS = 1e-8


def _armature_from_context(context):
    obj = context.active_object
    if obj is not None and obj.type == "ARMATURE":
        return obj
    for selected in context.selected_objects or []:
        if selected.type == "ARMATURE":
            return selected
    if obj is not None and obj.type == "MESH":
        return obj.find_armature()
    return None


def _meshes_for_armature(armature):
    from ..model.material.convert_smash_material import iter_armature_meshes
    return list(iter_armature_meshes(armature))


def _weighted_bone_names(meshes):
    weighted = set()
    for mesh_obj in meshes:
        if mesh_obj.type != "MESH" or not mesh_obj.vertex_groups:
            continue
        index_to_name = {group.index: group.name for group in mesh_obj.vertex_groups}
        for vertex in mesh_obj.data.vertices:
            for group in vertex.groups:
                if group.weight > WEIGHT_EPS:
                    name = index_to_name.get(group.group)
                    if name:
                        weighted.add(name)
    return weighted


def _bones_to_keep(armature, weighted_names):
    keep = set()
    bones = armature.data.bones
    for name in weighted_names:
        bone = bones.get(name)
        while bone is not None:
            keep.add(bone.name)
            bone = bone.parent
    return keep


def _bone_depth(bones, name):
    depth = 0
    bone = bones.get(name)
    while bone is not None and bone.parent is not None:
        depth += 1
        bone = bone.parent
    return depth


class SUB_OT_delete_unweighted_bones(Operator):
    bl_idname = "sub.delete_unweighted_bones"
    bl_label = "Delete Unweighted Bones"
    bl_description = (
        "Delete bones that have no vertex weights on meshes using this armature. "
        "Parents of weighted bones are kept"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _armature_from_context(context) is not None

    def invoke(self, context, event):
        armature = _armature_from_context(context)
        meshes = _meshes_for_armature(armature) if armature is not None else []
        if armature is None:
            self.report({"WARNING"}, "Select an armature or a skinned mesh.")
            return {"CANCELLED"}
        if not meshes:
            self.report({"WARNING"}, "No meshes found using this armature.")
            return {"CANCELLED"}
        weighted = _weighted_bone_names(meshes)
        if not weighted:
            self.report({"WARNING"}, "No vertex weights found on meshes using this armature.")
            return {"CANCELLED"}
        keep = _bones_to_keep(armature, weighted)
        delete_count = sum(1 for bone in armature.data.bones if bone.name not in keep)
        if delete_count == 0:
            self.report({"INFO"}, "Every bone is used by weights or is a parent of a weighted bone.")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        armature = _armature_from_context(context)
        layout = self.layout
        if armature is None:
            layout.label(text="Select an armature.")
            return
        meshes = _meshes_for_armature(armature)
        weighted = _weighted_bone_names(meshes)
        keep = _bones_to_keep(armature, weighted)
        delete_count = sum(1 for bone in armature.data.bones if bone.name not in keep)
        layout.label(text=f"Delete {delete_count} unweighted bone(s)?")
        layout.label(text="Parents of weighted bones are kept.")

    def execute(self, context):
        armature = _armature_from_context(context)
        if armature is None:
            self.report({"WARNING"}, "Select an armature or a skinned mesh.")
            return {"CANCELLED"}

        meshes = _meshes_for_armature(armature)
        if not meshes:
            self.report({"WARNING"}, "No meshes found using this armature.")
            return {"CANCELLED"}

        weighted = _weighted_bone_names(meshes)
        if not weighted:
            self.report({"WARNING"}, "No vertex weights found on meshes using this armature.")
            return {"CANCELLED"}

        keep = _bones_to_keep(armature, weighted)
        to_delete = [bone.name for bone in armature.data.bones if bone.name not in keep]
        if not to_delete:
            self.report({"INFO"}, "Every bone is used by weights or is a parent of a weighted bone.")
            return {"CANCELLED"}

        view_layer = context.view_layer
        previous_active = view_layer.objects.active
        previous_mode = getattr(previous_active, "mode", "OBJECT") if previous_active else "OBJECT"
        previous_selection = [obj for obj in context.selected_objects]

        view_layer.objects.active = armature
        if armature.mode != "EDIT":
            bpy.ops.object.mode_set(mode="EDIT")

        edit_bones = armature.data.edit_bones
        to_delete.sort(key=lambda name: _bone_depth(armature.data.bones, name), reverse=True)
        deleted = 0
        for name in to_delete:
            edit_bone = edit_bones.get(name)
            if edit_bone is None:
                continue
            edit_bones.remove(edit_bone)
            deleted += 1

        bpy.ops.object.mode_set(mode="OBJECT")

        delete_set = set(to_delete)
        for mesh_obj in meshes:
            for group in list(mesh_obj.vertex_groups):
                if group.name in delete_set:
                    mesh_obj.vertex_groups.remove(group)

        restored_active = previous_active if previous_active is not None else armature
        if restored_active.name in bpy.data.objects:
            for obj in list(context.selected_objects):
                obj.select_set(False)
            for obj in previous_selection:
                if obj.name in bpy.data.objects:
                    obj.select_set(True)
            view_layer.objects.active = restored_active
            if previous_mode in {"POSE", "EDIT"} and restored_active.type == "ARMATURE":
                bpy.ops.object.mode_set(mode=previous_mode)
            elif previous_mode == "EDIT" and restored_active.type == "MESH":
                bpy.ops.object.mode_set(mode="EDIT")

        self.report({"INFO"}, f"Deleted {deleted} unweighted bone(s).")
        return {"FINISHED"}


# Register the operator
def register():
    bpy.utils.register_class(SUB_OT_remove_selected_bones)
    bpy.utils.register_class(SUB_OT_delete_unweighted_bones)

def unregister():
    bpy.utils.unregister_class(SUB_OT_delete_unweighted_bones)
    bpy.utils.unregister_class(SUB_OT_remove_selected_bones)

if __name__ == "__main__":
    register()