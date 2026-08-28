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
import textwrap

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList

# `<fighter>_<VisName>_VIS_O_OBJShape` — how the importer names Smash's switchable face meshes.
VIS_MESH_RE = re.compile(r"^(?P<fighter>.+?)_(?P<vis>.+)_VIS_O_OBJShape(?:\.\d+)?$")

VIS_NAME_TEMPLATE = "{fighter}_{name}_VIS_O_OBJShape"

HALF_ITEMS = (
    ('UPPER', "Upper", "Bake from the upper face mesh — eyes and blinks"),
    ('LOWER', "Lower", "Bake from the lower face mesh — mouth and jaw"),
)

# VIS names that belong to the lower half. Smash prefixes every mouth shape with `Mouth_`, so
# the split is readable straight off the imported mesh names.
LOWER_HINTS = ("mouth", "lip", "voice", "talk")

# ...but an eye name wins over those, because the two vocabularies overlap: `Blink_Talk` is the
# eye shape used *while* talking, not a mouth shape, and matching "talk" first would file it
# under the wrong mesh.
UPPER_HINTS = ("blink", "eye")

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


def _source_for(settings, entry):
    """The mesh a row bakes from — the upper half for eyes, the lower half for the mouth."""
    return settings.source_lower if entry.half == 'LOWER' else settings.source_upper


def _active_sources(settings):
    """(label, object) for each source slot that is filled, for the whole-list operators."""
    return [
        (label, obj)
        for label, obj in (("Upper", settings.source_upper), ("Lower", settings.source_lower))
        if obj is not None
    ]


def _format_name(settings, entry):
    """The object name a row bakes to, or `None` if the template cannot be filled in.

    Returns `None` rather than raising so the caller can refuse the whole run with one clear
    message instead of a traceback halfway through.
    """
    try:
        return settings.name_template.format(
            name=entry.name,
            fighter=settings.fighter_prefix,
            frame=entry.frame,
        )
    except (KeyError, IndexError, ValueError):
        return None


def _template_problem(settings) -> str:
    """Why the current name template cannot produce one distinct name per row, if it can't.

    A template with no `{name}` in it formats every row to the same string, and Blender then
    silently disambiguates with `.001`, `.002` — so a run "succeeds" and leaves a pile of
    identically named meshes that have to be matched back up by hand. Catching it here is the
    difference between a refused bake and an hour of renaming.
    """
    template = settings.name_template
    if not template.strip():
        return "The name template is empty"
    probe = type("Probe", (), {"name": "X", "frame": 1})()
    if _format_name(settings, probe) is None:
        return (
            f"The name template {template!r} uses a placeholder that does not exist. "
            "Only {name}, {fighter} and {frame} are available"
        )
    if "{name}" not in template:
        return (
            f"The name template {template!r} has no {{name}} in it, so every row would bake to "
            "the same object name. Use the preset button beside the field for the standard "
            "VIS naming"
        )
    return ""


def _deform_armature(source_obj):
    """The rig that actually deforms `source_obj` — its Armature modifier's target.

    This is deliberately not the object's parent and not the rig chosen for binding output.
    A retargeted face mesh is routinely parented to the character rig for organisation while
    being deformed by a separate facial rig, and playing the action on the parent then poses
    a skeleton nothing is skinned to: the bake still runs, and every mesh comes out in rest
    pose. The modifier is the only thing that says which rig the vertices follow.
    """
    for modifier in source_obj.modifiers:
        if modifier.type == 'ARMATURE' and modifier.object is not None:
            return modifier.object
    parent = source_obj.parent
    return parent if parent is not None and parent.type == 'ARMATURE' else None


def _bind_target(settings, source_obj):
    """The rig a baked mesh is parented and bound to.

    Falls back to the source mesh's own parent, which in the usual setup is exactly right: the
    face mesh is parented to the Smash rig for organisation while an Armature modifier deforms
    it from the facial rig. The parent is therefore the rig the output belongs on, and the
    modifier is the rig the animation plays on — the two are read from different places on
    purpose, so neither has to be configured by hand.
    """
    if settings.armature is not None:
        return settings.armature
    parent = source_obj.parent
    return parent if parent is not None and parent.type == 'ARMATURE' else None


def _assign_action(rig, action):
    """Put `action` on `rig` so it actually evaluates.

    Blender 4.4+ splits an action into slots, and assigning `animation_data.action` can leave
    no slot bound — the action is attached, evaluates to nothing, and the rig stays at rest.
    Bind the first slot when one is not chosen for us.
    """
    rig.animation_data.action = action
    try:
        if rig.animation_data.action_slot is None and getattr(action, "slots", None):
            rig.animation_data.action_slot = action.slots[0]
    except (AttributeError, TypeError):
        pass


