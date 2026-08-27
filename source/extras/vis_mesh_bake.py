"""
Bake frames of a retargeted facial animation into standalone Smash VIS meshes.

The problem this solves: an All Justice face rig and a Smash face rig are not the same
skeleton. AJ drives the face with ~90 bones (`LT_eyelid_A`, `jaw_A`, `L_browskin_C`, ...);
Smash drives it with a dozen (`Jaw`, `UplipC`, `DownlipL`, `Tongue`, ...) and switches between
pre-modelled *VIS meshes* for everything else. So a retargeted AJ expression cannot be shipped
as animation — the bones it moves do not exist on the Smash rig. It has to become geometry.

Doing that by hand is the tedious part: scrub to the frame, duplicate, apply the armature
modifier, delete the AJ-only vertex groups, collapse their weights onto `Head` so the mesh
still follows the skull, rename what survives, repeat for every blink and mouth shape, and
never touch the original because the next costume needs it again.

This module is that loop, as a list. Each row is one output mesh: a name, an action, and a
frame. `Bake All` walks the rows and leaves the source mesh untouched.

The weight rule is the whole reason this is not just "apply modifier":
    * a group whose name the Smash rig also has crosses over unchanged (`Head`, `Neck`,
      `Face`, the fingers, the helper bones — most of the body);
    * a group with an explicit remap is renamed to its Smash counterpart (`jaw_A` -> `Jaw`);
    * everything left is AJ-only face rigging with no Smash equivalent, and its weight is
      folded into the fallback group (`Head`) rather than dropped, so the vertex still moves
      with the head instead of being left behind at the origin.
"""

import re

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList

# `<fighter>_<VisName>_VIS_O_OBJShape` — how the importer names Smash's switchable face meshes.
VIS_MESH_RE = re.compile(r"^(?P<fighter>.+?)_(?P<vis>.+)_VIS_O_OBJShape(?:\.\d+)?$")

VIS_NAME_TEMPLATE = "{fighter}_{name}_VIS_O_OBJShape"

# Suggested AJ -> Smash face-bone pairings, used only to prefill the remap list. These are
# proposals for the user to confirm, never applied on their own: an unconfirmed row is just a
# row, and a group nobody maps still lands safely on the fallback.
#
# AJ's prefixes read TOP and UNDER, not "top" and "up": `T_lip` is the UPPER lip and `U_lip`
# is the LOWER one, so `LT_*` maps to Smash's `Uplip*` and `LU_*` to `Downlip*`. Reading them
# the other way round silently swaps the lips, which looks almost right in a closed mouth and
# obviously wrong the moment it opens.
#
# Smash carries two upper bones per side (`UplipL1`, `UplipL2`) against AJ's four, so the
# outer two fold onto the nearer of the pair rather than being sent to the skull.
#
# The teeth, chin and faceline entries follow the same T/U reading: whatever is under the
# mouth line rides the jaw, and whatever is above it stays with the skull (which is the
# fallback, so those need no entry).
REMAP_SUGGESTIONS = {
    "jaw_a": "Jaw",
    "tongue_a": "Tongue",
    "tongue_b": "Tongue",
    # Upper lip — AJ `T` (top).
    "t_lip": "UplipC",
    "lt_lip_a": "UplipL1",
    "lt_lip_b": "UplipL2",
    "lt_lip_c": "UplipL2",
    "lt_lip_d": "UplipL2",
    "rt_lip_a": "UplipR1",
    "rt_lip_b": "UplipR2",
    "rt_lip_c": "UplipR2",
    "rt_lip_d": "UplipR2",
    # Lower lip — AJ `U` (under).
    "u_lip": "DownlipC",
    "lu_lip_a": "DownlipL",
    "lu_lip_b": "DownlipL",
    "lu_lip_c": "DownlipL",
    "ru_lip_a": "DownlipR",
    "ru_lip_b": "DownlipR",
    "ru_lip_c": "DownlipR",
    # Mouth corners: Smash has one bone for the region AJ splits above and below.
    "t_mouth": "Mouth_group",
    "u_mouth": "Mouth_group",
    "lt_mouth_a": "Mouth_group",
    "rt_mouth_a": "Mouth_group",
    "lu_mouth_a": "Mouth_group",
    "ru_mouth_a": "Mouth_group",
    # Below the mouth line: rides the jaw. (`t_teeth` is the upper set and stays on the skull.)
    "u_teeth": "Jaw",
    "l_chin_a": "Jaw",
    "r_chin_a": "Jaw",
    "lu_faceline_a": "Jaw",
    "ru_faceline_a": "Jaw",
}


