import bpy
import re
import os
from pathlib import Path
from bpy.types import Panel, Operator
from bpy.props import StringProperty, BoolProperty, EnumProperty
from ....dependencies import ssbh_data_py
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ...blender_property_extensions import SubSceneProperties

class SUB_PT_reimport_materials(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_context = "objectmode"
    bl_category = 'Ultimate'
    bl_label = 'Material Re-Importer'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        ssp: SubSceneProperties = context.scene.sub_scene_properties
        layout = self.layout
        layout.use_property_split = False

        row = layout.row(align=True)
        row.label(text='Select an Armature')
        row = layout.row(align=True)
        row.prop(ssp, 'material_reimport_arma', icon='ARMATURE_DATA')
        if not ssp.material_reimport_arma:
            return
        if '' == ssp.material_reimport_folder:
            row = layout.row(align=True)
            row.operator('sub.mat_reimport_dir_selector', icon='ZOOM_ALL', text='Select folder w/ .NUMATB & textures')
        else:
            row = layout.row(align=True)
            row.label(text=f'Textures Folder= "{ssp.material_reimport_folder}"')
            if '' == ssp.material_reimport_numatb_path:
                row = layout.row(align=True)
                row.alert = True
                row.label(text='No .numatb file found!', icon='ERROR')
            else:
                row = layout.row(align=True)
                row.label(text=f'.numatb file: "{Path(ssp.material_reimport_numatb_path).name}"', icon='FILE')
                row = layout.row(align=True)
                row.operator('sub.mat_reimport_numatb_selector', icon='ZOOM_ALL', text='Re-select .numatb')
                row = layout.row(align=True)
            row = layout.row(align=True)
            row.operator('sub.mat_reimport_dir_selector', icon='ZOOM_ALL', text='Re-Select folder')
            row = layout.row(align=True)
            row.prop(ssp, 'material_reimport_side_load')
            if ssp.material_reimport_side_load:
                box = layout.box()
                box.label(text='Side-Load is ON', icon='INFO')
                box.label(text='Your assigned materials will NOT be updated.')
                box.label(text='Turn this OFF to refresh their Ultimate')
                box.label(text='Material Data (vectors, textures, ...) in place.')
            row = layout.row(align=True)
            row.operator('sub.reimport_materials', icon='IMPORT',
                        text='Side-Load Materials' if ssp.material_reimport_side_load
                        else 'Re-Import materials (refresh in place)')
            layout.separator()
            layout.prop(ssp, 'export_prefer_sideloaded_materials')
            # "Prefer Side-Loaded" with no twins actually present is a silent
            # no-op: export looks for "<name> (Side-Loaded)", finds nothing and
            # quietly falls back to the assigned material's data. Worth saying
            # out loud, since the toggle being on reads like it's doing
            # something. Twins used to vanish on save because they had no fake
            # user (fixed above), so an older file can easily be in this state.
            if ssp.export_prefer_sideloaded_materials:
                if not any('(Side-Loaded)' in m.name for m in bpy.data.materials):
                    box = layout.box()
                    box.alert = True
                    box.label(text='No "(Side-Loaded)" materials exist!', icon='ERROR')
                    box.label(text='Export is falling back to the assigned')
                    box.label(text='materials, so this toggle does nothing.')

class SUB_OP_mat_reimport_directory_selector(Operator):
    bl_idname = 'sub.mat_reimport_dir_selector'
    bl_label = 'Confirm folder'

    filter_glob: StringProperty(
        default='*.numatb; *.png',
        options={'HIDDEN'}
    )
    directory: StringProperty(
        subtype="DIR_PATH"
    )

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        ssp: SubSceneProperties = context.scene.sub_scene_properties
        ssp.material_reimport_folder = self.directory
        numatb_files = {f for f in os.listdir(ssp.material_reimport_folder) if f.endswith('.numatb')}
        if len(numatb_files) == 0:
            ssp.material_reimport_numatb_path = ''
        elif len(numatb_files) == 1:
            ssp.material_reimport_numatb_path = os.path.join(ssp.material_reimport_folder, numatb_files.pop())
        else:
            if {'model.numatb'} & numatb_files:
                ssp.material_reimport_numatb_path = os.path.join(ssp.material_reimport_folder, 'model.numatb')
            else:
                ssp.material_reimport_numatb_path = os.path.join(ssp.material_reimport_folder, numatb_files.pop())
        return {'FINISHED'}   

class SUB_OP_mat_reimport_numatb_selector(Operator):
    bl_idname = 'sub.mat_reimport_numatb_selector'
    bl_label = 'Confirm .numatb'

    filter_glob: StringProperty(
        default='*.numatb;',
        options={'HIDDEN'}
    )
    filepath: StringProperty(
        subtype="FILE_PATH"
    )
    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
    
    def execute(self, context):
        context.scene.sub_scene_properties.material_reimport_numatb_path = self.filepath
        return {'FINISHED'}   

class SUB_OP_reimport_materials(Operator):
    bl_idname = 'sub.reimport_materials'
    bl_label = 'Reimport Materials'

    @classmethod
    def poll(cls, context):
        ssp: SubSceneProperties = context.scene.sub_scene_properties
        return ssp.material_reimport_numatb_path != '' and ssp.material_reimport_folder != ''

    def execute(self, context):
        reimport_materials(self, context)
        return {'FINISHED'}

_SUB_MATL_DATA_COLLECTIONS = (
    'bools', 'floats', 'vectors', 'textures',
    'samplers', 'blend_states', 'rasterizer_states', 'vertex_attributes',
)


def refresh_sub_matl_data(material: bpy.types.Material, entry, texture_name_to_image_dict):
    """Overwrite an EXISTING material's Ultimate Material Data from a fresh
    .numatb entry, in place - the material itself (name, node tree, whatever
    else the user built on it) is left completely alone.

    Mirrors exactly what create_blender_materials_from_matl() fills in for a
    brand new material (same set_shader_label/add_* calls, same source data),
    just retargeted at a material that already exists instead of creating a
    new one. Every add_* call only appends - see sub_matl_data.py, none of
    them clear first - so every collection is cleared here before refilling,
    or repeated reimports would pile up duplicate texture/float/vector
    entries on top of the old ones instead of replacing them.

    Does NOT rebuild the node tree, viewport display settings, blend method,
    or backface culling - only sub_matl_data, which is what Export Model
    actually reads (source/model/material/texture/export_nutexb.py and
    create_matl_from_blender_materials.py both read straight from it). Left
    untouched: linked_materials (the eye-material cross-linking system) -
    rebuilding that correctly depends on sibling materials also being
    refreshed in the same pass, which is more machinery than this needs for
    the common case; left as whatever it already was rather than risking
    clearing a working cross-link.
    """
    from .create_blender_materials_from_matl import get_vertex_attributes

    sub_matl_data = material.sub_matl_data
    for collection_name in _SUB_MATL_DATA_COLLECTIONS:
        getattr(sub_matl_data, collection_name).clear()

    sub_matl_data.set_shader_label(entry.shader_label)
    sub_matl_data.add_bools(entry.booleans)
    sub_matl_data.add_floats(entry.floats)
    sub_matl_data.add_vectors(entry.vectors)
    sub_matl_data.add_textures(entry.textures, texture_name_to_image_dict)
    sub_matl_data.add_samplers(entry.samplers)
    sub_matl_data.add_blend_states(entry.blend_states)
    sub_matl_data.add_rasterizer_states(entry.rasterizer_states)
    sub_matl_data.add_vertex_attributes(get_vertex_attributes(entry.shader_label))


def reimport_materials(operator: Operator, context):
    from .create_blender_materials_from_matl import create_blender_materials_from_matl, create_default_textures, import_material_images
    from ..export_model import would_trimmed_names_be_unique, trim_name, get_problematic_names
    from ...material_grouping import side_loaded_name

    ssp: SubSceneProperties = context.scene.sub_scene_properties
    arma: bpy.types.Object = ssp.material_reimport_arma
    mesh_objects: set[bpy.types.Object] = {child for child in arma.children if child.type == 'MESH'}
    materials: set[bpy.types.Material] = {material_slot.material for mesh_object in mesh_objects for material_slot in mesh_object.material_slots if material_slot.material is not None}
    material_names: set[str] = {material.name for material in materials}
    if not would_trimmed_names_be_unique(material_names):
        problematic_names = get_problematic_names(material_names)
        for problematic_name in problematic_names:
            message = f'The material name of "{problematic_name}" is not a unique name after trimming! Cannot reimport Materials! (Trimmed name is "{trim_name(problematic_name)}")'
            operator.report({'WARNING'}, message)
        return

    ssbh_matl = ssbh_data_py.matl_data.read_matl(str(ssp.material_reimport_numatb_path))

    if ssp.material_reimport_side_load:
        # Side-loading still creates whole separate materials, named
        # "<name> (Side-Loaded)" and never assigned to any mesh - that's a
        # deliberately different workflow (compare two versions of the same
        # material's data without touching what's actually in use) from a
        # plain reimport's in-place refresh below, so it keeps using
        # create_blender_materials_from_matl() to build full new materials.
        material_label_to_material = create_blender_materials_from_matl(operator, ssbh_matl, ssp.material_reimport_folder)
        for label, material in material_label_to_material.items():
            target_name = side_loaded_name(label)
            old_twin = bpy.data.materials.get(target_name)
            if old_twin is not None and old_twin is not material:
                bpy.data.materials.remove(old_twin, do_unlink=True)
            material.name = target_name
            # A side-loaded material is deliberately assigned to no mesh, so it
            # has zero users - which means Blender discards it on the next
            # save/reload, taking the whole side-load with it. Found exactly
            # that in a real file: both side-load toggles on, yet not a single
            # "(Side-Loaded)" material left in the .blend, so export silently
            # fell back to the assigned materials' stale data. A fake user is
            # what keeps an intentionally-unassigned datablock alive.
            material.use_fake_user = True
        operator.report(
            {'INFO'},
            f'Side-loaded {len(material_label_to_material)} material(s) as "<name> (Side-Loaded)" - '
            'not assigned to any mesh. Turn on "Prefer Side-Loaded Materials" to export from them.'
        )
        return

    # Plain reimport: refresh Ultimate Material Data on the materials already
    # assigned, in place - never create a new material or touch which one is
    # assigned to which mesh.
    entry_by_label = {entry.material_label: entry for entry in ssbh_matl.entries}
    create_default_textures()
    texture_name_to_image_dict = import_material_images(operator, ssbh_matl, ssp.material_reimport_folder)

    skipped: dict[str, set[str]] = {}   # current material name (trimmed) -> mesh names it's on
    refreshed_materials: set[bpy.types.Material] = set()
    for mesh_object in mesh_objects:
        for material_slot in mesh_object.material_slots:
            material = material_slot.material
            if material is None:
                continue
            current_name = trim_name(material.name)
            entry = entry_by_label.get(current_name)
            if entry is None:
                # Nothing in the freshly-parsed .numatb has this label. Used
                # to be silent - a material left with stale sub_matl_data
                # after reimport looked identical to one that had genuinely
                # been refreshed, and Export Model would go on reading
                # whatever stale data it already had. Most often this means
                # the mesh's current material name doesn't match its label in
                # this particular .numatb - a manual rename, or (for
                # something like eyes) a material this file's .numatb was
                # never going to contain in the first place.
                skipped.setdefault(current_name, set()).add(mesh_object.name)
                continue
            if material in refreshed_materials:
                continue   # already handled via another mesh sharing this material
            refresh_sub_matl_data(material, entry, texture_name_to_image_dict)
            refreshed_materials.add(material)

    if skipped:
        detail = '; '.join(f'"{name}" (on {", ".join(sorted(meshes))})'
                           for name, meshes in sorted(skipped.items()))
        operator.report({'WARNING'},
            f'Refreshed Ultimate Material Data on {len(refreshed_materials)} material(s). No matching '
            f'label in this .numatb for: {detail} - left as-is, so these will export with whatever '
            f'data they already had. Check the material name matches its label in the .numatb.')
    else:
        operator.report({'INFO'}, f'Refreshed Ultimate Material Data on {len(refreshed_materials)} material(s).')