def _guess_half(vis_name: str) -> str:
    lowered = vis_name.lower()
    if any(hint in lowered for hint in UPPER_HINTS):
        return 'UPPER'
    return 'LOWER' if any(hint in lowered for hint in LOWER_HINTS) else 'UPPER'


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
    source_obj = _source_for(settings, entry)
    depsgraph_frame_set(entry)

    depsgraph = context.evaluated_depsgraph_get()
    eval_obj = source_obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(
        eval_obj, preserve_all_data_layers=True, depsgraph=depsgraph
    )

    name = _format_name(settings, entry)

    # The imported Smash VIS meshes already hold these names, so Blender would hand the bake
    # `…_VIS_O_OBJShape.001` and the exporter would not see the name it needs. Move the old one
    # aside instead of deleting it: it is usually still the bone reference, and a rename keeps
    # every pointer to it intact while freeing the name.
    if settings.replace_existing:
        for datablocks in (bpy.data.objects, bpy.data.meshes):
            previous = datablocks.get(name)
            if previous is not None and previous is not source_obj:
                previous.name = f"{name}_old"

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

    unbound: set[str] = set()
    if settings.bind_armature:
        bind = _bind_target(settings, source_obj)
        if bind is not None:
            new_obj.parent = bind
            new_obj.matrix_parent_inverse = bind.matrix_world.inverted()
            modifier = new_obj.modifiers.new(name="Armature", type='ARMATURE')
            modifier.object = bind
            # A group with no bone of that name on the bind rig is inert — the mesh looks
            # bound and simply will not follow. Worth naming, because the reference that
            # decided these names can be a mesh skinned to a different skeleton entirely.
            bones = {bone.name for bone in bind.data.bones}
            unbound = {
                group.name for group in new_obj.vertex_groups if group.name not in bones
            }

    return new_obj, collapsed, unbound


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
    half: EnumProperty(
        name="Half",
        description="Which source mesh this shape comes from",
        items=HALF_ITEMS,
        default='UPPER',
    )
    use: BoolProperty(name="Bake", description="Include this row in Bake All", default=True)


class SUB_PG_vis_bake_remap(PropertyGroup):
    name: StringProperty(name="Source Group", description="Vertex group on the AJ mesh")
    target: StringProperty(
        name="Smash Bone",
        description="Group name to fold this into. Leave empty to send it to the fallback",
    )


