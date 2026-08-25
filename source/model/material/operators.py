import bpy
from bpy.types import Operator
from bpy.props import StringProperty, EnumProperty, BoolProperty
from .load_from_shader_label import (
    is_valid_shader_label, create_sub_matl_data_from_shader_label, apply_material_preset,
)
from . import presets as material_presets
from . import shader_info
from . import validate
from ...material_grouping import SIDE_LOAD_SUFFIX, side_loaded_name


def _active_smash_material(context):
    """The active material, if it is a set-up Smash material."""
    obj = getattr(context, 'object', None)
    if obj is None or obj.type != 'MESH':
        return None
    material = obj.active_material
    if material is None:
        return None
    sub_matl_data = getattr(material, 'sub_matl_data', None)
    if sub_matl_data is None or not sub_matl_data.shader_label:
        return None
    return material


class SUB_OP_change_render_pass(Operator):
    """Move a material between render passes without touching anything else"""
    bl_idname = 'sub.change_render_pass'
    bl_label = 'Change Render Pass'
    bl_description = (
        'Switch which render pass this material draws in. Same shader program '
        'and same parameter values - only the pass tag on the end of the '
        'shader label changes'
    )
    bl_options = {'REGISTER', 'UNDO'}

    render_pass: EnumProperty(
        name='Render Pass',
        description='Which pass this material draws in',
        items=(
            ('_opaque', 'Opaque',
             'Drawn first, before any transparency. Correct for solid surfaces'),
            ('_far', 'Far',
             'Alpha blending pass, drawn before _sort. For transparent geometry '
             'meant to sit behind other transparent geometry'),
            ('_sort', 'Sort',
             'Alpha blending pass, drawn after _far. The usual choice for '
             'transparent surfaces'),
            ('_near', 'Near',
             'Drawn after the bloom pass, so this material never contributes to '
             'bloom. Use for bright surfaces that should not glow'),
        ),
        default='_opaque',
    )

    @classmethod
    def poll(cls, context):
        return _active_smash_material(context) is not None

    def execute(self, context):
        material = _active_smash_material(context)
        sub_matl_data = material.sub_matl_data
        new_label = shader_info.with_render_pass(sub_matl_data.shader_label, self.render_pass)
        sub_matl_data.set_shader_label(new_label)

        from .create_blender_materials_from_matl import setup_blender_material_settings
        setup_blender_material_settings(material)

        self.report({'INFO'}, f'{material.name} now renders in the {self.render_pass[1:]} pass.')
        return {'FINISHED'}

    def invoke(self, context, event):
        material = _active_smash_material(context)
        current = shader_info.render_pass_of(material.sub_matl_data.shader_label)
        if current:
            self.render_pass = current
        return context.window_manager.invoke_props_dialog(self)


