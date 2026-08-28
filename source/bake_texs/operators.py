import bpy
import os
from bpy.types import Operator
from bpy.props import StringProperty, BoolProperty, EnumProperty

from . import core


class SUB_OP_bake_texs_run(Operator):
    """Bake COL/NOR/PRM for the selected meshes, then compile to .nutexb"""
    bl_idname = 'sub.bake_texs_run'
    bl_label = 'Bake Textures'
    bl_description = (
        'Bake COL/NOR/PRM texture sets for every material on the selected meshes. '
        'If "Compile to .nutexb" is on, also converts them into the model folder afterward'
    )
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath) and context.selected_objects

    def execute(self, context):
        props = context.scene.sub_bake_texs_properties
        core.apply_settings(props)

        out_dir = core.get_output_dir()
        if core.COMPILE_NUTEXB and not core.DRY_RUN and (not out_dir or props.nutexb_always_ask):
            bpy.ops.baketexs.pick_output('INVOKE_DEFAULT', do_bake=True)
            return {'FINISHED'}

        try:
            written = core.bake_all()
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        if core.DRY_RUN:
            self.report({'INFO'}, 'Dry run complete - see the console / _BAKE_REPORT.txt for what would bake.')
            return {'FINISHED'}

        if core.COMPILE_NUTEXB:
            ok, failed = core.compile_nutexb(written, out_dir)
            self.report({'INFO' if not failed else 'WARNING'},
                       f'Baked {len(written)} texture(s). nutexb: {ok} written'
                       + (f', {failed} failed' if failed else ''))
        else:
            self.report({'INFO'}, f'Baked {len(written)} texture(s) -> {core.BAKE_DIR}')
        return {'FINISHED'}


class SUB_OP_bake_texs_compile_only(Operator):
    """Compile already-baked PNGs to .nutexb, without re-baking"""
    bl_idname = 'sub.bake_texs_compile_only'
    bl_label = 'Compile Existing Bakes'
    bl_description = (
        'Convert the PNGs already sitting in the bake output folder into .nutexb, '
        'without baking again. Use this when you hand-edited a PNG, or a previous '
        'compile step failed'
    )
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath)

    def execute(self, context):
        props = context.scene.sub_bake_texs_properties
        core.apply_settings(props)

        pngs = core.bakes_in_folder(core.BAKE_DIR)
        if not pngs:
            self.report({'WARNING'}, f'No _col/_nor/_prm PNGs found in {core.BAKE_DIR}')
            return {'CANCELLED'}

        out_dir = core.get_output_dir()
        if not out_dir or props.nutexb_always_ask:
            bpy.ops.baketexs.pick_output('INVOKE_DEFAULT', do_bake=False)
            return {'FINISHED'}

        ok, failed = core.compile_nutexb(pngs, out_dir)
        self.report({'INFO' if not failed else 'WARNING'},
                   f'{ok} nutexb written' + (f', {failed} failed' if failed else ''))
        return {'FINISHED'}


def _find_texture_slot(material, param_id_name):
    """The existing sub_matl_data.textures entry for a param, or None."""
    sub_matl_data = getattr(material, 'sub_matl_data', None)
    if sub_matl_data is None:
        return None
    for texture in sub_matl_data.textures:
        if texture.param_id_name == param_id_name:
            return texture
    return None


def _load_or_refresh_image(filepath, name, colorspace):
    """Load a baked PNG as an Image named exactly `name` (no extension).

    Reuses the existing datablock of that name on a re-apply (pointing it at
    the file and reloading), rather than piling up Body_col.001, .002, ... on
    every re-bake.
    """
    existing = bpy.data.images.get(name)
    if existing is not None:
        existing.filepath = filepath
        existing.source = 'FILE'
        try:
            existing.reload()
        except Exception:
            pass
        core.set_colorspace(existing, colorspace)
        return existing
    img = bpy.data.images.load(filepath, check_existing=False)
    img.name = name
    core.set_colorspace(img, colorspace)
    return img


