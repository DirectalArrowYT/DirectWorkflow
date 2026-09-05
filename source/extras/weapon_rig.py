"""
Attaching a weapon armature to a fighter so it can be animated in place, and
getting it back off again cleanly for export.

Smash attaches an article to its owner at runtime - `LinkModule` constrains the
weapon's `root` to a bone on the fighter and applies a rotation offset - so the
exported animation must contain none of that. `root` has to stay at the origin
with no rotation, and every keyframe has to be local to the weapon.

Two things follow, and they are the whole module:

1.  **The attachment is an object constraint, never a bone one.** Constraining
    the weapon object leaves the armature's own bones untouched, so `root` is
    still identity in bone space and the exporter never sees the attachment.
    Constraining `root` itself would bake the fighter's arm into the animation
    and the game would then apply it a second time.

2.  **IK has to be baked before export.** The exporter reads fcurves, not
    evaluated pose matrices, so a chain held in place by an IK constraint
    exports as nothing at all. `Bake Chain IK` writes the pose down as ordinary
    keyframes on the chain bones and takes the rig away.
"""

import bpy
import math
from mathutils import Euler, Matrix, Vector

# Smash's own offset for a hand-mounted article, from the fighter's weapon init.
# Positive X for the right side, negative for the left; the game mirrors it by
# facing, which is not something the animation should carry.
#
# In degrees here for readability. The property below stores radians, because
# an EULER property is radians internally and only *displays* degrees - putting
# 90 in it directly reads back as 90 radians, or 5156 degrees on screen.
DEFAULT_ROT_OFFSET_DEG = (90.0, 0.0, 90.0)

ATTACH_CONSTRAINT = "SUB Weapon Attach"
IK_CONSTRAINT = "SUB Chain IK"
IK_TARGET_SUFFIX = "_ik_target"
IK_POLE_SUFFIX = "_ik_pole"


def _armatures(self, context):
    return [
        (o.name, o.name, "")
        for o in bpy.data.objects
        if o.type == 'ARMATURE'
    ]


def _bones(self, context):
    props = context.scene.sub_weapon_rig
    owner = bpy.data.objects.get(props.owner_armature)
    if owner is None or owner.type != 'ARMATURE':
        return [('NONE', 'No armature selected', '')]
    return [(b.name, b.name, "") for b in owner.data.bones]


class SUB_PG_weapon_rig(bpy.types.PropertyGroup):
    owner_armature: bpy.props.EnumProperty(
        name="Fighter",
        description="Armature the weapon hangs off",
        items=_armatures,
    )
    owner_bone: bpy.props.EnumProperty(
        name="Bone",
        description="Bone on the fighter the weapon's root follows",
        items=_bones,
    )
    rot_offset: bpy.props.FloatVectorProperty(
        name="Rotation Offset",
        description="Matches the offset the game applies when it constrains the article",
        size=3,
        default=tuple(math.radians(a) for a in DEFAULT_ROT_OFFSET_DEG),
        subtype='EULER',
    )
    ik_chain_length: bpy.props.IntProperty(
        name="Chain Length",
        description="Bones from the tip back toward the root the IK may bend. 0 uses the whole chain",
        default=0,
        min=0,
    )
    ik_use_pole: bpy.props.BoolProperty(
        name="Pole Target",
        description="Add a pole so the chain's bend direction can be steered",
        default=False,
    )


def _chain_from(bone):
    """The straight run of bones from `bone` down to the tip.

    Walks children while there is exactly one, which is what a finger or a whip
    is. It stops at the first fork rather than guessing which way to go.
    """
    chain = [bone]
    current = bone
    while len(current.children) == 1:
        current = current.children[0]
        chain.append(current)
    return chain


def _selected_chain(context):
    """The chain under the active bone, longest-first from the active bone."""
    arma = context.object
    if arma is None or arma.type != 'ARMATURE':
        return None, "Select the weapon armature"
    active = context.active_pose_bone
    if active is None:
        return None, "Select the bone the chain starts at, in Pose Mode"
    chain = _chain_from(active)
    if len(chain) < 2:
        return None, f"'{active.name}' has no chain under it"
    return chain, None