class SUB_OP_apply_material_preset(Operator):
    """Set this material up as a ready-made Smash material"""
    bl_idname = 'sub.apply_material_preset'
    bl_label = 'Apply Material Preset'
    bl_description = (
        'Set the shader and parameter values from a ready-made preset. Your '
        'assigned textures are kept - only the shader and its parameters change'
    )
    bl_options = {'REGISTER', 'UNDO'}

    preset_name: EnumProperty(
        name='Preset',
        description='Which material setup to apply',
        items=lambda self, context: material_presets.enum_items(),
    )
    apply_to_selected: BoolProperty(
        name='All Selected Objects',
        description=(
            'Apply to every material on every selected mesh, instead of just '
            'the active material'
        ),
        default=False,
    )
    target: EnumProperty(
        name='Apply To',
        description='Which material data the preset is written into',
        items=(
            ('LIVE', 'Live Material',
             'Change the material assigned to the mesh. What you see in the '
             'viewport updates immediately'),
            ('SIDE_LOADED', 'Side-Loaded Copy',
             'Leave the mesh material completely alone and write into its '
             '"(Side-Loaded)" twin instead, creating one if it does not exist. '
             'Export reads from the twin when "Prefer Side-Loaded Materials" '
             'is on'),
        ),
        default='LIVE',
    )

    @classmethod
    def poll(cls, context):
        obj = getattr(context, 'object', None)
        return obj is not None and obj.type == 'MESH' and obj.active_material is not None

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'preset_name')

        preset = material_presets.get(self.preset_name)
        if preset is not None:
            box = layout.box()
            box.label(text=preset.description, icon='INFO')

            textures = sorted(preset.textures,
                              key=lambda t: int(t.replace('Texture', '')))
            if textures:
                box.label(text='Uses: ' + ', '.join(textures), icon='TEXTURE')

            uses = preset.fighter_usage
            if uses:
                box.label(text=f'{uses} vanilla fighter materials use this shader.')

            if preset.notes:
                note_box = layout.box()
                note_box.label(text='Note', icon='QUESTION')
                for line in _wrap(preset.notes, 62):
                    note_box.label(text=line)

        layout.separator()
        layout.prop(self, 'target', expand=True)

        if self.target == 'SIDE_LOADED':
            from .create_matl_from_blender_materials import get_side_loaded_twin
            side_box = layout.box()
            material = context.object.active_material
            twin = get_side_loaded_twin(material) if material else None
            if twin is None:
                side_box.label(text=f'Will create "{side_loaded_name(material.name)}"',
                               icon='DUPLICATE')
                side_box.label(text='Copied from the live material, so it keeps its textures.')
            else:
                side_box.label(text=f'Updating existing "{twin.name}"', icon='FILE_REFRESH')

            scene_props = context.scene.sub_scene_properties
            if not scene_props.export_prefer_sideloaded_materials:
                warn = layout.box()
                warn.label(text='"Prefer Side-Loaded Materials" is off,', icon='ERROR')
                warn.label(text='so export will still use the live material.')
                warn.prop(scene_props, 'export_prefer_sideloaded_materials')

        layout.prop(self, 'apply_to_selected')

    def execute(self, context):
        preset = material_presets.get(self.preset_name)
        if preset is None:
            self.report({'ERROR'}, f'Unknown preset "{self.preset_name}".')
            return {'CANCELLED'}

        if self.apply_to_selected:
            materials = []
            for obj in context.selected_objects:
                if obj.type != 'MESH':
                    continue
                for slot in obj.material_slots:
                    if slot.material is not None and slot.material not in materials:
                        materials.append(slot.material)
        else:
            materials = [context.object.active_material]

        if not materials:
            self.report({'WARNING'}, 'No materials to apply the preset to.')
            return {'CANCELLED'}

        # Side-loading redirects the write to each material's twin, so the
        # material actually assigned to the mesh is never touched.
        created = 0
        if self.target == 'SIDE_LOADED':
            from .create_matl_from_blender_materials import get_or_create_side_loaded_twin
            targets = []
            for material in materials:
                # A twin of a twin would be meaningless, so a material that is
                # already side-loaded is written to directly.
                if material.name.endswith(SIDE_LOAD_SUFFIX):
                    targets.append(material)
                    continue
                twin, was_created = get_or_create_side_loaded_twin(material)
                created += int(was_created)
                targets.append(twin)
            materials = targets

        notes = []
        for material in materials:
            notes.extend(apply_material_preset(material, preset))

        if self.target == 'SIDE_LOADED':
            message = (f'Applied "{preset.name}" to {len(materials)} side-loaded '
                       f'material(s)' + (f', {created} newly created' if created else '')
                       + '. Mesh materials unchanged.')
            if not context.scene.sub_scene_properties.export_prefer_sideloaded_materials:
                notes.append('turn on "Prefer Side-Loaded Materials" for export to use them')
        else:
            message = f'Applied "{preset.name}" to {len(materials)} material(s).'

        if notes:
            self.report({'WARNING'}, message + ' ' + '; '.join(dict.fromkeys(notes)))
        else:
            self.report({'INFO'}, message)
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=460)


def _wrap(text, width):
    """Break a string into lines that fit a Blender dialog."""
    words = text.split()
    lines = []
    current = ''
    for word in words:
        candidate = f'{current} {word}'.strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