class SUB_OP_bake_texs_apply_to_materials(Operator):
    """Point each baked group's base material at its freshly baked textures"""
    bl_idname = 'sub.bake_texs_apply_to_materials'
    bl_label = 'Apply Baked Textures to Materials'
    bl_description = (
        'Load the baked COL/NOR/PRM/EMI PNGs and assign them onto each baked '
        'material\'s own texture slots, so Export Model actually picks them up. '
        'Only touches the base material of a bundled group (e.g. Body, not BodyShiny) - '
        'export already points BodyShiny at Body\'s textures on its own. '
        'This never happens automatically when you bake - run it explicitly whenever '
        'you actually want the bake to become the live material'
    )
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(
        name='Apply To',
        description='Which copy of each material gets the baked textures',
        items=(
            ('BOTH', 'Material And Side-Loaded Copy',
             'Assign to the mesh material and to its "(Side-Loaded)" twin when '
             'one exists. The safe default - a twin describes the same surface, '
             'so it wants the same freshly baked maps'),
            ('LIVE', 'Material Only',
             'Only the material assigned to the mesh. A side-loaded twin keeps '
             'whatever textures it already had'),
            ('SIDE_LOADED', 'Side-Loaded Copy Only',
             'Only the "(Side-Loaded)" twin, leaving the mesh material untouched'),
        ),
        default='BOTH',
    )

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath) and context.selected_objects

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'target')

        scene_props = context.scene.sub_scene_properties
        if (self.target != 'LIVE'
                and scene_props.export_prefer_sideloaded_materials):
            box = layout.box()
            box.label(text='Export is reading from side-loaded copies,', icon='INFO')
            box.label(text='so those are the ones that need the bakes.')

    def invoke(self, context, event):
        # When export is already set to read from side-loaded data, applying
        # only to the mesh materials would put the new bakes somewhere export
        # never looks - which is exactly the case where this silently does
        # nothing useful.
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        props = context.scene.sub_bake_texs_properties
        core.apply_settings(props)

        try:
            groups, _ = core.collect_targets()
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        applied = 0
        notes = []
        twins_written = 0
        missing_twins = []

        for group_name, group in groups.items():
            base_material = group['materials'][0]
            stem = core.make_stem(group['objects'][0], base_material)

            targets = self._target_materials(base_material)
            if not targets:
                missing_twins.append(base_material.name)
                continue
            twins_written += sum(1 for m in targets if m is not base_material)

            for material in targets:
                for suffix, param_id_name, colorspace in (
                    ('_col', 'Texture0', 'sRGB'),
                    ('_nor', 'Texture4', 'Non-Color'),
                    ('_prm', 'Texture6', 'Non-Color'),
                    ('_emi', 'Texture5', 'sRGB'),
                ):
                    png_path = os.path.join(core.BAKE_DIR, f'{stem}{suffix}.png')
                    if not os.path.isfile(png_path):
                        continue
                    slot = _find_texture_slot(material, param_id_name)
                    if slot is None:
                        notes.append(f'{material.name}: no {param_id_name} slot for {stem}{suffix} - skipped')
                        continue
                    slot.image = _load_or_refresh_image(png_path, f'{stem}{suffix}', colorspace)
                    applied += 1

                # An EMI map baked from an Emission Strength above 1 was divided
                # down to fit in 8 bits, and CustomVector3 is what puts the
                # brightness back. Applying the texture without it would render the
                # glow dimmer than it was authored.
                bake_info = core.LAST_BAKE_INFO.get(stem)
                if bake_info and 'custom_vector_3' in bake_info:
                    cv3_value = bake_info['custom_vector_3']
                    sub_matl_data = getattr(material, 'sub_matl_data', None)
                    cv3 = sub_matl_data.vectors.get('CustomVector3') if sub_matl_data else None
                    if cv3 is not None:
                        cv3.value = cv3_value
                        if max(cv3_value[:3]) > 1.0:
                            notes.append(
                                f'{material.name}: CustomVector3 set to '
                                f'{cv3_value[0]:.3f} to restore EMI brightness')
                    elif max(cv3_value[:3]) > 1.0:
                        notes.append(
                            f'{material.name}: needs CustomVector3 = '
                            f'{cv3_value[0]:.3f} for the EMI map, but has no such '
                            f'parameter - apply the Emissive preset')

        if missing_twins:
            notes.append(
                'no side-loaded twin for ' + ', '.join(sorted(missing_twins))
                + ' - nothing written for those')

        msg = f'Applied {applied} texture(s) to material slots.'
        if twins_written:
            msg += f' {twins_written} side-loaded copy/copies updated.'
        if notes:
            msg += ' ' + '; '.join(dict.fromkeys(notes))
        self.report({'INFO' if not notes else 'WARNING'}, msg)
        return {'FINISHED'}

    def _target_materials(self, base_material):
        """Which materials this run should write the baked textures onto.

        A side-loaded twin is never assigned to a mesh, so it never turns up in
        the bake's own target list - which is why baking and applying could
        leave the twin pointing at stale textures while export, reading from
        the twin, quietly ignored everything that was just baked.
        """
        from ..model.material.create_matl_from_blender_materials import get_side_loaded_twin
        from ..material_grouping import SIDE_LOAD_SUFFIX

        # Already a twin: write to it and stop, rather than hunting for a twin
        # of a twin.
        if base_material.name.endswith(SIDE_LOAD_SUFFIX):
            return [base_material]

        twin = get_side_loaded_twin(base_material)

        if self.target == 'LIVE':
            return [base_material]
        if self.target == 'SIDE_LOADED':
            return [twin] if twin is not None else []
        return [base_material] + ([twin] if twin is not None else [])


