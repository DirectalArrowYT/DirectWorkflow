import bpy
from bpy.types import EditBone, Operator


def _armature(context):
    obj = context.active_object
    if obj is not None and obj.type == "ARMATURE":
        return obj
    return None


def _selected_bone_names(context, armature):
    if armature is None:
        return []
    if armature.mode == "EDIT":
        return [bone.name for bone in armature.data.edit_bones if bone.select]
    if armature.mode == "POSE":
        return [bone.name for bone in context.selected_pose_bones or []]
    return []


def _chain_child(bone: EditBone):
    children = list(bone.children)
    if not children:
        return None
    if len(children) == 1:
        return children[0]
    non_eff = [child for child in children if not child.name.endswith("_eff")]
    candidates = non_eff or children
    if len(candidates) == 1:
        return candidates[0]
    tail = bone.tail
    return min(candidates, key=lambda child: (child.head - tail).length_squared)


def _root_selected_bones(selected):
    selected_set = set(selected)
    return [bone for bone in selected if bone.parent not in selected_set]


def connect_chains_from_roots(roots) -> int:
    visited = set()
    connected = 0
    stack = list(roots)
    while stack:
        bone = stack.pop()
        if bone.name in visited:
            continue
        visited.add(bone.name)
        child = _chain_child(bone)
        extras = [extra for extra in bone.children if extra is not child]
        if child is not None:
            bone.tail = child.head.copy()
            child.use_connect = True
            connected += 1
            stack.append(child)
        stack.extend(extras)
    return connected


class SUB_OT_connect_bone_chain(Operator):
    bl_idname = "sub.connect_bone_chain"
    bl_label = "Connect Bone Chain"
    bl_description = (
        "Snap each selected bone's tail to the next bone in its child chain, "
        "then keep connecting down the rest of that chain"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        armature = _armature(context)
        return bool(_selected_bone_names(context, armature))

    def execute(self, context):
        armature = _armature(context)
        names = _selected_bone_names(context, armature)
        if armature is None or not names:
            self.report({"WARNING"}, "Select a bone in Pose or Armature Edit Mode.")
            return {"CANCELLED"}

        previous_mode = armature.mode
        if previous_mode != "EDIT":
            bpy.ops.object.mode_set(mode="EDIT")

        selected = [armature.data.edit_bones.get(name) for name in names]
        selected = [bone for bone in selected if bone is not None]
        connected = connect_chains_from_roots(_root_selected_bones(selected))

        if previous_mode != "EDIT":
            bpy.ops.object.mode_set(mode=previous_mode)

        if not connected:
            self.report({"WARNING"}, "Selected bone(s) have no child chain to connect.")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Connected {connected} bone(s) down the chain.")
        return {"FINISHED"}


def register():
    bpy.utils.register_class(SUB_OT_connect_bone_chain)


def unregister():
    bpy.utils.unregister_class(SUB_OT_connect_bone_chain)