class SUB_OP_find_shader_label(Operator):
    """Search the shader database instead of typing a label by hand"""
    bl_idname = 'sub.find_shader_label'
    bl_label = 'Find Shader'
    bl_description = (
        'Search all 4008 shader programs for one matching the features you '
        'need, ranked by how heavily vanilla fighters use it'
    )
    bl_options = {'REGISTER', 'UNDO'}

    use_col: BoolProperty(name='Col Map', default=True,
                          description='Shader must read Texture0, the base colour map')
    use_nor: BoolProperty(name='Nor Map', default=True,
                          description='Shader must read Texture4, the normal map')
    use_prm: BoolProperty(name='PRM Map', default=True,
                          description='Shader must read Texture6, the PRM map')
    use_emi: BoolProperty(name='Emi Map', default=False,
                          description='Shader must read Texture5, the emissive map')

    lighting: EnumProperty(
        name='Lighting',
        items=(
            ('ANY', 'Any', ''),
            ('LIT', 'Lit', 'Shaded by stage lighting'),
            ('SHADELESS', 'Shadeless', 'Ignores stage lighting entirely'),
        ),
        default='LIT',
    )
    transparency: EnumProperty(
        name='Transparency',
        items=(
            ('ANY', 'Any', ''),
            ('OPAQUE', 'Opaque', 'No alpha testing'),
            ('ALPHA_TEST', 'Alpha Test', 'Hard cutout transparency'),
        ),
        default='OPAQUE',
    )
    exact: BoolProperty(
        name='Exact Texture Set',
        default=True,
        description=(
            'Only shaders using exactly these maps. Off also returns shaders '
            'that read additional maps'
        ),
    )

    result: EnumProperty(
        name='Match',
        description='Shader programs matching the search',
        items=lambda self, context: self._results(context),
    )

    def _search(self):
        textures = []
        excluded = []
        for enabled, param in ((self.use_col, 'Texture0'), (self.use_nor, 'Texture4'),
                               (self.use_prm, 'Texture6'), (self.use_emi, 'Texture5')):
            (textures if enabled else excluded).append(param)
        # Only exclude the emissive maps - excluding col/nor/prm when unticked
        # would be surprising, since "I did not ask for a PRM map" rarely means
        # "and it must not have one".
        exclude = ['Texture5', 'Texture14'] if not self.use_emi else []

        return shader_info.find_shader_labels(
            textures=textures,
            exclude_textures=exclude,
            lighting={'LIT': True, 'SHADELESS': False}.get(self.lighting),
            discard={'OPAQUE': False, 'ALPHA_TEST': True}.get(self.transparency),
            exact_textures=self.exact,
            limit=20,
        )

    def _results(self, context):
        labels = self._search()
        if not labels:
            return [('NONE', 'No matching shader', '')]
        items = []
        for label in labels:
            uses = shader_info.fighter_usage_count(label)
            items.append((label, f'{label}  ({uses} uses)', shader_info.describe(label)))
        return items

    @classmethod
    def poll(cls, context):
        return _active_smash_material(context) is not None

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text='Maps this material needs', icon='TEXTURE')
        row = box.row(align=True)
        row.prop(self, 'use_col', toggle=True)
        row.prop(self, 'use_nor', toggle=True)
        row.prop(self, 'use_prm', toggle=True)
        row.prop(self, 'use_emi', toggle=True)
        box.prop(self, 'exact')

        row = layout.row(align=True)
        row.prop(self, 'lighting', expand=True)
        row = layout.row(align=True)
        row.prop(self, 'transparency', expand=True)

        layout.separator()
        layout.prop(self, 'result')

        label = self.result
        if label and label != 'NONE':
            info_box = layout.box()
            for line in _wrap(shader_info.describe(label), 62):
                info_box.label(text=line)

    def execute(self, context):
        if not self.result or self.result == 'NONE':
            self.report({'WARNING'}, 'No shader selected.')
            return {'CANCELLED'}

        material = _active_smash_material(context)
        # Keep the pass the material is already in; the search matches on
        # shader features, which are the same across all four passes.
        current_pass = shader_info.render_pass_of(material.sub_matl_data.shader_label) or '_opaque'
        create_sub_matl_data_from_shader_label(material, self.result + current_pass)
        self.report({'INFO'}, f'{material.name} now uses {self.result}{current_pass}.')
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=520)


class SUB_OP_validate_materials(Operator):
    """Check materials for problems that only show up in game"""
    bl_idname = 'sub.validate_materials'
    bl_label = 'Check Materials'
    bl_description = (
        'Look for material problems that export will happily write but the '
        'game renders wrong - sRGB normal maps, missing vertex attributes, '
        'double-applied alpha, and so on'
    )
    bl_options = {'REGISTER'}

    whole_file: BoolProperty(
        name='Every Mesh In The File',
        default=False,
        description='Check every mesh, not just the selected ones',
    )

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects) or bool(bpy.data.objects)

    def execute(self, context):
        objects = bpy.data.objects if self.whole_file else context.selected_objects
        materials_to_meshes = validate.collect_materials(objects)

        if not materials_to_meshes:
            self.report({'WARNING'}, 'No Smash materials found to check.')
            return {'CANCELLED'}

        issues = validate.check_materials(materials_to_meshes)
        errors, warnings, infos = validate.counts(issues)

        print('\n' + '=' * 70)
        print(f'Smash material check - {len(materials_to_meshes)} material(s)')
        print('=' * 70)
        if not issues:
            print('No problems found.')
        for issue in issues:
            print(f'[{issue.severity:7s}] {issue.material_name}: {issue.message}')
            if issue.fix_hint:
                print(f'{"":10s}  -> {issue.fix_hint}')
        print('=' * 70 + '\n')

        validate.store_results(issues, len(materials_to_meshes))

        if not issues:
            self.report({'INFO'},
                        f'Checked {len(materials_to_meshes)} material(s) - no problems found.')
        else:
            level = 'ERROR' if errors else ('WARNING' if warnings else 'INFO')
            self.report({level},
                        f'{errors} error(s), {warnings} warning(s), {infos} note(s). '
                        f'See the console for details.')
        return {'FINISHED'}

