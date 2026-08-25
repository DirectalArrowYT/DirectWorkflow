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
            ctrl = ebs.new(EYE_CTRL_BONE)
            # +Y is forward for Smash rigs (the face looks down +Y in rest).
            ctrl.head = (base.x, base.y + self.distance * size, base.z)
            ctrl.tail = (base.x, base.y + self.distance * size + size, base.z)
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
        try:
            for frame in range(start, end + 1):
                scene.frame_set(frame)
                # Location relative to the bone's own rest position, so a
                # control sitting still writes the neutral (0, 0) look.
                offset = pbone.matrix_basis.translation
                dx = max(-self.clamp, min(self.clamp, offset.x * self.sensitivity))
                dz = max(-self.clamp, min(self.clamp, offset.z * self.sensitivity))
                for name, (track_index, track, prop_index) in tracks.items():
                    prop = track.properties[prop_index]
                    # X is mirrored between the eyes - the same convention the
                    # existing CV31 modal uses (left subtracts, right adds).
                    prop.custom_vector[2] = -dx if name == 'EyeL' else dx
                    prop.custom_vector[3] = dz
                    arma.data.keyframe_insert(
                        data_path=(f'sub_anim_properties.mat_tracks[{track_index}]'
                                   f'.properties[{prop_index}].custom_vector'),
                        frame=frame,
                        group=f'Material ({name})')
                baked += 1
        finally:
            scene.frame_set(original_frame)

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


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