def _settings(context):
    return context.scene.sub_vis_bake


def _reference_group_names(reference) -> set[str]:
    """The set of group names the baked mesh is allowed to keep.

    Accepts either an armature (its bones) or a mesh (its vertex groups). A mesh is usually
    the better answer: an imported Smash VIS mesh carries exactly the group set the exporter
    expects, whereas a merged armature may already contain the AJ bones being stripped.
    """
    if reference is None:
        return set()
    if reference.type == 'ARMATURE':
        return {bone.name for bone in reference.data.bones}
    if reference.type == 'MESH':
        return {group.name for group in reference.vertex_groups}
    return set()


def _keyed_bone_names(action) -> set[str]:
    """Bones an action actually writes to, read from its fcurve data paths."""
    names: set[str] = set()
    if action is None:
        return names
    for fcurve in action.fcurves:
        match = re.match(r'pose\.bones\["([^"]+)"\]', fcurve.data_path)
        if match:
            names.add(match.group(1))
    return names


def _capture_pose(armature):
    """Every pose bone's transform, so the bake can put the rig back exactly as it was."""
    return {
        pbone.name: (
            tuple(pbone.location),
            tuple(pbone.rotation_quaternion),
            tuple(pbone.rotation_euler),
            tuple(pbone.scale),
            pbone.rotation_mode,
        )
        for pbone in armature.pose.bones
    }


def _restore_pose(armature, snapshot):
    for pbone in armature.pose.bones:
        stored = snapshot.get(pbone.name)
        if stored is None:
            continue
        location, quat, euler, scale, mode = stored
        pbone.rotation_mode = mode
        pbone.location = location
        pbone.rotation_quaternion = quat
        pbone.rotation_euler = euler
        pbone.scale = scale


def _neutralize_unkeyed(armature, keyed: set[str]):
    """Reset every bone the action does not key back to rest.

    A facial action keys face bones only. Whatever pose the body was left in is not part of
    the expression, and baking it in would double-deform the result once the mesh is bound
    back to the rig — the retained Smash bones would apply their transform a second time on
    playback. Clearing them makes the bake a pure face snapshot in rest space.
    """
    for pbone in armature.pose.bones:
        if pbone.name in keyed:
            continue
        pbone.location = (0.0, 0.0, 0.0)
        pbone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pbone.rotation_euler = (0.0, 0.0, 0.0)
        pbone.scale = (1.0, 1.0, 1.0)


def _resolve_group_targets(vertex_groups, settings) -> tuple[dict[int, str], set[str]]:
    """Map each vertex-group index to the group name it becomes on the baked mesh.

    Returns the mapping and the set of source names that fell back, so the operator can say
    how much of the AJ face rig was collapsed rather than leaving it silent.
    """
    allowed = _reference_group_names(settings.bone_reference)
    remaps = {entry.name.lower(): entry.target for entry in settings.remaps if entry.target}
    lowered = {name.lower(): name for name in allowed}
    fallback = lowered.get(settings.fallback_group.lower(), settings.fallback_group)

    targets: dict[int, str] = {}
    collapsed: set[str] = set()
    for index, group in enumerate(vertex_groups):
        name = group.name
        remapped = remaps.get(name.lower())
        if remapped:
            targets[index] = remapped
            continue
        if name in allowed:
            targets[index] = name
            continue
        # Tolerate a pure case difference between the two rigs rather than treating it as an
        # unknown bone; use the reference's spelling so the exporter sees the name it expects.
        ci = lowered.get(name.lower())
        if ci:
            targets[index] = ci
            continue
        targets[index] = fallback
        collapsed.add(name)
    return targets, collapsed