class SUB_OP_create_sub_matl_data_from_shader_label(Operator):
    bl_idname = 'sub.create_sub_matl_data_from_shader_label'
    bl_label = 'Create New Material from Shader Label'
    
    new_shader_label: StringProperty(
        name="New Shader Label",
        description="The New Shader Label",
        default="SFX_PBS_0100000008008269_opaque"
        )
    @classmethod
    def poll(cls, context):
        if not context.object:
            return False
        if context.object.type != 'MESH':
            return False
        if context.object.active_material is None:
            return False
        if context.object.active_material.sub_matl_data is None:
            return False
        if context.object.active_material.sub_matl_data.shader_label == "":
            return False
        return True
    
    def execute(self, context):
        if not is_valid_shader_label(self, self.new_shader_label):
            return{'CANCELLED'}
        create_sub_matl_data_from_shader_label(context.object.active_material, self.new_shader_label)
        return {'FINISHED'} 
    
    def invoke(self, context, event):
        wm = context.window_manager
        self.new_shader_label = context.object.active_material.sub_matl_data.shader_label
        return wm.invoke_props_dialog(self)

from .convert_blender_material import convert_blender_material, rename_mesh_attributes_of_meshes_using_material
from .convert_smash_material import (
    convert_smash_material_to_principled,
    has_smash_material_data,
    has_prm_texture,
    is_converted_to_principled,
    revert_to_smash_material,
    find_target_armature,
    smash_materials_on_armature,
    armature_has_unconverted_smash_materials,
    armature_has_converted_smash_materials,
)
class SUB_OP_convert_blender_material(Operator):
    bl_idname = 'sub.convert_blender_material'
    bl_label = 'Convert Blender Material (Creates PRM, uses existing normal)'
    bl_options = {'REGISTER', 'INTERNAL'}
    
    bake_size: bpy.props.IntProperty(
        name="Texture Size",
        description="Size of the generated PRM texture (width and height)",
        default=1024,
        min=64,
        max=8192,
        step=1,
        subtype='PIXEL'
    )
    
    def draw(self, context):
        layout = self.layout
        layout.label(text="Warning: Creating textures is resource-intensive.", icon='ERROR')
        layout.label(text="Larger textures require more memory and time.")
        layout.separator()
        
        # Display requirements
        box = layout.box()
        box.label(text="Requirements:", icon='INFO')
        box.label(text="• Cycles render engine must be enabled")
        box.label(text="• GPU acceleration recommended for speed")
        
        # Display the custom size input field
        layout.separator()
        layout.prop(self, "bake_size")
    
    def execute(self, context):
        rename_mesh_attributes_of_meshes_using_material(self, context.object.active_material)
        convert_blender_material(self, context.object.active_material, self.bake_size)
        return {'FINISHED'} 
        
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

class SUB_OP_convert_blender_material_no_textures(Operator):
    bl_idname = 'sub.convert_blender_material_no_textures'
    bl_label = 'Convert Blender Material (Diffuse only)'

    def execute(self, context):
        rename_mesh_attributes_of_meshes_using_material(self, context.object.active_material)
        # Import the original implementation without texture creation
        from .convert_blender_material_original import convert_blender_material_original
        convert_blender_material_original(self, context.object.active_material)
        return {'FINISHED'} 

class SUB_OP_set_texture_size(Operator):
    bl_idname = 'sub.set_texture_size'
    bl_label = 'Set Texture Size'
    bl_options = {'INTERNAL'}
    
    size: bpy.props.IntProperty(default=1024)
    operator_id: bpy.props.StringProperty(default="sub.convert_blender_material")
    
    def execute(self, context):
        # Find the active operator and set its size
        if hasattr(context, 'window_manager'):
            for area in context.screen.areas:
                if area.type == 'PROPERTIES':
                    for space in area.spaces:
                        if space.type == 'PROPERTIES':
                            for region in area.regions:
                                if region.type == 'WINDOW':
                                    override = context.copy()
                                    override['area'] = area
                                    override['region'] = region
                                    override['space_data'] = space
                                    bpy.context.window_manager.operator_properties_last(self.operator_id).bake_size = self.size
        return {'FINISHED'}

class SUB_OP_copy_from_ult_material(Operator):
    bl_idname = 'sub.copy_from_ult_material'
    bl_label = 'Copy From Other Material'

    def execute(self, context):
        return {'FINISHED'} 