class SUB_OP_bake_texs_pick_output(Operator):
    """Choose the model folder that .nutexb files are written to"""
    bl_idname = 'baketexs.pick_output'
    bl_label = 'Select Model Folder for .nutexb Output'

    directory: StringProperty(subtype='DIR_PATH')
    filter_folder: BoolProperty(default=True, options={'HIDDEN'})
    do_bake: BoolProperty(default=True, options={'HIDDEN'})

    def invoke(self, context, event):
        self.directory = core.get_output_dir() or bpy.path.abspath("//")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not os.path.isdir(self.directory):
            self.report({'ERROR'}, "That is not a folder")
            return {'CANCELLED'}
        core.set_output_dir(self.directory)

        try:
            written = core.bake_all() if self.do_bake else core.bakes_in_folder(core.BAKE_DIR)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        ok, failed = core.compile_nutexb(written, self.directory)
        self.report({'INFO' if not failed else 'WARNING'},
                   f'{ok} nutexb written' + (f', {failed} failed' if failed else ''))
        return {'FINISHED'}


def _stem_for_material(material, stems):
    """The bake stem for a material, or None if nothing uses it.

    Prefers the stem the current selection would produce, so it agrees exactly
    with what a bake just wrote. Falls back to any mesh in the file that uses
    the material, because stacking is routinely done on materials that are not
    part of the current selection - which is the case that used to pass None
    into make_stem() and crash on obj.name.
    """
    if material.name in stems:
        return stems[material.name]
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        for slot in obj.material_slots:
            if slot.material is material:
                return core.make_stem(obj, material)
    return None