def _bake_one(context, settings, entry, depsgraph_frame_set) -> tuple[object, set[str]]:
    """Evaluate the source mesh at one frame and build a standalone object from it."""
    source_obj = settings.source_object
    depsgraph_frame_set(entry)

    depsgraph = context.evaluated_depsgraph_get()
    eval_obj = source_obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(
        eval_obj, preserve_all_data_layers=True, depsgraph=depsgraph
    )

    name = settings.name_template.format(
        name=entry.name,
        fighter=settings.fighter_prefix,
        frame=entry.frame,
    )
    mesh.name = name
    new_obj = bpy.data.objects.new(name, mesh)
    new_obj.matrix_world = source_obj.matrix_world.copy()

    # `new_from_object` carries the source's vertex groups across with the mesh, so the new
    # object already has all 90-odd AJ groups on it. They are resolved against `new_obj`'s own
    # group order (not the source's) so the indices in the deform data are guaranteed to line
    # up, then the whole set is rebuilt: adding groups on top of the inherited ones instead
    # would collide into `Head.001` and leave every vertex weighted twice.
    targets, collapsed = _resolve_group_targets(new_obj.vertex_groups, settings)

    # Fold the weights. Several AJ groups collapse onto one Smash group, so weights are summed
    # per target before being written — assigning them one at a time would let the last write
    # win and quietly discard the rest. Read every vertex before clearing the groups, because
    # clearing wipes the deform data these weights come from.
    folded_per_vertex: list[dict[str, float]] = []
    for vertex in mesh.vertices:
        folded: dict[str, float] = {}
        for element in vertex.groups:
            target = targets.get(element.group)
            if not target:
                continue
            folded[target] = folded.get(target, 0.0) + element.weight
        if folded and settings.normalize_weights:
            total = sum(folded.values())
            if total > 0.0:
                folded = {key: value / total for key, value in folded.items()}
        folded_per_vertex.append(folded)

    new_obj.vertex_groups.clear()

    # Recreate in the source's own order so the result reads the way the reference does rather
    # than in whatever order weights happen to appear.
    group_lookup: dict[str, object] = {}
    for index in range(len(targets)):
        target = targets.get(index)
        if target and target not in group_lookup:
            group_lookup[target] = new_obj.vertex_groups.new(name=target)

    used_targets: set[str] = set()
    for vertex_index, folded in enumerate(folded_per_vertex):
        for target, weight in folded.items():
            group = group_lookup.get(target)
            if group is not None:
                group.add([vertex_index], min(weight, 1.0), 'REPLACE')
                used_targets.add(target)

    # A source group is created for every mapping target, but a mesh that only covers part of
    # the body carries groups no vertex actually uses — more so once unused geometry has been
    # cleaned out of it. Drop the ones nothing landed in so the result lists the bones it is
    # really weighted to.
    if settings.drop_empty_groups:
        for target, group in group_lookup.items():
            if target not in used_targets:
                new_obj.vertex_groups.remove(group)

    context.collection.objects.link(new_obj)

    if settings.bind_armature and settings.armature:
        new_obj.parent = settings.armature
        new_obj.matrix_parent_inverse = settings.armature.matrix_world.inverted()
        modifier = new_obj.modifiers.new(name="Armature", type='ARMATURE')
        modifier.object = settings.armature

    return new_obj, collapsed


class SUB_PG_vis_bake_entry(PropertyGroup):
    name: StringProperty(
        name="Output Name",
        description="Smash VIS name for this shape, e.g. Openblink or Mouth_Talk",
        default="",
    )
    action: StringProperty(
        name="Action",
        description="Facial action to read the shape from",
        default="",
    )
    frame: IntProperty(name="Frame", description="Frame to bake", default=1)
    use: BoolProperty(name="Bake", description="Include this row in Bake All", default=True)


class SUB_PG_vis_bake_remap(PropertyGroup):
    name: StringProperty(name="Source Group", description="Vertex group on the AJ mesh")
    target: StringProperty(
        name="Smash Bone",
        description="Group name to fold this into. Leave empty to send it to the fallback",
    )


class SUB_PG_vis_bake(PropertyGroup):
    source_object: PointerProperty(
        name="Source Mesh",
        description="The animated AJ face mesh to bake from. It is never modified",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
    )
    armature: PointerProperty(
        name="Rig",
        description="Armature that plays the facial actions",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE',
    )
    bone_reference: PointerProperty(
        name="Smash Groups From",
        description=(
            "Defines which vertex groups survive the bake. Point at an imported Smash VIS "
            "mesh for the exact group set the exporter expects, or at a pure Smash armature"
        ),
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type in {'MESH', 'ARMATURE'},
    )
    fallback_group: StringProperty(
        name="Fallback Group",
        description="Where AJ-only face weights go so the vertex still follows the skull",
        default="Head",
    )
    fighter_prefix: StringProperty(
        name="Fighter",
        description="Fills {fighter} in the name template",
        default="eflame",
    )
    name_template: StringProperty(
        name="Name Template",
        description="Output object name. {name}, {fighter} and {frame} are substituted",
        default="{name}",
    )
    neutralize_unkeyed: BoolProperty(
        name="Rest Unkeyed Bones",
        description=(
            "Reset bones the action does not key before baking, so a leftover body pose is "
            "not baked in and cannot double-deform the result once it is bound back"
        ),
        default=True,
    )
    normalize_weights: BoolProperty(
        name="Normalize Weights",
        description="Rescale each vertex's folded weights to sum to 1",
        default=True,
    )
    drop_empty_groups: BoolProperty(
        name="Drop Empty Groups",
        description="Remove vertex groups no vertex on the baked mesh is weighted to",
        default=True,
    )
    bind_armature: BoolProperty(
        name="Bind To Rig",
        description="Parent the baked mesh to the rig and add an Armature modifier",
        default=True,
    )
    entries: CollectionProperty(type=SUB_PG_vis_bake_entry)
    entries_index: IntProperty(default=0)
    remaps: CollectionProperty(type=SUB_PG_vis_bake_remap)
    remaps_index: IntProperty(default=0)