class SUB_OP_fix_solid_view_display(bpy.types.Operator):
    bl_idname = "smash_ultimate.fix_solid_view_display"
    bl_label = "Fix Solid View Display"
    bl_description = "Fixes materials that aren't displaying properly in Solid view mode, especially for materials using multiple UV maps"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return context.material is not None
    
    def execute(self, context):
        material = context.material
        if not material:
            self.report({'ERROR'}, "No material selected")
            return {'CANCELLED'}
        
        # Import the setup functions
        from .create_blender_materials_from_matl import setup_material_for_solid_view, setup_eye_material_for_solid_view
        
        # Apply fixes for Solid view display
        setup_material_for_solid_view(material)
        
        # Special handling for eye materials
        if 'Eye' in material.name:
            setup_eye_material_for_solid_view(material)
        
        # Force viewport update
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.type = space.shading.type  # Force refresh
        
        self.report({'INFO'}, f"Fixed Solid view display for material '{material.name}'")
        return {'FINISHED'}

class SUB_OP_fix_all_materials_solid_view(bpy.types.Operator):
    bl_idname = "smash_ultimate.fix_all_materials_solid_view"
    bl_label = "Fix All Materials for Solid View"
    bl_description = "Fixes all materials in the scene to display properly in Solid view mode"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        # Import the setup functions
        from .create_blender_materials_from_matl import setup_material_for_solid_view, setup_eye_material_for_solid_view
        
        fixed_count = 0
        
        for material in bpy.data.materials:
            if material.use_nodes and material.node_tree:
                # Apply fixes for Solid view display
                setup_material_for_solid_view(material)
                
                # Special handling for eye materials
                if 'Eye' in material.name:
                    setup_eye_material_for_solid_view(material)
                
                fixed_count += 1
        
        # Force viewport update
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.type = space.shading.type  # Force refresh
        
        self.report({'INFO'}, f"Fixed Solid view display for {fixed_count} materials")
        return {'FINISHED'} 

class SUB_OP_fix_uvset_solid_view(bpy.types.Operator):
    bl_idname = "smash_ultimate.fix_uvset_solid_view"
    bl_label = "Fix uvSet Textures in Solid View"
    bl_description = "Specifically fixes textures using the uvSet UV map to display properly in Solid view mode"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return context.material is not None
    
    def execute(self, context):
        material = context.material
        if not material or not material.use_nodes or not material.node_tree:
            self.report({'ERROR'}, "No material with nodes selected")
            return {'CANCELLED'}
        
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        
        # Find all texture nodes that use uvSet
        uvset_texture_nodes = []
        for node in nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                # Check if this texture is connected to a uvSet UV map
                for link in links:
                    if link.to_node == node and link.from_node.type == 'UVMAP':
                        if hasattr(link.from_node, 'uv_map') and link.from_node.uv_map == 'uvSet':
                            uvset_texture_nodes.append(node)
                            break
        
        if not uvset_texture_nodes:
            self.report({'WARNING'}, "No textures using uvSet UV map found")
            return {'CANCELLED'}
        
        # Find or create a principled BSDF node
        principled_node = None
        for node in nodes:
            if node.type == 'BSDF_PRINCIPLED':
                principled_node = node
                break
        
        if not principled_node:
            principled_node = nodes.new('ShaderNodeBsdfPrincipled')
            principled_node.location = (0, 0)
        
        # Connect uvSet textures to the principled BSDF
        connected_count = 0
        for tex_node in uvset_texture_nodes:
            # Check if texture is already connected to principled
            already_connected = False
            for link in links:
                if link.from_node == tex_node and link.to_node == principled_node:
                    already_connected = True
                    break
            
            if not already_connected:
                # Connect texture to base color if not already connected
                if not principled_node.inputs['Base Color'].links:
                    links.new(tex_node.outputs['Color'], principled_node.inputs['Base Color'])
                    connected_count += 1
                else:
                    # If base color is already connected, create a mix node
                    mix_node = nodes.new('ShaderNodeMixRGB')
                    mix_node.location = (principled_node.location[0] - 300, principled_node.location[1])
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs[0].default_value = 1.0  # Factor
                    
                    # Connect existing base color to mix node
                    existing_link = principled_node.inputs['Base Color'].links[0]
                    links.new(existing_link.from_node.outputs[existing_link.from_socket.name], mix_node.inputs[1])
                    
                    # Connect new texture to mix node
                    links.new(tex_node.outputs['Color'], mix_node.inputs[2])
                    
                    # Connect mix node to principled
                    links.new(mix_node.outputs[0], principled_node.inputs['Base Color'])
                    connected_count += 1
        
        # Ensure principled BSDF is connected to material output
        output_nodes = [n for n in nodes if n.type == 'OUTPUT_MATERIAL']
        for output_node in output_nodes:
            if output_node.target == 'EEVEE':
                if not output_node.inputs[0].links:
                    links.new(principled_node.outputs[0], output_node.inputs[0])
                elif output_node.inputs[0].links[0].from_node != principled_node:
                    # If connected to something else, create a mix shader
                    mix_shader = nodes.new('ShaderNodeMixShader')
                    mix_shader.location = (output_node.location[0] - 200, output_node.location[1])
                    mix_shader.inputs[0].default_value = 0.5  # Factor
                    
                    # Connect existing shader to mix
                    existing_link = output_node.inputs[0].links[0]
                    links.new(existing_link.from_node.outputs[existing_link.from_socket.name], mix_shader.inputs[1])
                    
                    # Connect principled to mix
                    links.new(principled_node.outputs[0], mix_shader.inputs[2])
                    
                    # Connect mix to output
                    links.new(mix_shader.outputs[0], output_node.inputs[0])
        
        # Force viewport update
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.type = space.shading.type  # Force refresh
        
        self.report({'INFO'}, f"Fixed {connected_count} uvSet textures for Solid view display")
        return {'FINISHED'} 

