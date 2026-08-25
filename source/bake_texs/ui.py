from bpy.types import Panel


class SUB_PT_bake_texs(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Bake Textures'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        props = context.scene.sub_bake_texs_properties

        if not context.blend_data.filepath:
            layout.label(text='Save the .blend first.', icon='ERROR')
            return

        col = layout.column(align=True)
        col.prop(props, 'char_tag')
        row = col.row(align=True)
        row.prop(props, 'name_from', expand=True)

        row = layout.row(align=True)
        row.prop(props, 'use_selection_only')
        row.prop(props, 'dry_run')

        layout.separator()
        bake_col = layout.column()
        bake_col.scale_y = 1.4
        bake_col.operator('sub.bake_texs_run', icon='RENDER_STILL',
                          text='Dry Run' if props.dry_run else 'Bake Textures')

        from . import core
        out_dir = core.get_output_dir()
        row = layout.row()
        row.label(text='Nutexb folder: ' + (out_dir or '(not set - will ask)'), icon='FILE_FOLDER')

        layout.operator('sub.bake_texs_compile_only', icon='FILE_REFRESH')

        layout.separator()
        layout.operator('sub.bake_texs_apply_to_materials', icon='MATERIAL')
        layout.label(text="Doesn't happen automatically - only on click", icon='INFO')


class SUB_PT_bake_texs_channels(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Channels'
    bl_parent_id = "SUB_PT_bake_texs"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.sub_bake_texs_properties

        row = layout.row(align=True)
        row.prop(props, 'write_col', toggle=True)
        row.prop(props, 'write_nor', toggle=True)
        row.prop(props, 'write_prm', toggle=True)
        row.prop(props, 'write_emi', toggle=True)

        layout.prop(props, 'prm_ao_mode')

        skin = layout.column(align=True)
        skin.prop(props, 'prm_skin_mask_mode')
        sub = skin.row()
        sub.enabled = props.prm_skin_mask_mode == 'AUTO'
        sub.prop(props, 'prm_skin_mask_const')

        emi = layout.column(align=True)
        emi.enabled = props.write_emi
        emi.prop(props, 'emi_mode')
        emi.prop(props, 'emi_normalize_to_cv3')

        layout.prop(props, 'write_component_debug_maps')


class SUB_PT_bake_texs_output(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Bake & Output Settings'
    bl_parent_id = "SUB_PT_bake_texs"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.sub_bake_texs_properties

        layout.prop(props, 'bake_size')
        row = layout.row(align=True)
        row.prop(props, 'bake_margin')
        row.prop(props, 'bake_margin_type', text='')
        layout.prop(props, 'bake_subfolder')
        layout.prop(props, 'overwrite_files')

        layout.separator()
        layout.prop(props, 'compile_nutexb')
        sub = layout.column()
        sub.enabled = props.compile_nutexb
        sub.prop(props, 'nutexb_always_ask')