class SUB_UL_vis_bake_entries(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "use", text="")
        row.prop(item, "name", text="", emboss=False)
        sub = row.row(align=True)
        sub.scale_x = 0.9
        sub.prop_search(item, "action", bpy.data, "actions", text="", icon='ACTION')
        sub.scale_x = 0.4
        sub.prop(item, "frame", text="")


class SUB_UL_vis_bake_remaps(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False)
        row.label(icon='FORWARD')
        row.prop(item, "target", text="")


class SUB_OP_vis_bake_entry_add(Operator):
    bl_idname = "sub.vis_bake_entry_add"
    bl_label = "Add Row"
    bl_description = "Add an empty bake row"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = _settings(context)
        entry = settings.entries.add()
        entry.name = "NewShape"
        entry.frame = context.scene.frame_current
        settings.entries_index = len(settings.entries) - 1
        return {'FINISHED'}


class SUB_OP_vis_bake_entry_remove(Operator):
    bl_idname = "sub.vis_bake_entry_remove"
    bl_label = "Remove Row"
    bl_description = "Remove the selected bake row"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return 0 <= settings.entries_index < len(settings.entries)

    def execute(self, context):
        settings = _settings(context)
        settings.entries.remove(settings.entries_index)
        settings.entries_index = max(0, settings.entries_index - 1)
        return {'FINISHED'}


class SUB_OP_vis_bake_entry_move(Operator):
    bl_idname = "sub.vis_bake_entry_move"
    bl_label = "Move Row"
    bl_description = "Reorder the selected bake row"
    bl_options = {'REGISTER', 'UNDO'}

    direction: StringProperty(default='UP')

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return len(settings.entries) > 1

    def execute(self, context):
        settings = _settings(context)
        index = settings.entries_index
        target = index - 1 if self.direction == 'UP' else index + 1
        if not (0 <= target < len(settings.entries)):
            return {'CANCELLED'}
        settings.entries.move(index, target)
        settings.entries_index = target
        return {'FINISHED'}