class SUB_OP_fix_eye_uvset_solid_view(bpy.types.Operator):
    bl_idname = "smash_ultimate.fix_eye_uvset_solid_view"
    bl_label = "Fix Eye uvSet Textures in Solid View"
    bl_description = "Specifically fixes eye materials with uvSet textures to display properly in Solid view mode"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return context.material is not None and 'Eye' in context.material.name
    
    def execute(self, context):
        material = context.material
        if not material or not material.use_nodes or not material.node_tree:
            self.report({'ERROR'}, "No eye material with nodes selected")
            return {'CANCELLED'}
        
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        
        # Find all texture nodes that use uvSet
        uvset_texture_nodes = []
        for node in nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                # Check if this texture is connected to a uvSet UV map
                for link in links:
                    if link.to_node == node and link.from_node.type == 'UVMAP':
                        if hasattr(link.from_node, 'uv_map') and link.from_node.uv_map == 'uvSet':
                            uvset_texture_nodes.append(node)
                            break
        
        if not uvset_texture_nodes:
            self.report({'WARNING'}, "No uvSet textures found in eye material")
            return {'CANCELLED'}
        
        # For eye materials, we need to ensure the uvSet textures are properly connected
        # to the material output for Solid view display
        
        # Find the material output node
        output_node = None
        for node in nodes:
            if node.type == 'OUTPUT_MATERIAL' and node.target == 'EEVEE':
                output_node = node
                break
        
        if not output_node:
            # Create output node if it doesn't exist
            output_node = nodes.new('ShaderNodeOutputMaterial')
            output_node.target = 'EEVEE'
            output_node.location = (600, 0)
        
        # Create a principled BSDF specifically for the uvSet textures
        principled_node = nodes.new('ShaderNodeBsdfPrincipled')
        principled_node.location = (300, 0)
        
        # Connect uvSet textures to the principled BSDF
        connected_count = 0
        for tex_node in uvset_texture_nodes:
            # Connect texture to base color
            links.new(tex_node.outputs['Color'], principled_node.inputs['Base Color'])
            connected_count += 1
        
        # Connect principled BSDF to material output
        links.new(principled_node.outputs[0], output_node.inputs[0])
        
        # Set material properties for eye display
        material.use_backface_culling = False  # Eyes should be visible from both sides
        material.blend_method = 'OPAQUE'  # Ensure proper display in Solid view
        
        # Force viewport update
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.type = space.shading.type  # Force refresh
        
        self.report({'INFO'}, f"Fixed {connected_count} uvSet textures in eye material for Solid view display")
        return {'FINISHED'} 