class SUB_OP_weapon_attach(bpy.types.Operator):
    """Hang the selected weapon armature off a bone on the fighter"""
    bl_idname = "sub.weapon_attach"
    bl_label = "Attach Weapon To Fighter"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'ARMATURE'

    def execute(self, context):
        props = context.scene.sub_weapon_rig
        weapon = context.object
        owner = bpy.data.objects.get(props.owner_armature)

        if owner is None or owner.type != 'ARMATURE':
            self.report({'ERROR'}, "Pick a fighter armature first")
            return {'CANCELLED'}
        if owner == weapon:
            self.report({'ERROR'}, "The weapon and the fighter are the same armature")
            return {'CANCELLED'}
        if props.owner_bone in ('', 'NONE') or props.owner_bone not in owner.data.bones:
            self.report({'ERROR'}, "Pick a bone on the fighter")
            return {'CANCELLED'}

        for c in [c for c in weapon.constraints if c.name == ATTACH_CONSTRAINT]:
            weapon.constraints.remove(c)

        con = weapon.constraints.new('CHILD_OF')
        con.name = ATTACH_CONSTRAINT
        con.target = owner
        con.subtarget = props.owner_bone

        # The property is already radians; converting again is what turned 90
        # into 5156 degrees.
        offset = Euler(props.rot_offset, 'XYZ').to_matrix().to_4x4()

        # Child Of resolves as: world = target @ inverse @ basis.
        #
        # Leaving the inverse to "set inverse" keeps whatever gap the weapon
        # already had from the bone, which is the offset it was landing with.
        # Solving for the inverse instead puts the weapon exactly on the bone.
        #
        # `target` is measured rather than assumed: whether Blender resolves a
        # bone subtarget at the head or the tail does not have to be known if
        # the answer is read back from the scene.
        con.inverse_matrix = Matrix.Identity(4)
        weapon.matrix_basis = Matrix.Identity(4)
        context.view_layer.update()
        target = weapon.matrix_world.copy()

        bone = owner.pose.bones[props.owner_bone]
        bone_world = owner.matrix_world @ bone.matrix

        try:
            con.inverse_matrix = target.inverted() @ bone_world
        except ValueError:
            self.report({'ERROR'}, "Could not resolve the bone's transform")
            weapon.constraints.remove(con)
            return {'CANCELLED'}

        weapon.matrix_basis = offset
        context.view_layer.update()

        landed = (weapon.matrix_world.translation - bone_world.translation).length
        self.report(
            {'INFO'},
            f"{weapon.name} attached to {owner.name}:{props.owner_bone} "
            f"(root {landed:.4f} from the bone)",
        )
        return {'FINISHED'}


class SUB_OP_weapon_detach(bpy.types.Operator):
    """Take the weapon off the fighter and put it back at the origin"""
    bl_idname = "sub.weapon_detach"
    bl_label = "Detach For Export"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'ARMATURE'

    def execute(self, context):
        weapon = context.object
        removed = 0
        for c in [c for c in weapon.constraints if c.name == ATTACH_CONSTRAINT]:
            weapon.constraints.remove(c)
            removed += 1

        weapon.matrix_basis = Matrix.Identity(4)

        # `root` has to leave as identity: the game puts the article where it
        # belongs, and anything baked in here would be applied on top of that.
        root = weapon.pose.bones.get("root")
        if root is not None:
            root.matrix_basis = Matrix.Identity(4)

        self.report({'INFO'}, f"Detached ({removed} constraint(s) removed), root cleared")
        return {'FINISHED'}