class SUB_OP_vis_bake_populate(Operator):
    bl_idname = "sub.vis_bake_populate"
    bl_label = "Populate From VIS Meshes"
    bl_description = (
        "Create one row per imported Smash VIS mesh in the scene, named after it. Fill in the "
        "action and frame for the ones you want and delete the rest"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = _settings(context)
        existing = {entry.name for entry in settings.entries}

        found: dict[str, str] = {}
        for obj in context.scene.objects:
            if obj.type != 'MESH':
                continue
            match = VIS_MESH_RE.match(obj.name)
            if not match:
                continue
            # `.001` duplicates of the same VIS mesh are the same shape; keep one row each.
            found.setdefault(match.group("vis"), match.group("fighter"))

        if not found:
            self.report({'WARNING'}, "No *_VIS_O_OBJShape meshes found in the scene")
            return {'CANCELLED'}

        added = 0
        for vis_name in sorted(found):
            if vis_name in existing:
                continue
            entry = settings.entries.add()
            entry.name = vis_name
            entry.frame = 1
            entry.use = False
            added += 1

        fighters = set(found.values())
        if len(fighters) == 1:
            settings.fighter_prefix = next(iter(fighters))

        settings.entries_index = max(0, len(settings.entries) - 1)
        self.report({'INFO'}, f"Added {added} row(s) from {len(found)} VIS mesh name(s)")
        return {'FINISHED'}


class SUB_OP_vis_bake_use_vis_template(Operator):
    bl_idname = "sub.vis_bake_use_vis_template"
    bl_label = "Use VIS Name Template"
    bl_description = "Set the name template to the exporter's VIS mesh naming"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        _settings(context).name_template = VIS_NAME_TEMPLATE
        return {'FINISHED'}


class SUB_OP_vis_bake_guess_remaps(Operator):
    bl_idname = "sub.vis_bake_guess_remaps"
    bl_label = "Suggest Remaps"
    bl_description = (
        "Prefill the remap list with AJ face groups that have a likely Smash counterpart. "
        "These are suggestions — check them before baking"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return settings.source_object is not None and settings.bone_reference is not None

    def execute(self, context):
        settings = _settings(context)
        allowed = _reference_group_names(settings.bone_reference)
        existing = {entry.name for entry in settings.remaps}

        added = 0
        for group in settings.source_object.vertex_groups:
            if group.name in allowed or group.name in existing:
                continue
            suggestion = REMAP_SUGGESTIONS.get(group.name.lower())
            if not suggestion or suggestion not in allowed:
                continue
            entry = settings.remaps.add()
            entry.name = group.name
            entry.target = suggestion
            added += 1

        self.report({'INFO'}, f"Suggested {added} remap(s)")
        return {'FINISHED'}


class SUB_OP_vis_bake_remap_add(Operator):
    bl_idname = "sub.vis_bake_remap_add"
    bl_label = "Add Remap"
    bl_description = "Add an empty remap row"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = _settings(context)
        settings.remaps.add()
        settings.remaps_index = len(settings.remaps) - 1
        return {'FINISHED'}


class SUB_OP_vis_bake_remap_remove(Operator):
    bl_idname = "sub.vis_bake_remap_remove"
    bl_label = "Remove Remap"
    bl_description = "Remove the selected remap row"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return 0 <= settings.remaps_index < len(settings.remaps)

    def execute(self, context):
        settings = _settings(context)
        settings.remaps.remove(settings.remaps_index)
        settings.remaps_index = max(0, settings.remaps_index - 1)
        return {'FINISHED'}


class SUB_OP_vis_bake_preview_weights(Operator):
    bl_idname = "sub.vis_bake_preview_weights"
    bl_label = "Preview Weight Mapping"
    bl_description = (
        "Report how the source mesh's vertex groups would be treated, without baking anything"
    )

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return settings.source_object is not None and settings.bone_reference is not None

    def execute(self, context):
        settings = _settings(context)
        targets, collapsed = _resolve_group_targets(
            settings.source_object.vertex_groups, settings
        )
        kept = len(set(targets.values()))
        total = len(settings.source_object.vertex_groups)
        print("\n[VIS bake] weight mapping preview")
        for index, group in enumerate(settings.source_object.vertex_groups):
            target = targets.get(index, "?")
            marker = "  " if target == group.name else "->"
            print(f"  {group.name:24} {marker} {target}")
        self.report(
            {'INFO'},
            f"{total} source group(s) -> {kept} kept; "
            f"{len(collapsed)} folded into '{settings.fallback_group}' (details in console)",
        )
        return {'FINISHED'}


class SUB_OP_vis_bake_run(Operator):
    bl_idname = "sub.vis_bake_run"
    bl_label = "Bake"
    bl_description = "Bake the enabled rows into new meshes. The source mesh is not modified"
    bl_options = {'REGISTER', 'UNDO'}

    only_active: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return (
            settings.source_object is not None
            and settings.armature is not None
            and settings.bone_reference is not None
        )

    def execute(self, context):
        settings = _settings(context)
        scene = context.scene
        armature = settings.armature

        if self.only_active:
            index = settings.entries_index
            if not (0 <= index < len(settings.entries)):
                self.report({'ERROR'}, "No active row")
                return {'CANCELLED'}
            rows = [settings.entries[index]]
        else:
            rows = [entry for entry in settings.entries if entry.use]

        if not rows:
            self.report({'WARNING'}, "No rows enabled to bake")
            return {'CANCELLED'}

        missing = [entry.name for entry in rows if not entry.action]
        if missing:
            self.report({'ERROR'}, f"No action set for: {', '.join(missing[:5])}")
            return {'CANCELLED'}

        if armature.animation_data is None:
            armature.animation_data_create()

        original_action = armature.animation_data.action
        original_frame = scene.frame_current
        original_pose = _capture_pose(armature)

        created = []
        all_collapsed: set[str] = set()
        try:
            for entry in rows:
                action = bpy.data.actions.get(entry.action)
                if action is None:
                    self.report({'ERROR'}, f"Action '{entry.action}' not found")
                    return {'CANCELLED'}

                def apply_frame(entry=entry, action=action):
                    armature.animation_data.action = action
                    scene.frame_set(entry.frame)
                    if settings.neutralize_unkeyed:
                        _neutralize_unkeyed(armature, _keyed_bone_names(action))
                    context.view_layer.update()

                new_obj, collapsed = _bake_one(context, settings, entry, apply_frame)
                created.append(new_obj.name)
                all_collapsed |= collapsed
        finally:
            armature.animation_data.action = original_action
            scene.frame_set(original_frame)
            _restore_pose(armature, original_pose)
            context.view_layer.update()

        if all_collapsed:
            print(
                f"\n[VIS bake] folded into '{settings.fallback_group}': "
                + ", ".join(sorted(all_collapsed))
            )

        self.report(
            {'INFO'},
            f"Baked {len(created)} mesh(es); "
            f"{len(all_collapsed)} AJ-only group(s) folded into '{settings.fallback_group}'",
        )
        return {'FINISHED'}


class SUB_PT_vis_mesh_bake(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'VIS Mesh Bake'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)

        box = layout.box()
        box.prop(settings, "source_object")
        box.prop(settings, "armature")
        box.prop(settings, "bone_reference")
        row = box.row(align=True)
        row.prop(settings, "fallback_group")
        box.operator("sub.vis_bake_preview_weights", icon='GROUP_VERTEX')

        box = layout.box()
        box.label(text="Naming")
        box.prop(settings, "fighter_prefix")
        row = box.row(align=True)
        row.prop(settings, "name_template")
        row.operator("sub.vis_bake_use_vis_template", text="", icon='PRESET')

        box = layout.box()
        header = box.row(align=True)
        header.label(text="Shapes")
        header.operator("sub.vis_bake_populate", text="", icon='IMPORT')

        row = box.row()
        row.template_list(
            "SUB_UL_vis_bake_entries", "", settings, "entries", settings, "entries_index", rows=6
        )
        col = row.column(align=True)
        col.operator("sub.vis_bake_entry_add", text="", icon='ADD')
        col.operator("sub.vis_bake_entry_remove", text="", icon='REMOVE')
        col.separator()
        col.operator("sub.vis_bake_entry_move", text="", icon='TRIA_UP').direction = 'UP'
        col.operator("sub.vis_bake_entry_move", text="", icon='TRIA_DOWN').direction = 'DOWN'

        box = layout.box()
        header = box.row(align=True)
        header.label(text="Bone Remaps")
        header.operator("sub.vis_bake_guess_remaps", text="", icon='AUTO')
        row = box.row()
        row.template_list(
            "SUB_UL_vis_bake_remaps", "", settings, "remaps", settings, "remaps_index", rows=3
        )
        col = row.column(align=True)
        col.operator("sub.vis_bake_remap_add", text="", icon='ADD')
        col.operator("sub.vis_bake_remap_remove", text="", icon='REMOVE')

        box = layout.box()
        box.prop(settings, "neutralize_unkeyed")
        box.prop(settings, "normalize_weights")
        box.prop(settings, "drop_empty_groups")
        box.prop(settings, "bind_armature")

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("sub.vis_bake_run", text="Bake All", icon='RENDER_RESULT').only_active = False
        row.operator("sub.vis_bake_run", text="Bake Active").only_active = True


classes = (
    SUB_PG_vis_bake_entry,
    SUB_PG_vis_bake_remap,
    SUB_PG_vis_bake,
    SUB_UL_vis_bake_entries,
    SUB_UL_vis_bake_remaps,
    SUB_OP_vis_bake_entry_add,
    SUB_OP_vis_bake_entry_remove,
    SUB_OP_vis_bake_entry_move,
    SUB_OP_vis_bake_populate,
    SUB_OP_vis_bake_use_vis_template,
    SUB_OP_vis_bake_guess_remaps,
    SUB_OP_vis_bake_remap_add,
    SUB_OP_vis_bake_remap_remove,
    SUB_OP_vis_bake_preview_weights,
    SUB_OP_vis_bake_run,
    SUB_PT_vis_mesh_bake,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass
    bpy.types.Scene.sub_vis_bake = PointerProperty(type=SUB_PG_vis_bake)


def unregister():
    try:
        del bpy.types.Scene.sub_vis_bake
    except AttributeError:
        pass
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