class SUB_OP_fix_eye_dual_uv_solid_view(bpy.types.Operator):
    bl_idname = "smash_ultimate.fix_eye_dual_uv_solid_view"
    bl_label = "Fix Eye Dual UV Maps in Solid View"
    bl_description = "Combines map1 (white eye) and uvSet (pupil) textures for proper Solid view display"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return context.material is not None and 'Eye' in context.material.name
    
    def execute(self, context):
        material = context.material
        if not material or not material.use_nodes or not material.node_tree:
            self.report({'ERROR'}, "No eye material with nodes selected")
            return {'CANCELLED'}
        
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        
        # Find textures for both UV maps
        map1_texture_nodes = []
        uvset_texture_nodes = []
        
        for node in nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                # Check which UV map this texture uses
                for link in links:
                    if link.to_node == node and link.from_node.type == 'UVMAP':
                        if hasattr(link.from_node, 'uv_map'):
                            if link.from_node.uv_map == 'map1':
                                map1_texture_nodes.append(node)
                            elif link.from_node.uv_map == 'uvSet':
                                uvset_texture_nodes.append(node)
                            break
        
        if not map1_texture_nodes and not uvset_texture_nodes:
            self.report({'WARNING'}, "No map1 or uvSet textures found in eye material")
            return {'CANCELLED'}
        
        # Find or create material output node
        output_node = None
        for node in nodes:
            if node.type == 'OUTPUT_MATERIAL' and node.target == 'EEVEE':
                output_node = node
                break
        
        if not output_node:
            output_node = nodes.new('ShaderNodeOutputMaterial')
            output_node.target = 'EEVEE'
            output_node.location = (800, 0)
        
        # Create a principled BSDF for the combined result
        principled_node = nodes.new('ShaderNodeBsdfPrincipled')
        principled_node.location = (600, 0)
        
        # Connect map1 textures (white eye) to base color
        if map1_texture_nodes:
            # Use the first map1 texture as base color
            links.new(map1_texture_nodes[0].outputs['Color'], principled_node.inputs['Base Color'])
            
            # If there are multiple map1 textures, mix them
            if len(map1_texture_nodes) > 1:
                for i, tex_node in enumerate(map1_texture_nodes[1:], 1):
                    mix_node = nodes.new('ShaderNodeMixRGB')
                    mix_node.location = (principled_node.location[0] - 300 * i, principled_node.location[1])
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs[0].default_value = 1.0
                    
                    # Connect previous result to mix node
                    if i == 1:
                        links.new(map1_texture_nodes[0].outputs['Color'], mix_node.inputs[1])
                    else:
                        prev_mix = nodes[f"mix_map1_{i-1}"]
                        links.new(prev_mix.outputs[0], mix_node.inputs[1])
                    
                    # Connect new texture to mix node
                    links.new(tex_node.outputs['Color'], mix_node.inputs[2])
                    mix_node.name = f"mix_map1_{i}"
                    
                    # Connect final mix to principled
                    if i == len(map1_texture_nodes) - 1:
                        links.new(mix_node.outputs[0], principled_node.inputs['Base Color'])
        
        # Connect uvSet textures (pupil) using mix nodes
        if uvset_texture_nodes:
            if map1_texture_nodes:
                # Mix uvSet textures with existing base color
                for i, tex_node in enumerate(uvset_texture_nodes):
                    mix_node = nodes.new('ShaderNodeMixRGB')
                    mix_node.location = (principled_node.location[0] - 300 * (len(map1_texture_nodes) + i), principled_node.location[1])
                    mix_node.blend_type = 'MULTIPLY'
                    mix_node.inputs[0].default_value = 1.0
                    
                    # Connect current base color to mix node
                    if i == 0:
                        links.new(principled_node.inputs['Base Color'].links[0].from_node.outputs[principled_node.inputs['Base Color'].links[0].from_socket.name], mix_node.inputs[1])
                    else:
                        prev_mix = nodes[f"mix_uvset_{i-1}"]
                        links.new(prev_mix.outputs[0], mix_node.inputs[1])
                    
                    # Connect uvSet texture to mix node
                    links.new(tex_node.outputs['Color'], mix_node.inputs[2])
                    mix_node.name = f"mix_uvset_{i}"
                    
                    # Connect final mix to principled
                    if i == len(uvset_texture_nodes) - 1:
                        links.new(mix_node.outputs[0], principled_node.inputs['Base Color'])
            else:
                # No map1 textures, use uvSet as base color
                links.new(uvset_texture_nodes[0].outputs['Color'], principled_node.inputs['Base Color'])
                
                # If multiple uvSet textures, mix them
                if len(uvset_texture_nodes) > 1:
                    for i, tex_node in enumerate(uvset_texture_nodes[1:], 1):
                        mix_node = nodes.new('ShaderNodeMixRGB')
                        mix_node.location = (principled_node.location[0] - 300 * i, principled_node.location[1])
                        mix_node.blend_type = 'MULTIPLY'
                        mix_node.inputs[0].default_value = 1.0
                        
                        # Connect previous result to mix node
                        if i == 1:
                            links.new(uvset_texture_nodes[0].outputs['Color'], mix_node.inputs[1])
                        else:
                            prev_mix = nodes[f"mix_uvset_{i-1}"]
                            links.new(prev_mix.outputs[0], mix_node.inputs[1])
                        
                        # Connect new texture to mix node
                        links.new(tex_node.outputs['Color'], mix_node.inputs[2])
                        mix_node.name = f"mix_uvset_{i}"
                        
                        # Connect final mix to principled
                        if i == len(uvset_texture_nodes) - 1:
                            links.new(mix_node.outputs[0], principled_node.inputs['Base Color'])
        
        # Connect principled BSDF to material output
        links.new(principled_node.outputs[0], output_node.inputs[0])
        
        # Set material properties for eye display
        material.use_backface_culling = False  # Eyes should be visible from both sides
        material.blend_method = 'OPAQUE'  # Ensure proper display in Solid view
        
        # Force viewport update
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.type = space.shading.type  # Force refresh
        
        map1_count = len(map1_texture_nodes)
        uvset_count = len(uvset_texture_nodes)
        self.report({'INFO'}, f"Combined {map1_count} map1 textures (white eye) and {uvset_count} uvSet textures (pupil) for Solid view display")
        return {'FINISHED'}

