"""Eye look setup and rig helper for CustomVector31.

WHAT CV31 IS. Despite being the thing everyone uses to aim the eyes, it is
literally "UV Transform Layer 2" (source/model/material/matl_params.py):

    CV31.X = UV layer 2 scale X        <- pupil size
    CV31.Y = UV layer 2 scale Y        <- pupil size
    CV31.Z = UV layer 2 translate X    <- look left / right
    CV31.W = UV layer 2 translate Y    <- look up / down

Aiming the eyes is just sliding the eye texture around. Vanilla default is
(1, 1, 0, 0).

WHY THE MODAL DOES NOTHING ON A FRESH RIG. Editing an eye needs a chain of
three things, and the modal only supplies the middle one:

    control value                 material node             viewport
    sub_anim_properties           CustomVector31_Z          the eye
    .mat_tracks['EyeL']    --->   value node in       --->  actually
    .properties['CV31']    driver  the material             moves

The driver is built by import_anim.setup_material_drivers(), which only runs
when an animation is imported or a mat track is edited. Worse, the mat track
can exist with an EMPTY property list - which is exactly the state a rig ends
up in - and then the modal's properties.get('CustomVector31') returns None,
both eyes come back None, and it cancels with a message about loading an
animation. The materials already have their CustomVector31_X/Y/Z/W value
nodes; only the track property and driver are missing.

SUB_OT_setup_eye_cv31 builds that missing chain directly.

WHY THE RIG HELPER BAKES RATHER THAN DRIVES. It would be neater to drive
mat_tracks straight from a control bone, but export_anim.py gathers material
animation by scanning the action's FCURVES for the mat_tracks data path - it
does not evaluate drivers. A driven CV31 would look perfect in Blender and
export as nothing at all. So the control bone is baked down to real keyframes
on the track, which is what export reads.
"""

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy.types import Operator

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..anim.anim_data import SUB_PG_sub_anim_data, SUB_PG_mat_track


EYE_TRACKS = ('EyeL', 'EyeR')
CV31 = 'CustomVector31'
CV31_DEFAULT = (1.0, 1.0, 0.0, 0.0)

# Control bone names. Kept out of the exported skeleton's way by living only
# in Blender - they are helpers, not game bones, and Export Model will happily
# include them if left in, so they are named distinctively.
EYE_CTRL_BONE = 'BL_EyeLook'


def eye_meshes(arma):
    """Meshes whose first material is an eye - used to locate the face."""
    from ..model.export_model import trim_name
    found = []
    for child in arma.children:
        if child.type != 'MESH' or not child.material_slots:
            continue
        mat = child.material_slots[0].material
        if mat is not None and trim_name(mat.name) in EYE_TRACKS:
            found.append(child)
    return found


def face_forward(arma, anchor_bone):
    """Which way the character faces, in armature space, measured not assumed.

    Smash rigs face -Y, but rather than hardcode that this compares the eye
    meshes' centre against the head bone. Measured on this rig the head sits
    at Y=-0.035 and the eyes at Y=-0.96, so forward really is -Y; an earlier
    version of this assumed +Y and planted the control behind the head.
    Falls back to -Y only when there is nothing to measure from.
    """
    from mathutils import Vector
    meshes = eye_meshes(arma)
    if meshes and anchor_bone is not None:
        centre = Vector((0.0, 0.0, 0.0))
        n = 0
        for mesh in meshes:
            local = arma.matrix_world.inverted() @ mesh.matrix_world
            for corner in mesh.bound_box:
                centre += local @ Vector(corner)
                n += 1
        if n:
            centre /= n
            direction = centre - anchor_bone.head
            direction.z = 0.0            # horizontal only; keep the control at eye height
            if direction.length > 1e-6:
                return direction.normalized()
    return Vector((0.0, -1.0, 0.0))


def control_offset_armature_space(pbone):
    """The animator's push on the control, as an armature-space vector.

    pbone.location is expressed in the BONE's own axes, which for a Smash head
    chain are nothing like world XYZ - Head's local X is world +Y and its
    local Z is world +X. Reading .x/.z straight off it, as an earlier version
    did, mapped 'move right' onto the wrong look direction entirely. Rotating
    by the bone's rest matrix puts it back into armature space, where X really
    is left/right and Z really is up/down whatever the bone's roll.
    """
    return pbone.bone.matrix_local.to_3x3() @ pbone.location


