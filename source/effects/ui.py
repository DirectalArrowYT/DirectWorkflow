import os

import bpy
from bpy.types import Panel

from . import forge


class SUB_PT_smash_fx(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Smash FX'
    bl_label = 'Smash Effects'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sub_smash_fx

        col = layout.column(align=True)
        col.prop(settings, 'source', text='')
        if settings.source == 'CUSTOM':
            col.prop(settings, 'custom_eff', text='')
        row = layout.row()
        row.scale_y = 1.3
        bone = context.mode == 'POSE' and context.active_pose_bone
        row.operator('sub.smash_fx_add', icon='SHADERFX',
                     text=f'Add Effect on {context.active_pose_bone.name}' if bone else 'Add Effect')

        box = layout.box()
        box.label(text='From a Move Script', icon='SCRIPT')
        col = box.column(align=True)
        col.prop(settings, 'acmd_file', text='')
        if settings.acmd_file:
            col.prop(settings, 'acmd_script', text='')
        row = box.row()
        row.enabled = bool(settings.acmd_file) and context.object is not None and context.object.type == 'ARMATURE'
        op = row.operator('sub.smash_fx_import_acmd', icon='IMPORT')
        op.start = context.scene.frame_current

        obj = context.object
        if obj is None or not obj.smash_fx.enabled:
            layout.label(text='Select an effect empty to edit it', icon='INFO')
            return
        fx = obj.smash_fx
        box = layout.box()
        box.label(text=obj.name, icon='OUTLINER_OB_EMPTY')
        box.prop(fx, 'eff')
        eff = os.path.normpath(bpy.path.abspath(fx.eff)) if fx.eff else ''
        if eff and eff == settings.listed_eff and not settings.list_error:
            box.prop_search(fx, 'effect', settings, 'listed', icon='SHADERFX')
            listed = settings.listed.get(fx.effect)
            if listed:
                loops = ', loops' if listed.loops else ''
                box.label(text=f'{listed.layers} layers, {listed.length} frames{loops}')
            elif fx.effect:
                box.label(text=f'{fx.effect} is not in this file', icon='ERROR')
        else:
            box.prop(fx, 'effect')
            if settings.list_error and eff == settings.listed_eff:
                box.label(text=settings.list_error, icon='ERROR')
            box.operator('sub.smash_fx_list', icon='FILE_REFRESH')

        col = box.column(align=True)
        col.prop(fx, 'start')
        row = col.row(align=True)
        row.prop(fx, 'use_stop', text='')
        sub = row.row(align=True)
        sub.active = fx.use_stop
        sub.prop(fx, 'stop')
        row = col.row(align=True)
        row.prop(fx, 'use_end', text='')
        sub = row.row(align=True)
        sub.active = fx.use_end
        sub.prop(fx, 'end')
        box.operator('sub.smash_fx_fit_length', icon='PREVIEW_RANGE')

        col = box.column(align=True)
        col.prop(fx, 'rate')
        col.prop(fx, 'seed')
        box.prop(fx, 'colour')
        box.prop(fx, 'play')
        box.label(text='Scale the empty to size the effect', icon='INFO')


class SUB_PT_smash_fx_bake(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Smash FX'
    bl_label = 'Bake'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.sub_smash_fx

        count = len(forge.effect_objects(scene))
        layout.label(text=f'{count} effect{"" if count == 1 else "s"}, frames {scene.frame_start}-{scene.frame_end}')
        layout.prop(settings, 'out')
        layout.prop(settings, 'geometry')
        row = layout.row(align=True)
        row.active = not settings.geometry
        row.prop(settings, 'show_in_camera', toggle=True)
        row.prop(settings, 'composite', toggle=True)

        box = layout.box()
        box.prop(settings, 'lights')
        col = box.column(align=True)
        col.active = settings.lights
        col.prop(settings, 'light_strength')
        col.prop(settings, 'light_size')
        col.prop(settings, 'light_shadows')
        row = layout.row()
        row.active = not settings.geometry
        row.prop(settings, 'cards')
        box = layout.box()
        box.active = not settings.geometry
        box.prop(settings, 'bloom')
        col = box.column(align=True)
        col.active = settings.bloom and settings.composite
        col.prop(settings, 'bloom_strength')
        col.prop(settings, 'bloom_size')

        box = layout.box()
        box.prop(settings, 'use_shot')
        box.operator('sub.smash_fx_render_shot', icon='RENDER_ANIMATION')

        col = layout.column()
        col.scale_y = 1.4
        col.operator('sub.smash_fx_bake', icon='RENDER_STILL')
        if scene.view_settings.view_transform != 'Standard':
            layout.label(text='Standard view transform matches the game', icon='INFO')


class SUB_PT_smash_fx_settings(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Smash FX'
    bl_label = 'Settings'
    bl_parent_id = 'SUB_PT_smash_fx_bake'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        settings = context.scene.sub_smash_fx
        layout = self.layout
        layout.prop(settings, 'forge')
        layout.prop(settings, 'game_root')


classes = (
    SUB_PT_smash_fx,
    SUB_PT_smash_fx_bake,
    SUB_PT_smash_fx_settings,
)