class SUB_OP_convert_smash_material(Operator):
    bl_idname = 'sub.convert_smash_material'
    bl_label = 'Convert Smash Material to Principled BSDF'
    bl_description = 'Convert a Smash Ultimate material to a standard Principled BSDF with decomposed PRM texture'
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        material = context.object.active_material if context.object else None
        # Only show if material has Smash data AND has NOT been converted yet
        return material is not None and has_smash_material_data(material) and not is_converted_to_principled(material)
    
    def execute(self, context):
        material = context.object.active_material
        if not material:
            self.report({'ERROR'}, "No active material")
            return {'CANCELLED'}
        
        if not has_smash_material_data(material):
            self.report({'ERROR'}, f"Material '{material.name}' is not a Smash Ultimate material")
            return {'CANCELLED'}
        
        # Perform the conversion
        success = convert_smash_material_to_principled(self, material)
        
        if success:
            self.report({'INFO'}, f"Successfully converted Smash material '{material.name}' to Principled BSDF")
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, f"Failed to convert material '{material.name}'")
            return {'CANCELLED'}


class SUB_OP_revert_smash_material(Operator):
    bl_idname = 'sub.revert_smash_material'
    bl_label = 'Revert to Smash Material'
    bl_description = 'Revert a converted Principled BSDF material back to the original Smash Ultimate material'
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        material = context.object.active_material if context.object else None
        # Only show if material has been converted to Principled BSDF
        return material is not None and is_converted_to_principled(material)
    
    def execute(self, context):
        material = context.object.active_material
        if not material:
            self.report({'ERROR'}, "No active material")
            return {'CANCELLED'}
        
        if not is_converted_to_principled(material):
            self.report({'ERROR'}, f"Material '{material.name}' is not a converted Smash material")
            return {'CANCELLED'}
        
        # Perform the reversion
        success = revert_to_smash_material(self, material)
        
        if success:
            self.report({'INFO'}, f"Successfully reverted material '{material.name}' to Smash Ultimate material")
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, f"Failed to revert material '{material.name}'")
            return {'CANCELLED'}


class SUB_OP_convert_armature_smash_materials(Operator):
    bl_idname = "sub.convert_armature_smash_materials"
    bl_label = "Convert All to Principled BSDF"
    bl_description = "Convert every Smash material on the selected armature's meshes to Principled BSDF"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        armature = find_target_armature(context)
        return armature is not None and armature_has_unconverted_smash_materials(armature)

    def execute(self, context):
        armature = find_target_armature(context)
        if armature is None:
            self.report({"ERROR"}, "Select an armature")
            return {"CANCELLED"}
        converted = 0
        failed = 0
        for material in smash_materials_on_armature(armature):
            if is_converted_to_principled(material):
                continue
            if convert_smash_material_to_principled(self, material):
                converted += 1
            else:
                failed += 1
        if converted:
            self.report({"INFO"}, f"Converted {converted} material(s) on '{armature.name}' to Principled BSDF")
            return {"FINISHED"}
        self.report({"ERROR"}, f"Failed to convert materials on '{armature.name}' ({failed} failed)")
        return {"CANCELLED"}


class SUB_OP_revert_armature_smash_materials(Operator):
    bl_idname = "sub.revert_armature_smash_materials"
    bl_label = "Revert All to Smash Material"
    bl_description = "Rebuild the original Smash shaders for every converted material on the selected armature"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        armature = find_target_armature(context)
        return armature is not None and armature_has_converted_smash_materials(armature)

    def execute(self, context):
        armature = find_target_armature(context)
        if armature is None:
            self.report({"ERROR"}, "Select an armature")
            return {"CANCELLED"}
        reverted = 0
        failed = 0
        for material in smash_materials_on_armature(armature):
            if not is_converted_to_principled(material):
                continue
            if revert_to_smash_material(self, material):
                reverted += 1
            else:
                failed += 1
        if reverted:
            self.report({"INFO"}, f"Reverted {reverted} material(s) on '{armature.name}' to Smash shaders")
            return {"FINISHED"}
        self.report({"ERROR"}, f"Failed to revert materials on '{armature.name}' ({failed} failed)")
        return {"CANCELLED"}