def look_values_from_control(pbone, sensitivity, clamp, invert_x=False, invert_y=False):
    """(left_z, right_z, shared_w) CV31 components for the control's pose.

    The two eyes take opposite horizontal signs - the eye texture slides the
    other way on the mirrored UV - while vertical is shared.

    Which eye gets which sign was originally taken from the existing CV31
    mouse modal, but that modal derives its offset from a reversed mouse delta
    (temp_mouse - event.mouse), so copying its signs directly inverted the
    horizontal look. Corrected here; the toggles exist because the right
    answer depends on how the eye UVs were laid out, which varies per model.
    """
    offset = control_offset_armature_space(pbone)
    dx = max(-clamp, min(clamp, offset.x * sensitivity))
    dz = max(-clamp, min(clamp, offset.z * sensitivity))
    if invert_x:
        dx = -dx
    if invert_y:
        dz = -dz
    return dx, -dx, dz


def apply_look_to_tracks(arma, sensitivity, clamp, invert_x=False, invert_y=False):
    """Push the control bone's pose into CV31. Returns True if anything moved."""
    pbone = arma.pose.bones.get(EYE_CTRL_BONE)
    if pbone is None:
        return False
    sap = arma.data.sub_anim_properties
    left_z, right_z, shared_w = look_values_from_control(
        pbone, sensitivity, clamp, invert_x, invert_y)
    changed = False
    for name, z in (('EyeL', left_z), ('EyeR', right_z)):
        track = sap.mat_tracks.get(name)
        prop = track.properties.get(CV31) if track else None
        if prop is None:
            continue
        if abs(prop.custom_vector[2] - z) > 1e-7 or abs(prop.custom_vector[3] - shared_w) > 1e-7:
            prop.custom_vector[2] = z
            prop.custom_vector[3] = shared_w
            changed = True
    return changed


# Live preview. Writing CV31 from inside a depsgraph handler retriggers the
# depsgraph, so this reentrancy guard is what stops it looping forever, and
# apply_look_to_tracks only writes when a value actually differs.
_live_sync_running = False


@bpy.app.handlers.persistent
def _eye_look_live_handler(scene, depsgraph=None):
    global _live_sync_running
    if _live_sync_running:
        return
    ssp = getattr(scene, 'sub_scene_properties', None)
    if ssp is None or not getattr(ssp, 'eye_look_live_preview', False):
        return
    _live_sync_running = True
    try:
        for obj in scene.objects:
            if obj.type != 'ARMATURE' or EYE_CTRL_BONE not in obj.pose.bones:
                continue
            apply_look_to_tracks(obj, ssp.eye_look_sensitivity, ssp.eye_look_clamp,
                                 ssp.eye_look_invert_x, ssp.eye_look_invert_y)
    except Exception as ex:
        print(f"[eye look live] disabled after error: {ex}")
        try:
            scene.sub_scene_properties.eye_look_live_preview = False
        except Exception:
            pass
    finally:
        _live_sync_running = False


def _register_live_handler():
    if _eye_look_live_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_eye_look_live_handler)


def _unregister_live_handler():
    if _eye_look_live_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_eye_look_live_handler)


def get_or_create_track(sap, name):
    track = sap.mat_tracks.get(name)
    if track is None:
        track = sap.mat_tracks.add()
        track.name = name
        return track, True
    return track, False


def get_or_create_cv31(track):
    prop = track.properties.get(CV31)
    if prop is not None:
        return prop, False
    prop = track.properties.add()
    prop.sub_type = 'VECTOR'
    prop.name = CV31                      # set after sub_type; name has an update callback
    prop.custom_vector = CV31_DEFAULT
    return prop, True


