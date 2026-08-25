"""Operators for the swing bone physics rig (see physics.py)."""

import bpy
from bpy.types import Operator
from bpy.props import IntProperty

from . import physics


def _swing_armature(context):
    obj = getattr(context, 'object', None)
    if obj is None or obj.type != 'ARMATURE':
        return None
    if getattr(obj.data, 'sub_swing_data', None) is None:
        return None
    return obj


def _has_chains(arma_obj):
    return len(arma_obj.data.sub_swing_data.swing_bone_chains) > 0


class SUB_OP_swing_physics_build(Operator):
    """Turn this armature's swing chains into a live Blender rigid body sim"""
    bl_idname = 'sub.swing_physics_build'
    bl_label = 'Enable Swing Physics'
    bl_description = (
        'Build a rigid body simulation from the swing data so the chains move '
        'in Blender. Collision shapes become colliders, and each bone only '
        'collides with the shapes its swing data lists. Play the timeline to '
        'see it; nothing is keyframed until you bake'
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arma_obj = _swing_armature(context)
        return arma_obj is not None and _has_chains(arma_obj)

    def execute(self, context):
        arma_obj = _swing_armature(context)
        settings = context.scene.sub_scene_properties
        try:
            built = physics.build_physics(self, context, arma_obj, settings)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f'Could not build swing physics: {e}')
            return {'CANCELLED'}

        if built == 0:
            self.report({'WARNING'},
                        'No swing chains could be built - the chains have no bones '
                        'that exist on this armature.')
            return {'CANCELLED'}

        # A simulation has to be watched from its first frame; jumping in
        # halfway gives the solver no history and the chain snaps.
        context.scene.frame_set(context.scene.frame_start)
        return {'FINISHED'}


class SUB_OP_swing_physics_remove(Operator):
    """Remove the generated physics rig and put the bones back"""
    bl_idname = 'sub.swing_physics_remove'
    bl_label = 'Disable Swing Physics'
    bl_description = (
        'Delete the generated proxies, joints and bone constraints, and take '
        'the rigid body settings off the collision shapes. Your swing data and '
        'collision shape objects are left alone'
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arma_obj = _swing_armature(context)
        return arma_obj is not None and physics.has_physics(arma_obj)

    def execute(self, context):
        arma_obj = _swing_armature(context)
        removed = physics.remove_physics(context, arma_obj)
        self.report({'INFO'}, f'Swing physics removed ({removed} object(s)).')
        return {'FINISHED'}


class SUB_OP_swing_physics_bake(Operator):
    """Bake the simulated swing motion into pose keyframes"""
    bl_idname = 'sub.swing_physics_bake'
    bl_label = 'Bake Swing Physics'
    bl_description = (
        'Keyframe the simulated motion onto the swing bones over the frame '
        'range, then remove the physics rig. The result is ordinary pose '
        'animation you can edit by hand'
    )
    bl_options = {'REGISTER', 'UNDO'}

    frame_start: IntProperty(name='Start Frame', default=1)
    frame_end: IntProperty(name='End Frame', default=250)

    @classmethod
    def poll(cls, context):
        arma_obj = _swing_armature(context)
        return arma_obj is not None and physics.has_physics(arma_obj)

    def invoke(self, context, event):
        self.frame_start = context.scene.frame_start
        self.frame_end = context.scene.frame_end
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.prop(self, 'frame_start')
        row.prop(self, 'frame_end')
        box = layout.box()
        box.label(text='Baking replaces the simulation with keyframes', icon='INFO')
        box.label(text='and removes the physics rig.')

    def execute(self, context):
        arma_obj = _swing_armature(context)
        if self.frame_end < self.frame_start:
            self.report({'ERROR'}, 'End frame is before the start frame.')
            return {'CANCELLED'}
        ok = physics.bake_physics(self, context, arma_obj, self.frame_start, self.frame_end)
        return {'FINISHED'} if ok else {'CANCELLED'}