class SUB_OP_bake_texs_stack_materials(Operator):
    """Stack two materials' baked maps into one texture, switched by CustomVector6"""
    bl_idname = 'sub.bake_texs_stack_materials'
    bl_label = 'Stack Two Materials'
    bl_description = (
        'Combine two already-baked materials into one vertically stacked '
        'texture set, so a single material can switch between them by '
        'animating CustomVector6.w. Bake both materials first'
    )
    bl_options = {'REGISTER', 'UNDO'}

    top_material: StringProperty(
        name='Top (default)',
        description='The look shown at rest, placed in the upper half. CustomVector6.w = 0 selects it',
    )
    bottom_material: StringProperty(
        name='Bottom (switched to)',
        description='The look switched to, placed in the lower half. CustomVector6.w = -1 selects it',
    )
    half_height: BoolProperty(
        name='Keep Original Size',
        description=(
            'Squeeze each half so the atlas stays the size of one texture, at '
            'half the vertical resolution. Off, the atlas is twice as tall and '
            'neither half loses detail'
        ),
        default=False,
    )
    uv_mode: EnumProperty(
        name='UVs',
        description='How the mesh is aimed at the top half of the atlas',
        items=(
            ('REMAP', 'Remap Mesh UVs',
             'Rewrite the mesh V coordinates into the top half now, so the face '
             'is not stretched and Blender previews it correctly. '
             'CustomVector6 stays at identity scale; switch with .w = -0.5'),
            ('SHADER', 'Leave UVs Alone',
             'Keep the mesh at 0-1 and let CustomVector6.y squeeze it at '
             'runtime. Correct in game, but Blender has no CustomVector6 '
             'wiring so the viewport will look stretched. Switch with .w = -1'),
        ),
        default='REMAP',
    )
    compile_nutexb: BoolProperty(
        name='Compile To .nutexb',
        description=(
            'Convert the stacked PNGs into the model folder afterwards, the '
            'same way baking does, so they are ready to ship without a '
            'separate compile step'
        ),
        default=True,
    )
    apply_to_material: BoolProperty(
        name='Apply To Top Material',
        description=(
            'Point the top material at the stacked maps and set its '
            'CustomVector6 to (1, 0.5, 0, 0)'
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath)

    def invoke(self, context, event):
        obj = context.object
        if obj is not None and obj.type == 'MESH' and len(obj.material_slots) >= 2:
            slots = [s.material.name for s in obj.material_slots if s.material]
            if len(slots) >= 2:
                self.top_material, self.bottom_material = slots[0], slots[1]
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        layout = self.layout
        layout.prop_search(self, 'top_material', bpy.data, 'materials')
        layout.prop_search(self, 'bottom_material', bpy.data, 'materials')
        layout.prop(self, 'half_height')
        layout.prop(self, 'uv_mode')
        layout.prop(self, 'apply_to_material')
        layout.prop(self, 'compile_nutexb')

        from . import stack_textures
        bottom_w = (stack_textures.REMAP_W_BOTTOM if self.uv_mode == 'REMAP'
                    else stack_textures.STACK_W_BOTTOM)
        box = layout.box()
        box.label(text='After stacking, animate CustomVector6.w:', icon='ANIM')
        box.label(text='       0    = top texture (default)')
        box.label(text=f'    {bottom_w}   = bottom texture')
        box.label(text='Nor and PRM are stacked too - the UV shift')
        box.label(text='moves every map, not just Col.')

        if self.uv_mode == 'REMAP':
            warn = layout.box()
            warn.label(text='Mesh UVs will be edited (undoable).', icon='ERROR')
            warn.label(text='Bake BEFORE stacking - baking afterwards')
            warn.label(text='would bake into the top half only.')

    def execute(self, context):
        from . import stack_textures

        props = context.scene.sub_bake_texs_properties
        core.apply_settings(props)

        top = bpy.data.materials.get(self.top_material)
        bottom = bpy.data.materials.get(self.bottom_material)
        if top is None or bottom is None:
            self.report({'ERROR'}, 'Pick two materials that exist.')
            return {'CANCELLED'}
        if top is bottom:
            self.report({'ERROR'}, 'Top and bottom are the same material.')
            return {'CANCELLED'}

        # Stems come from the same naming the bake used, so the files line up
        # with whatever was actually written.
        try:
            groups, _ = core.collect_targets()
        except Exception:
            groups = {}
        stems = {}
        for group in groups.values():
            base = group['materials'][0]
            stems[base.name] = core.make_stem(group['objects'][0], base)

        top_stem = _stem_for_material(top, stems)
        bottom_stem = _stem_for_material(bottom, stems)
        missing_stem = [m.name for m, s in ((top, top_stem), (bottom, bottom_stem))
                        if s is None]
        if missing_stem:
            self.report(
                {'ERROR'},
                f'No mesh in this file uses {" or ".join(missing_stem)}, so the '
                f'baked file name cannot be worked out. Assign the material to a '
                f'mesh, or pick the material that was actually baked.')
            return {'CANCELLED'}

        try:
            written = stack_textures.stack_material_bakes(
                self, top, bottom, top_stem, bottom_stem,
                core.BAKE_DIR, top_stem, self.half_height)
        except Exception as e:
            self.report({'ERROR'}, f'Could not stack: {e}')
            return {'CANCELLED'}

        if not written:
            self.report({'WARNING'},
                        f'No baked maps found for "{top_stem}" and "{bottom_stem}" '
                        f'in {core.BAKE_DIR}. Bake both materials first.')
            return {'CANCELLED'}

        message = f'Stacked {len(written)} map(s) into {top_stem}_*'

        remapped = []
        if self.uv_mode == 'REMAP':
            remapped = stack_textures.remap_uvs_to_top_half(
                stack_textures.meshes_using_material(top))
            if remapped:
                message += f'; remapped UVs on {len(remapped)} mesh(es)'
            else:
                message += '; no mesh uses the top material, so no UVs were remapped'

        if self.apply_to_material:
            applied, notes = stack_textures.apply_stacked_textures(
                top, top_stem, core.BAKE_DIR, self.uv_mode)
            uv_transform = (stack_textures.REMAP_UV_TRANSFORM
                            if self.uv_mode == 'REMAP'
                            else stack_textures.STACK_UV_TRANSFORM)
            message += (f'; applied {applied} to {top.name}, '
                        f'CustomVector6 = {uv_transform}')
            if notes:
                message += '; ' + '; '.join(notes)

        if self.compile_nutexb:
            out_dir = core.get_output_dir()
            if out_dir:
                ok, failed = core.compile_nutexb(written, out_dir)
                message += f'; {ok} nutexb written'
                if failed:
                    message += f', {failed} failed'
            else:
                message += '; no nutexb folder set - run Bake once to choose one'

        print('\n' + '=' * 66)
        print(f'Stacked textures: {top.name} (top) over {bottom.name} (bottom)')
        for path in written:
            print(f'  {os.path.basename(path)}')
        print('\nAnimate CustomVector6.w on this material:')
        print(f'   {stack_textures.STACK_W_TOP:>5}  -> {top.name}')
        print(f'   {stack_textures.STACK_W_BOTTOM:>5}  -> {bottom.name}')
        print('=' * 66 + '\n')

        self.report({'INFO'}, message)
        return {'FINISHED'}


class SUB_OP_bake_texs_remap_uvs(Operator):
    """Move the selected meshes' UVs into the top half of a stacked texture"""
    bl_idname = 'sub.bake_texs_remap_uvs'
    bl_label = 'Fix UVs For Stacked Texture'
    bl_description = (
        'Rewrite the selected meshes\' V coordinates so they point at the top '
        'half of a vertically stacked texture instead of stretching over the '
        'whole thing. Also restores them for re-baking'
    )
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(
        name='Direction',
        items=(
            ('TOP_HALF', 'Fit To Top Half',
             'V 0-1 becomes 0.5-1.0, so the mesh samples the top image of a '
             'stacked pair at its correct proportions'),
            ('RESTORE', 'Restore Full Range',
             'V 0.5-1.0 becomes 0-1 again. Do this before re-baking, or the '
             'bake lands in the top half of a fresh texture'),
        ),
        default='TOP_HALF',
    )
    set_uv_transform: BoolProperty(
        name='Set CustomVector6',
        description=(
            'Also set CustomVector6 on the meshes\' materials to match - '
            'identity for the top half, so only .w needs animating'
        ),
        default=True,
    )
    force: BoolProperty(
        name='Run Anyway',
        description=(
            'Skip the check that stops a mesh being remapped twice. Remapping '
            'twice squeezes the UVs into a quarter of the atlas'
        ),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        from . import stack_textures
        layout = self.layout
        layout.prop(self, 'mode')
        layout.prop(self, 'set_uv_transform')

        meshes = [o for o in context.selected_objects if o.type == 'MESH']
        box = layout.box()
        box.label(text=f'{len(meshes)} selected mesh(es):', icon='MESH_DATA')
        for obj in meshes[:6]:
            span = stack_textures.uv_v_range(obj)
            if span is None:
                box.label(text=f'   {obj.name}: no UVs')
                continue
            state = ' (already in top half)' if stack_textures.looks_remapped(obj) else ''
            box.label(text=f'   {obj.name}: V {span[0]:.2f}-{span[1]:.2f}{state}')
        if len(meshes) > 6:
            box.label(text=f'   ...and {len(meshes) - 6} more')

        wrong_way = [o for o in meshes
                     if stack_textures.looks_remapped(o) == (self.mode == 'TOP_HALF')]
        if wrong_way:
            warn = layout.box()
            if self.mode == 'TOP_HALF':
                warn.label(text=f'{len(wrong_way)} mesh(es) already look remapped.',
                           icon='ERROR')
                warn.label(text='They will be skipped unless you force it.')
            else:
                warn.label(text=f'{len(wrong_way)} mesh(es) are already full range.',
                           icon='ERROR')
                warn.label(text='They will be skipped unless you force it.')
            warn.prop(self, 'force')

    def execute(self, context):
        from . import stack_textures

        meshes = [o for o in context.selected_objects if o.type == 'MESH']
        if not meshes:
            self.report({'WARNING'}, 'Select at least one mesh.')
            return {'CANCELLED'}

        want_remapped = self.mode == 'TOP_HALF'
        targets, skipped = [], []
        for obj in meshes:
            already = stack_textures.looks_remapped(obj)
            # Skip a mesh that is already in the state being asked for -
            # running either direction twice is destructive and silent.
            if already == want_remapped and not self.force:
                skipped.append(obj.name)
            else:
                targets.append(obj)

        if not targets:
            self.report(
                {'WARNING'},
                f'Nothing to do - {len(skipped)} mesh(es) are already '
                f'{"in the top half" if want_remapped else "at full range"}. '
                f'Tick "Run Anyway" to override.')
            return {'CANCELLED'}

        if want_remapped:
            changed = stack_textures.remap_uvs_to_top_half(targets)
            transform = stack_textures.REMAP_UV_TRANSFORM
        else:
            changed = stack_textures.restore_uvs_from_full_range(targets)
            transform = None

        materials_touched = set()
        if self.set_uv_transform and transform is not None:
            for obj in targets:
                for slot in obj.material_slots:
                    material = slot.material
                    if material is None or material.name in materials_touched:
                        continue
                    sub_matl_data = getattr(material, 'sub_matl_data', None)
                    if sub_matl_data is None:
                        continue
                    uv_transform = sub_matl_data.vectors.get('CustomVector6')
                    if uv_transform is not None:
                        uv_transform.value = transform
                        materials_touched.add(material.name)

        message = (f'{"Fitted" if want_remapped else "Restored"} UVs on '
                   f'{len(changed)} mesh(es)')
        if materials_touched:
            message += f'; CustomVector6 set on {len(materials_touched)} material(s)'
        if skipped:
            message += f'; skipped {len(skipped)} already done'
        if want_remapped:
            message += f'; switch with CustomVector6.w = {stack_textures.REMAP_W_BOTTOM}'

        self.report({'INFO'}, message)
        return {'FINISHED'}