class SUB_OT_setup_eye_cv31(Operator):
    """Create the EyeL/EyeR CustomVector31 tracks and drivers so eye aiming works"""
    bl_idname = "sub.setup_eye_cv31"
    bl_label = "Set Up Eye Look (CustomVector31)"
    bl_description = ("Create the EyeL/EyeR material tracks and their CustomVector31 property "
                      "if missing, then build the drivers that connect them to the eye "
                      "materials. Without this the eye modal has nothing to edit and cancels")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == 'ARMATURE'

    def execute(self, context):
        arma = context.object
        sap = arma.data.sub_anim_properties

        made_tracks, made_props, already = [], [], []
        for name in EYE_TRACKS:
            track, new_track = get_or_create_track(sap, name)
            if new_track:
                made_tracks.append(name)
            _prop, new_prop = get_or_create_cv31(track)
            if new_prop:
                made_props.append(name)
            elif not new_track:
                already.append(name)

        # Rebuild the node drivers now the tracks are in place. Indices are
        # baked into each driver's data path, so they must be rebuilt whenever
        # a track or property is added - not just created once.
        from ..anim.anim_data import refresh_material_drivers
        try:
            refresh_material_drivers(context)
        except Exception as ex:
            self.report({'ERROR'}, f"Tracks created but driver setup failed: {ex}")
            return {'CANCELLED'}

        # Report what the materials can actually receive, since a driver is
        # only half the chain - the value node has to exist too.
        missing_nodes = []
        from ..model.export_model import trim_name
        mats = {slot.material for child in arma.children if child.type == 'MESH'
                for slot in child.material_slots if slot.material}
        by_trimmed = {trim_name(m.name): m for m in mats}
        for name in EYE_TRACKS:
            mat = by_trimmed.get(name)
            if mat is None:
                missing_nodes.append(f"{name} (no material assigned to a mesh)")
            elif not mat.use_nodes or mat.node_tree.nodes.get(f'{CV31}_Z') is None:
                missing_nodes.append(f"{name} (material has no {CV31}_Z value node)")

        msg = (f"Eye look ready. Tracks created: {made_tracks or 'none'}; "
               f"{CV31} added to: {made_props or 'none'}; already set up: {already or 'none'}")
        if missing_nodes:
            msg += (f". NOTE - these can't be driven yet: {', '.join(missing_nodes)}. "
                    "Re-import the material for that eye so its value nodes exist")
        self.report({'WARNING'} if missing_nodes else {'INFO'}, msg)
        return {'FINISHED'}


class SUB_OT_add_eye_look_control(Operator):
    """Add a bone you can move to aim the eyes, then bake it to CustomVector31"""
    bl_idname = "sub.add_eye_look_control"
    bl_label = "Add Eye Look Control Bone"
    bl_description = ("Add a control bone in front of the head. Move it in Pose Mode to aim "
                      "the eyes, then use Bake to write it into CustomVector31 keyframes")
    bl_options = {"REGISTER", "UNDO"}

    distance: FloatProperty(
        name="Distance In Front",
        description="How far in front of the head the control sits, in Blender units",
        default=3.0, min=0.01,
    )

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == 'ARMATURE'

    def execute(self, context):
        arma = context.object
        prev_mode = arma.mode

        parent_name = None
        for candidate in ('Head', 'Face', 'Neck'):
            if candidate in arma.data.bones:
                parent_name = candidate
                break

        bpy.ops.object.mode_set(mode='EDIT')
        try:
            ebs = arma.data.edit_bones
            existing = ebs.get(EYE_CTRL_BONE)
            if existing is not None:
                ebs.remove(existing)
            anchor = ebs.get(parent_name) if parent_name else None
            if anchor is not None:
                base = anchor.head.copy()
                size = max((anchor.head - anchor.tail).length, 0.1)
            else:
                base = ebs[0].head.copy() if len(ebs) else None
                size = 1.0
                if base is None:
                    self.report({'ERROR'}, "Armature has no bones to anchor the control to")
                    return {'CANCELLED'}
            forward = face_forward(arma, anchor)
            # Sit at eye height rather than the head bone's root, so the
            # control lands in front of the face instead of the forehead.
            eyes = eye_meshes(arma)
            if eyes:
                from mathutils import Vector
                zs = []
                for mesh in eyes:
                    local = arma.matrix_world.inverted() @ mesh.matrix_world
                    zs += [(local @ Vector(c)).z for c in mesh.bound_box]
                base.z = sum(zs) / len(zs)
            offset = forward * (self.distance * size)
            ctrl = ebs.new(EYE_CTRL_BONE)
            ctrl.head = base + offset
            ctrl.tail = base + offset + forward * size
            ctrl.roll = 0.0
            ctrl.use_deform = False
            if anchor is not None:
                ctrl.parent = anchor
                ctrl.use_connect = False
        finally:
            bpy.ops.object.mode_set(mode=prev_mode if prev_mode != 'EDIT' else 'OBJECT')

        self.report({'INFO'},
                    f"Added {EYE_CTRL_BONE}"
                    + (f" parented to {parent_name}" if parent_name else " (no Head bone found to parent to)")
                    + ". Move it in Pose Mode, then Bake Eye Look.")
        return {'FINISHED'}