class SUB_PG_vis_bake(PropertyGroup):
    source_upper: PointerProperty(
        name="Upper Mesh",
        description=(
            "Animated AJ mesh for the eyes and blinks. Rows set to Upper bake from this. "
            "It is never modified"
        ),
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
    )
    source_lower: PointerProperty(
        name="Lower Mesh",
        description=(
            "Animated AJ mesh for the mouth and jaw. Rows set to Lower bake from this. "
            "It is never modified"
        ),
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
    )
    armature: PointerProperty(
        name="Bind To",
        description=(
            "Armature the BAKED meshes are parented and bound to — normally the Smash rig. "
            "The rig the animation plays on is not this one: it is taken from each source "
            "mesh's own Armature modifier, so a face deformed by a separate facial rig works "
            "without setting anything"
        ),
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
        description=(
            "Output object name. {name}, {fighter} and {frame} are substituted — plain text "
            "with no {name} in it would give every row the same name. Defaults to the naming "
            "the model exporter expects"
        ),
        default=VIS_NAME_TEMPLATE,
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
    replace_existing: BoolProperty(
        name="Take Name From Existing",
        description=(
            "If an object already holds the name a row bakes to — usually the imported VIS "
            "mesh being replaced — rename that one to <name>_old so the new mesh gets the "
            "exact name the exporter expects, instead of Blender appending .001. Nothing is "
            "deleted"
        ),
        default=False,
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
        settings = _settings(context)
        row = layout.row(align=True)
        row.prop(item, "use", text="")
        row.prop(item, "name", text="", emboss=False)
        half = row.row(align=True)
        half.scale_x = 0.55
        # Flag a row whose half has no mesh assigned, rather than letting the bake fail later.
        half.alert = _source_for(settings, item) is None
        half.prop(item, "half", text="")
        sub = row.row(align=True)
        sub.scale_x = 0.85
        sub.prop_search(item, "action", bpy.data, "actions", text="", icon='ACTION')
        sub.scale_x = 0.35
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
            entry.half = _guess_half(vis_name)
            entry.use = False
            added += 1

        fighters = set(found.values())
        if len(fighters) == 1:
            settings.fighter_prefix = next(iter(fighters))

        settings.entries_index = max(0, len(settings.entries) - 1)
        self.report({'INFO'}, f"Added {added} row(s) from {len(found)} VIS mesh name(s)")
        return {'FINISHED'}


class SUB_OP_vis_bake_guess_halves(Operator):
    bl_idname = "sub.vis_bake_guess_halves"
    bl_label = "Guess Halves From Names"
    bl_description = (
        "Re-read every row's name and set Upper or Lower from it. Populate only assigns a half "
        "to rows it creates, so use this on a list saved before the two-mesh split existed, or "
        "after renaming rows"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(_settings(context).entries)

    def execute(self, context):
        settings = _settings(context)
        changed = 0
        for entry in settings.entries:
            guess = _guess_half(entry.name)
            if entry.half != guess:
                entry.half = guess
                changed += 1
        self.report({'INFO'}, f"Reassigned {changed} row(s)")
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
        return bool(_active_sources(settings)) and settings.bone_reference is not None

    def execute(self, context):
        settings = _settings(context)
        allowed = _reference_group_names(settings.bone_reference)
        existing = {entry.name for entry in settings.remaps}

        added = 0
        # Both halves feed one remap list: the two meshes share the AJ rig, so a pairing found
        # on the mouth mesh is the same pairing on the eye mesh.
        for _, source in _active_sources(settings):
            for group in source.vertex_groups:
                if group.name in allowed or group.name in existing:
                    continue
                suggestion = REMAP_SUGGESTIONS.get(group.name.lower())
                if not suggestion or suggestion not in allowed:
                    continue
                entry = settings.remaps.add()
                entry.name = group.name
                entry.target = suggestion
                existing.add(group.name)
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
        return bool(_active_sources(settings)) and settings.bone_reference is not None

    def execute(self, context):
        settings = _settings(context)
        summary = []
        for label, source in _active_sources(settings):
            targets, collapsed = _resolve_group_targets(source.vertex_groups, settings)
            kept = len(set(targets.values()))
            total = len(source.vertex_groups)
            print(f"\n[VIS bake] weight mapping preview — {label}: {source.name}")
            for index, group in enumerate(source.vertex_groups):
                target = targets.get(index, "?")
                marker = "  " if target == group.name else "->"
                print(f"  {group.name:24} {marker} {target}")
            summary.append(f"{label}: {total}->{kept}, {len(collapsed)} folded")
        self.report(
            {'INFO'},
            "; ".join(summary) + f" (into '{settings.fallback_group}'; details in console)",
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
        return bool(_active_sources(settings)) and settings.bone_reference is not None

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

        problem = _template_problem(settings)
        if problem:
            self.report({'ERROR'}, problem)
            return {'CANCELLED'}

        missing = [entry.name for entry in rows if not entry.action]
        if missing:
            self.report({'ERROR'}, f"No action set for: {', '.join(missing[:5])}")
            return {'CANCELLED'}

        # Two rows can also collide by having the same name, which the template cannot fix.
        names = [_format_name(settings, entry) for entry in rows]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            self.report({'ERROR'}, f"Rows would collide on: {', '.join(duplicates[:5])}")
            return {'CANCELLED'}

        # A row can only bake if the half it names has a mesh. Check the whole list up front so
        # a missing slot is one message rather than a part-finished run.
        no_source = sorted(
            {
                f"{entry.name} ({entry.half.title()})"
                for entry in rows
                if _source_for(settings, entry) is None
            }
        )
        if no_source:
            self.report({'ERROR'}, f"No source mesh for: {', '.join(no_source[:5])}")
            return {'CANCELLED'}

        # The action has to play on the rig that DEFORMS each source, which is not necessarily
        # the rig chosen for binding: a retargeted face mesh is commonly parented to the
        # character rig while an Armature modifier deforms it from a separate facial rig.
        # Playing the action on the wrong one poses a skeleton nothing is skinned to, and every
        # mesh bakes in rest pose. Resolved per row, because the two halves can differ.
        deform_rigs: dict[str, object] = {}
        for entry in rows:
            source = _source_for(settings, entry)
            rig = _deform_armature(source)
            if rig is None:
                self.report(
                    {'ERROR'},
                    f"{source.name} has no Armature modifier, so there is no rig to play the "
                    "animation on",
                )
                return {'CANCELLED'}
            deform_rigs[source.name] = rig

        original_frame = scene.frame_current
        originals = []
        for rig in {rig.name: rig for rig in deform_rigs.values()}.values():
            if rig.animation_data is None:
                rig.animation_data_create()
            originals.append((rig, rig.animation_data.action, _capture_pose(rig)))

        created = []
        all_collapsed: set[str] = set()
        all_unbound: set[str] = set()
        try:
            for entry in rows:
                action = bpy.data.actions.get(entry.action)
                if action is None:
                    self.report({'ERROR'}, f"Action '{entry.action}' not found")
                    return {'CANCELLED'}
                rig = deform_rigs[_source_for(settings, entry).name]

                def apply_frame(entry=entry, action=action, rig=rig):
                    _assign_action(rig, action)
                    scene.frame_set(entry.frame)
                    if settings.neutralize_unkeyed:
                        _neutralize_unkeyed(rig, _keyed_bone_names(action))
                    context.view_layer.update()

                new_obj, collapsed, unbound = _bake_one(context, settings, entry, apply_frame)
                created.append(new_obj.name)
                all_collapsed |= collapsed
                all_unbound |= unbound
        finally:
            for rig, action, pose in originals:
                rig.animation_data.action = action
                _restore_pose(rig, pose)
            scene.frame_set(original_frame)
            context.view_layer.update()

        if all_collapsed:
            print(
                f"\n[VIS bake] folded into '{settings.fallback_group}': "
                + ", ".join(sorted(all_collapsed))
            )

        summary = (
            f"Baked {len(created)} mesh(es); "
            f"{len(all_collapsed)} AJ-only group(s) folded into '{settings.fallback_group}'"
        )
        if all_unbound:
            print(
                "\n[VIS bake] groups with no matching bone on the bind rig: "
                + ", ".join(sorted(all_unbound))
            )
            self.report(
                {'WARNING'},
                summary
                + f"; {len(all_unbound)} group(s) have no bone on the bind rig and will not "
                "deform (see console)",
            )
        else:
            self.report({'INFO'}, summary)
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
        box.prop(settings, "source_upper")
        box.prop(settings, "source_lower")
        # Which rig each source is actually deformed by decides whether the animation reaches
        # the bake at all, and it is invisible in the modifier stack unless you go looking, so
        # it is reported here rather than assumed.
        for label, source in _active_sources(settings):
            rig = _deform_armature(source)
            row = box.row()
            row.alert = rig is None
            row.label(
                text=f"{label} animated by: {rig.name if rig else 'NO ARMATURE MODIFIER'}",
                icon='ARMATURE_DATA' if rig else 'ERROR',
            )
        box.prop(settings, "armature")
        # Bind To is optional; say which rig the output will actually land on so an empty
        # field does not read as "nothing will happen".
        if settings.bind_armature and settings.armature is None:
            for label, source in _active_sources(settings):
                bind = _bind_target(settings, source)
                row = box.row()
                row.alert = bind is None
                row.label(
                    text=(
                        f"{label} parents to: {bind.name} (auto, from parent)"
                        if bind
                        else f"{label} has no parent rig — set Bind To"
                    ),
                    icon='OUTLINER_OB_ARMATURE' if bind else 'ERROR',
                )
        box.prop(settings, "bone_reference")
        row = box.row(align=True)
        row.prop(settings, "fallback_group")
        box.operator("sub.vis_bake_preview_weights", icon='GROUP_VERTEX')

        box = layout.box()
        box.label(text="Naming")
        box.prop(settings, "fighter_prefix")
        problem = _template_problem(settings)
        row = box.row(align=True)
        row.alert = bool(problem)
        row.prop(settings, "name_template")
        row.operator("sub.vis_bake_use_vis_template", text="", icon='PRESET')
        # Show the name a real row would get. A template is easy to get wrong in a way that
        # only shows up as a pile of `.001`s after the bake, so it is spelled out up front.
        if problem:
            for position, line in enumerate(textwrap.wrap(problem, 46)):
                box.label(text=line, icon='ERROR' if position == 0 else 'BLANK1')
        else:
            index = settings.entries_index
            sample = (
                settings.entries[index]
                if 0 <= index < len(settings.entries)
                else None
            )
            if sample is not None:
                box.label(text=_format_name(settings, sample), icon='OUTLINER_OB_MESH')

        box = layout.box()
        header = box.row(align=True)
        header.label(text="Shapes")
        header.operator("sub.vis_bake_guess_halves", text="", icon='ARROW_LEFTRIGHT')
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
        box.prop(settings, "replace_existing")

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
    SUB_OP_vis_bake_guess_halves,
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