class SUB_OP_weapon_chain_ik(bpy.types.Operator):
    """Build an IK handle for the bone chain under the active bone"""
    bl_idname = "sub.weapon_chain_ik"
    bl_label = "Auto Chain IK"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE'

    def execute(self, context):
        props = context.scene.sub_weapon_rig
        chain, err = _selected_chain(context)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        arma = context.object
        tip = chain[-1]
        base_name = chain[0].name
        target_name = base_name + IK_TARGET_SUFFIX
        pole_name = base_name + IK_POLE_SUFFIX

        tip_head = arma.matrix_world @ tip.head
        tip_tail = arma.matrix_world @ tip.tail
        length = (tip_tail - tip_head).length or 1.0

        bpy.ops.object.mode_set(mode='EDIT')
        edit_bones = arma.data.edit_bones

        for name in (target_name, pole_name):
            if name in edit_bones:
                edit_bones.remove(edit_bones[name])

        world_to_local = arma.matrix_world.inverted()
        target = edit_bones.new(target_name)
        target.head = world_to_local @ tip_tail
        target.tail = world_to_local @ (tip_tail + Vector((0.0, 0.0, length)))
        target.use_deform = False

        pole = None
        if props.ik_use_pole:
            chain_dir = (tip_tail - (arma.matrix_world @ chain[0].head))
            side = Vector((0.0, -1.0, 0.0)) * max(chain_dir.length, 1.0)
            mid = (arma.matrix_world @ chain[len(chain) // 2].head) + side
            pole = edit_bones.new(pole_name)
            pole.head = world_to_local @ mid
            pole.tail = world_to_local @ (mid + Vector((0.0, 0.0, length)))
            pole.use_deform = False

        bpy.ops.object.mode_set(mode='POSE')

        tip_pose = arma.pose.bones[tip.name]
        for c in [c for c in tip_pose.constraints if c.name == IK_CONSTRAINT]:
            tip_pose.constraints.remove(c)

        ik = tip_pose.constraints.new('IK')
        ik.name = IK_CONSTRAINT
        ik.target = arma
        ik.subtarget = target_name
        # 0 means "every bone up to the armature root", which would drag the
        # article's own root around. Default to exactly the chain we walked.
        ik.chain_count = props.ik_chain_length if props.ik_chain_length > 0 else len(chain)
        if pole is not None:
            ik.pole_target = arma
            ik.pole_subtarget = pole_name

        self.report({'INFO'}, f"IK over {ik.chain_count} bones, target '{target_name}'")
        return {'FINISHED'}


class SUB_OP_weapon_bake_chain_ik(bpy.types.Operator):
    """Write the IK pose down as keyframes and remove the IK rig"""
    bl_idname = "sub.weapon_bake_chain_ik"
    bl_label = "Bake Chain IK"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE'

    def execute(self, context):
        arma = context.object
        scene = context.scene

        targets = [
            b for b in arma.pose.bones
            if any(c.name == IK_CONSTRAINT for c in b.constraints)
        ]
        if not targets:
            self.report({'ERROR'}, "No chain IK on this armature")
            return {'CANCELLED'}

        # Visual keying is the point: it samples where the bones actually are,
        # which is the only place an IK result exists. The exporter reads
        # fcurves, so without this the chain exports unanimated.
        bpy.ops.pose.select_all(action='SELECT')
        bpy.ops.nla.bake(
            frame_start=scene.frame_start,
            frame_end=scene.frame_end,
            only_selected=True,
            visual_keying=True,
            clear_constraints=False,
            use_current_action=True,
            bake_types={'POSE'},
        )

        removed_bones = set()
        for bone in targets:
            for c in [c for c in bone.constraints if c.name == IK_CONSTRAINT]:
                if c.subtarget:
                    removed_bones.add(c.subtarget)
                if c.pole_subtarget:
                    removed_bones.add(c.pole_subtarget)
                bone.constraints.remove(c)

        if removed_bones:
            bpy.ops.object.mode_set(mode='EDIT')
            for name in removed_bones:
                eb = arma.data.edit_bones.get(name)
                if eb is not None:
                    arma.data.edit_bones.remove(eb)
            bpy.ops.object.mode_set(mode='POSE')

        self.report(
            {'INFO'},
            f"Baked {len(targets)} chain(s) to keyframes, removed {len(removed_bones)} control bone(s)",
        )
        return {'FINISHED'}


class SUB_PT_weapon_rig(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Weapon Rig'

    def draw(self, context):
        layout = self.layout
        props = context.scene.sub_weapon_rig

        weapon = context.object
        attached = (
            weapon is not None
            and any(c.name == ATTACH_CONSTRAINT for c in weapon.constraints)
        )

        box = layout.box()
        box.label(text="Attach", icon='CONSTRAINT_BONE')
        box.prop(props, "owner_armature")
        box.prop(props, "owner_bone")
        box.prop(props, "rot_offset")
        row = box.row()
        row.operator(SUB_OP_weapon_attach.bl_idname, icon='LINKED')
        row.operator(SUB_OP_weapon_detach.bl_idname, icon='UNLINKED')
        if attached:
            box.label(text="Attached - detach before export", icon='INFO')

        box = layout.box()
        box.label(text="Chain IK", icon='CON_KINEMATIC')
        box.prop(props, "ik_chain_length")
        box.prop(props, "ik_use_pole")
        box.operator(SUB_OP_weapon_chain_ik.bl_idname, icon='BONE_DATA')
        box.operator(SUB_OP_weapon_bake_chain_ik.bl_idname, icon='ACTION')
        box.label(text="Bake before exporting: IK is not keyframes", icon='ERROR')


classes = (
    SUB_PG_weapon_rig,
    SUB_OP_weapon_attach,
    SUB_OP_weapon_detach,
    SUB_OP_weapon_chain_ik,
    SUB_OP_weapon_bake_chain_ik,
    SUB_PT_weapon_rig,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass
    bpy.types.Scene.sub_weapon_rig = bpy.props.PointerProperty(type=SUB_PG_weapon_rig)


def unregister():
    if hasattr(bpy.types.Scene, "sub_weapon_rig"):
        del bpy.types.Scene.sub_weapon_rig
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