class SUB_OT_bake_eye_look(Operator):
    """Bake the eye look control bone into CustomVector31 keyframes"""
    bl_idname = "sub.bake_eye_look"
    bl_label = "Bake Eye Look to CustomVector31"
    bl_description = ("Sample the control bone across the scene frame range and write "
                      "CustomVector31 keyframes for both eyes. Export reads those keyframes - "
                      "a driver alone would export as nothing")
    bl_options = {"REGISTER", "UNDO"}

    sensitivity: FloatProperty(
        name="Sensitivity",
        description="UV units of eye movement per Blender unit the control moves",
        default=0.05, soft_min=0.0, soft_max=1.0,
    )
    clamp: FloatProperty(
        name="Clamp",
        description="Largest UV offset allowed, so the pupil can't slide off the eye",
        default=0.5, min=0.0,
    )

    @classmethod
    def poll(cls, context):
        obj = context.object
        if obj is None or obj.type != 'ARMATURE':
            return False
        return EYE_CTRL_BONE in obj.pose.bones

    def execute(self, context):
        arma = context.object
        sap = arma.data.sub_anim_properties

        tracks = {}
        for name in EYE_TRACKS:
            track = sap.mat_tracks.get(name)
            prop = track.properties.get(CV31) if track else None
            if prop is not None:
                tracks[name] = (sap.mat_tracks.find(name), track, track.properties.find(CV31))
        if not tracks:
            self.report({'ERROR'},
                        "No EyeL/EyeR CustomVector31 to bake into - run "
                        "'Set Up Eye Look (CustomVector31)' first")
            return {'CANCELLED'}

        if arma.data.animation_data is None or arma.data.animation_data.action is None:
            self.report({'ERROR'},
                        "The armature DATA has no action to hold the keyframes - "
                        "import or create an animation first")
            return {'CANCELLED'}

        scene = context.scene
        start, end = scene.frame_start, scene.frame_end
        original_frame = scene.frame_current
        pbone = arma.pose.bones[EYE_CTRL_BONE]

        baked = 0
        # Live preview writes CV31 on every depsgraph update; leaving it on
        # during a bake would fight the frame-by-frame values being inserted.
        ssp = context.scene.sub_scene_properties
        prev_live = ssp.eye_look_live_preview
        ssp.eye_look_live_preview = False
        try:
            for frame in range(start, end + 1):
                scene.frame_set(frame)
                left_z, right_z, shared_w = look_values_from_control(
                    pbone, self.sensitivity, self.clamp,
                    ssp.eye_look_invert_x, ssp.eye_look_invert_y)
                for name, (track_index, track, prop_index) in tracks.items():
                    prop = track.properties[prop_index]
                    prop.custom_vector[2] = left_z if name == 'EyeL' else right_z
                    prop.custom_vector[3] = shared_w
                    arma.data.keyframe_insert(
                        data_path=(f'sub_anim_properties.mat_tracks[{track_index}]'
                                   f'.properties[{prop_index}].custom_vector'),
                        frame=frame,
                        group=f'Material ({name})')
                baked += 1
        finally:
            scene.frame_set(original_frame)
            ssp.eye_look_live_preview = prev_live

        self.report({'INFO'},
                    f"Baked {baked} frame(s) of eye look into {', '.join(sorted(tracks))} "
                    f"{CV31}. These are real keyframes, so they will export")
        return {'FINISHED'}


classes = (
    SUB_OT_setup_eye_cv31,
    SUB_OT_add_eye_look_control,
    SUB_OT_bake_eye_look,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass
    _register_live_handler()


def unregister():
    _unregister_live_handler()
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
