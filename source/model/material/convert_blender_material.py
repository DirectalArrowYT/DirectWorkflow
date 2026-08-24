import bpy

from bpy.types import (
    ShaderNode, ShaderNodeOutputMaterial, ShaderNodeBsdfDiffuse, ShaderNodeEmission,ShaderNodeBsdfPrincipled,
    ShaderNodeAttribute, ShaderNodeUVMap, NodeSocketFloat, NodeSocketColor, Image, ShaderNodeVertexColor, ShaderNodeMixRGB, Material, Operator, NodeSocket)

from math import isclose
import os
import tempfile
import time

from ....dependencies import ssbh_data_py
from .load_from_shader_label import create_sub_matl_data_from_shader_label
from .sub_matl_data import *
from .create_blender_materials_from_matl import create_default_textures
from .texture.convert_textures import create_nor_from_material, create_prm_from_material
ParamId = ssbh_data_py.matl_data.ParamId

def convert_from_no_nodes(operator: bpy.types.Operator, material: bpy.types.Material):
    diffuse_color = material.diffuse_color[:]
    metalness = material.metallic
    specular = material.specular_intensity
    roughness = material.roughness
    create_sub_matl_data_from_shader_label(material, "SFX_PBS_010000000800ba69_opaque") # Mesh-wide PRM, 1 Col, Nor, no ColorSet1
    sub_matl_data: SUB_PG_sub_matl_data = material.sub_matl_data
    cv47: SUB_PG_matl_vector = sub_matl_data.vectors.get(ParamId.CustomVector47.name)
    cv47.value = (metalness, roughness, 1.0, specular)
    cv13: SUB_PG_matl_vector = sub_matl_data.vectors.get(ParamId.CustomVector13.name)
    cv13.value = diffuse_color[:]

def get_vertex_color_count(nodes: list[bpy.types.ShaderNode]) -> int:
    vertex_color_names = set()
    for node in nodes:
        if not isinstance(node, (ShaderNodeAttribute, ShaderNodeVertexColor)):
            # Not the right node type
            continue
        if isinstance(node, ShaderNodeAttribute):
            if node.attribute_type != 'GEOMETRY':
                # Not vertex colors
                continue
            if not node.outputs['Color'].is_linked:
                # Not being used at all, or not used in a standard way
                continue
            if node.attribute_name == "":
                # Not being used at all, or being used incorrectly
                continue
            vertex_color_names.add(node.attribute_name)
        elif isinstance(node, ShaderNodeVertexColor):
            if not node.outputs['Color'].is_linked:
                # Not being used at all, or not used in a standard way
                continue
            if node.layer_name == "":
                # Not being used at all, or being used incorrectly
                continue
            vertex_color_names.add(node.layer_name)
        

    return len(vertex_color_names)

def get_uv_layer_count(nodes: list[bpy.types.ShaderNode]) -> int:
    uv_layer_names = set()
    for node in nodes:
        if not isinstance(node, ShaderNodeUVMap):
            # Not the right node type
            continue
        if node.from_instancer is True:
            # This means its using the 'Active' uv map rather than any specific one
            continue
        if node.uv_map == "":
            # This means its using the 'Active' uv map rather than any specific one.
            # it would be unusual for a user to intentionally use this and then use a manually specified one later
            continue
        uv_layer_names.add(node.uv_map)

    return len(uv_layer_names)

def principled_uses_emission(node: ShaderNodeBsdfPrincipled) -> bool:
    emission_input: NodeSocketColor = node.inputs['Emission Color']
    strength_input: NodeSocketFloat = node.inputs['Emission Strength']
    if emission_input.is_linked or strength_input.is_linked:
        return True
    
    if isclose(strength_input.default_value,0,abs_tol=0.01):
        return False
    
    if all(isclose(emission_input.default_value[col_index],0,abs_tol=0.01) for col_index in (0,1,2)):
        return False 
        
    return True

def principled_uses_subsurface(node: ShaderNodeBsdfPrincipled) -> bool:
    subsurface_weight_input: NodeSocketFloat = node.inputs['Subsurface Weight']
    if subsurface_weight_input.is_linked:
        return True
    
    if isclose(subsurface_weight_input.default_value,0,abs_tol=0.01):
        return False

    return True

def rename_mesh_attributes_of_meshes_using_material(operator: bpy.types.Operator, material: Material, preset:str = "FIGHTER"):
    meshes: set[bpy.types.Mesh] = {mesh for mesh in bpy.data.meshes if material in mesh.materials.values()}
    for mesh in meshes:
        if preset == 'FIGHTER':
            if len(mesh.uv_layers) > 2:
                operator.report({'WARNING'}, f"Can't rename UV Layers of mesh '{mesh.name}', theres more than 2 UV Layers! Please rename them manually, or remove the un-needed layers!")
            else:
                if len(mesh.uv_layers) == 2:
                    uv_layer_names = {uv_layer.name for uv_layer in mesh.uv_layers}
                    if 'map1' not in uv_layer_names:
                        mesh.uv_layers[0].name = 'map1'
                    if 'uvSet' not in uv_layer_names:
                        mesh.uv_layers[1].name = 'uvSet'
                if len(mesh.uv_layers) == 1:
                    if mesh.uv_layers[0].name != 'map1':
                        mesh.uv_layers[0].name = 'map1'
            if len(mesh.color_attributes) > 1:
                operator.report({'WARNING'}, f"Can't rename UV Layers of mesh '{mesh.name}', theres more than 2 UV Layers! Please rename them manually, or remove the un-needed layers!")
            else:
                if len(mesh.color_attributes) == 1:
                    if mesh.color_attributes[0].name != 'colorSet1':
                        mesh.color_attributes[0].name = 'colorSet1'
                        # Scale color_set_1 for intuitive results
                        for data in mesh.color_attributes[0].data:
                            data.color = [ value / 2 for value in data.color ]
def get_tex_image_going_to_linked_input(initial_input:NodeSocket, mix_nodes_between: int, layer: int) -> Image | None:
    if not initial_input.is_linked:
        return
    if layer not in (1,2):
        return
    if mix_nodes_between not in (0,1,2):
        return
    
    if mix_nodes_between == 0:
        tex_node = initial_input.links[0].from_node 
        
    elif mix_nodes_between == 1:
        mix_node = initial_input.links[0].from_node
        if not isinstance(mix_node, ShaderNodeMixRGB):
            return
        
        if not mix_node.inputs[f'Color{layer}'].is_linked:
            return
        
        tex_node = mix_node.inputs[f'Color{layer}'].links[0].from_node
        
    elif mix_nodes_between == 2:
        mix_vertex_colors_node = initial_input.links[0].from_node
        if not isinstance(mix_vertex_colors_node, ShaderNodeMixRGB):
            return
        
        if not mix_vertex_colors_node.inputs['Color1'].is_linked:
            return
        
        mix_textures_node = mix_vertex_colors_node.inputs['Color1'].links[0].from_node
        if not isinstance(mix_textures_node, ShaderNodeMixRGB):
            return
        
        if not mix_textures_node.inputs[f'Color{layer}'].is_linked:
            return
        
        tex_node = mix_textures_node.inputs[f'Color{layer}'].links[0].from_node

    if not isinstance(tex_node, ShaderNodeTexImage):
        return
    return tex_node.image   

def convert_emission(emission_node: ShaderNodeEmission, material: Material, vertex_color_count: int, uv_layer_count: int):
    if emission_node.inputs['Color'].is_linked is False:
        emission_color = emission_node.inputs['Color'].default_value[:]
        emission_strength = emission_node.inputs['Strength'].default_value
        emission_strength_linked = emission_node.inputs['Strength'].is_linked
        linked_emission_map: Image = None
        if emission_strength_linked:
            # User may be using a texture for the emission map
            pre_final_node = emission_node.inputs['Strength'].links[0].from_node
            if isinstance(pre_final_node, ShaderNodeTexImage):
                linked_emission_map = pre_final_node.image
        else:
            # User not using a map
            emission_strength = emission_node.inputs['Strength'].default_value   
        
        create_sub_matl_data_from_shader_label(material, "SFX_PBS_0000000000000100_opaque") # 1 Layer Shadeless Emissive
        sub_matl_data: SUB_PG_sub_matl_data = material.sub_matl_data
        cv3: SUB_PG_matl_vector = sub_matl_data.vectors.get(ParamId.CustomVector3.name)
        texture5: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture5.name)
        if emission_strength_linked:
            cv3.value = [emission_color[i] for i in (0,1,2,3)]
            texture5.image = linked_emission_map
        else:
            cv3.value = [emission_color[i] * emission_strength for i in (0,1,2,3)]
            texture5.image = bpy.data.images.get('/common/shader/sfxpbs/default_white')
        return
    
    emission_strength = emission_node.inputs['Strength'].default_value if not emission_node.inputs['Strength'].is_linked else 1.0
    emi_layer_1 = None
    emi_layer_2 = None
    if uv_layer_count >= 2:
        if vertex_color_count >= 1:
            # Texture -> Mix -> Mix -> Bsdf
            emi_layer_1 = get_tex_image_going_to_linked_input(emission_node.inputs['Color'], mix_nodes_between=2, layer=1)
            emi_layer_2 = get_tex_image_going_to_linked_input(emission_node.inputs['Color'], mix_nodes_between=2, layer=2)
            create_sub_matl_data_from_shader_label(material, "SFX_PBS_0120000810080100_opaque") # 2 Layer Shadeless + colorSet1
        else:
            # Texture -> Mix -> Bsdf
            emi_layer_1 = get_tex_image_going_to_linked_input(emission_node.inputs['Color'], mix_nodes_between=1, layer=1)
            emi_layer_2 = get_tex_image_going_to_linked_input(emission_node.inputs['Color'], mix_nodes_between=1, layer=2)
            create_sub_matl_data_from_shader_label(material, "SFX_PBS_0120000010008100_opaque") # 2 Layer Shadeless
    else:
        if vertex_color_count >= 1:
            # Texture -> Mix -> Bsdf
            emi_layer_1 = get_tex_image_going_to_linked_input(emission_node.inputs['Color'], mix_nodes_between=1, layer=1)
            create_sub_matl_data_from_shader_label(material, "SFX_PBS_0000000000080100_opaque") # 1 Layer Shadeless Emissive + colorSet1
        else:
            # Texture -> Bsdf
            emi_layer_1 = get_tex_image_going_to_linked_input(emission_node.inputs['Color'], mix_nodes_between=0, layer=1)
            create_sub_matl_data_from_shader_label(material, "SFX_PBS_0000000000000100_opaque") # 1 Layer Shadeless Emissive

    sub_matl_data: SUB_PG_sub_matl_data = material.sub_matl_data
    texture5: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture5.name)
    texture14: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture14.name)
    if texture5 is not None and emi_layer_1 is not None:
        texture5.image = emi_layer_1
    if texture14 is not None and emi_layer_2 is not None:
        texture14.image = emi_layer_2

def convert_principled_emission(principled_node: ShaderNodeBsdfPrincipled, material: Material, vertex_color_count: int, uv_layer_count: int):
    '''
    The assumption is that the user will use seperate images for the base col layer and the emmission layer
    '''
    col_layer_1 = None
    col_layer_2 = None
    emi_layer_1 = None
    emi_layer_2 = None
    
    emission_input = principled_node.inputs['Emission Color']
    emission_color = emission_input.default_value[:]
    was_emission_input_linked = emission_input.is_linked
    emission_strength_input: NodeSocketFloat = principled_node.inputs['Emission Strength']
    emission_strength = emission_strength_input.default_value if emission_strength_input.is_linked is False else 1

    if uv_layer_count >= 2:
        # No Fighter PBR with 2 diffuse, 2 emission, and colorSet1
        # Texture -> Mix -> Principled
        col_layer_1 = get_tex_image_going_to_linked_input(principled_node.inputs["Base Color"], mix_nodes_between=1, layer=1)
        col_layer_2 = get_tex_image_going_to_linked_input(principled_node.inputs["Base Color"], mix_nodes_between=1, layer=2)
        emi_layer_1 = get_tex_image_going_to_linked_input(principled_node.inputs["Emission Color"], mix_nodes_between=1, layer=1)
        emi_layer_2 = get_tex_image_going_to_linked_input(principled_node.inputs["Emission Color"], mix_nodes_between=1, layer=2)
        create_sub_matl_data_from_shader_label(material, "SFX_PBS_010000001a00824f_opaque") # PBR, 2 Diffuse, 2 Emmissive
    else:
        if vertex_color_count >= 1: 
            # Texture -> Mix -> Principled
            col_layer_1 = get_tex_image_going_to_linked_input(principled_node.inputs["Base Color"], mix_nodes_between=1, layer=1)
            emi_layer_1 = get_tex_image_going_to_linked_input(principled_node.inputs["Emission Color"], mix_nodes_between=1, layer=1)
            create_sub_matl_data_from_shader_label(material, "SFX_PBS_010000000a088269_opaque") # PBR, 1 Diffuse, 1 Emmissive  + colorset1
        else:
            # Texture -> Principled
            col_layer_1 = get_tex_image_going_to_linked_input(principled_node.inputs["Base Color"], mix_nodes_between=0, layer=1)
            emi_layer_1 = get_tex_image_going_to_linked_input(principled_node.inputs["Emission Color"], mix_nodes_between=0, layer=1)
            create_sub_matl_data_from_shader_label(material, "SFX_PBS_010000080a008269_opaque") # PBR, 1 Diffuse, 1 Emmissive
    
    sub_matl_data: SUB_PG_sub_matl_data = material.sub_matl_data
    
    texture0: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture0.name)
    texture1: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture1.name)
    texture5: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture5.name)
    texture14: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture14.name) 
    cv3: SUB_PG_matl_vector = sub_matl_data.vectors.get(ParamId.CustomVector3.name)
    
    texture0.image = col_layer_1 if col_layer_1 is not None else bpy.data.images.get('/common/shader/sfxpbs/default_white')
    if texture1 is not None:
        texture1.image = col_layer_2 if col_layer_2 is not None else bpy.data.images.get('/common/shader/sfxpbs/default_white')
        
    if was_emission_input_linked is False:
        cv3.value = [emission_color[i] * emission_strength for i in (0,1,2,3)]
        texture5.image = bpy.data.images.get('/common/shader/sfxpbs/default_white')
        if texture14 is not None:
            texture14.image = bpy.data.images.get('/common/shader/sfxpbs/default_white')
    else:
        cv3.value = [emission_strength for _ in (0,1,2,3)]
        texture5.image = emi_layer_1 if emi_layer_1 is not None else bpy.data.images.get('/common/shader/sfxpbs/default_black')
        if texture14 is not None:
            texture14.image = emi_layer_2 if emi_layer_2 is not None else bpy.data.images.get('/common/shader/sfxpbs/default_black')

def convert_principled_subsurface(operator: Operator, principled_node: ShaderNodeBsdfPrincipled, material: Material, vertex_color_count: int, uv_layer_count: int):
    col_layer_1 = None
    col_layer_2 = None
    sub_surface_color = None
    # Smash handles SSS differently and doesn't have a texture input for SSS color, its just uniform.
    # As of blender 4.0, the 'Subsurface Color' input no longer exists, instead the subsurface uses the "Radius" vector input for making the RGB transmit further into the mesh.
    """
    if principled_node.inputs['Subsurface Color'].is_linked:
        operator.report({'INFO'}, f"Material {material.name} converted to Ult PBR Mat w/ SSS, but please be aware the SSS color in smash is uniform (mesh-wide, set by CustomVector11), it can't be a map.")
    else:
        sub_surface_color = principled_node.inputs['Subsurface Color'].default_value[:]
    """

    # The factor multiplies the subsurf radius, it doesn't really "mix", so will ignore and set CV30.x to .5 as a reasonable starting point
    # sub_surface_factor = None 
    
    if uv_layer_count >= 2:
        # No shader for fighters with support for 2 UV maps and colorSet1
        # Texture -> Mix -> Principled
        col_layer_1 = get_tex_image_going_to_linked_input(principled_node.inputs["Base Color"], mix_nodes_between=1, layer=1)
        col_layer_2 = get_tex_image_going_to_linked_input(principled_node.inputs["Base Color"], mix_nodes_between=1, layer=2)
        create_sub_matl_data_from_shader_label(material, "SFX_PBS_010000000800824f_opaque") # PBR Fake SSS, 2 Layer
    else:
        if vertex_color_count >= 1:
            # Texture -> Mix -> Principled
            col_layer_1 = get_tex_image_going_to_linked_input(principled_node.inputs["Base Color"], mix_nodes_between=1, layer=1)
            create_sub_matl_data_from_shader_label(material, "SFX_PBS_010000080808826b_opaque") # PBR Fake SSS, 1 Layer + colorSet1
        else:
            # Texture -> Principled
            col_layer_1 = get_tex_image_going_to_linked_input(principled_node.inputs["Base Color"], mix_nodes_between=0, layer=1)
            create_sub_matl_data_from_shader_label(material, "SFX_PBS_010000080800826b_opaque") # PBR Fake SSS, 1 Layer
    
    sub_matl_data: SUB_PG_sub_matl_data = material.sub_matl_data
    texture0: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture0.name)
    texture1: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture1.name)
    cv11: SUB_PG_matl_vector = sub_matl_data.vectors.get(ParamId.CustomVector11.name)
    cv30: SUB_PG_matl_vector = sub_matl_data.vectors.get(ParamId.CustomVector30.name)
    if col_layer_1 is not None:
        texture0.image = col_layer_1
    if texture1 is not None and col_layer_2 is not None:
        texture1.image = col_layer_2
    if sub_surface_color is not None:
        cv11.value = [sub_surface_color[i] for i in (0,1,2,3)]
    cv30.value[0] = 0.5
    cv30.value[1] = 1.5

def convert_principled_standard(principled_node: ShaderNodeBsdfPrincipled, material: Material, vertex_color_count: int, uv_layer_count: int):
    col_layer_1 = None
    col_layer_2 = None
    if uv_layer_count >= 2:
        # No shader for fighters with support for 2 UV maps and colorSet1
        col_layer_1 = get_tex_image_going_to_linked_input(principled_node.inputs["Base Color"], mix_nodes_between=1, layer=1)
        col_layer_2 = get_tex_image_going_to_linked_input(principled_node.inputs["Base Color"], mix_nodes_between=1, layer=2)
        create_sub_matl_data_from_shader_label(material, "SFX_PBS_010000000800824f_opaque") # PBR, 2 Layer
    else:
        if vertex_color_count >= 1:
            col_layer_1 = get_tex_image_going_to_linked_input(principled_node.inputs["Base Color"], mix_nodes_between=1, layer=1)
            create_sub_matl_data_from_shader_label(material,"SFX_PBS_0100000008088269_opaque") # PBR, 1 Layer + colorSet1
        else:
            col_layer_1 = get_tex_image_going_to_linked_input(principled_node.inputs["Base Color"], mix_nodes_between=0, layer=1)
            create_sub_matl_data_from_shader_label(material, "SFX_PBS_0100000008008269_opaque") # PBR, 1 Layer
    sub_matl_data: SUB_PG_sub_matl_data = material.sub_matl_data
    texture0: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture0.name)
    texture1: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture1.name)
    if col_layer_1 is not None:
        texture0.image = col_layer_1
    if texture1 is not None and col_layer_2 is not None:
        texture1.image = col_layer_2

def is_fpv3_material(node: ShaderNode) -> bool:
    """Check if a node is a FPv3 Material from Fortnite"""
    # Check for group nodes with FPv3 in the name or label
    if node.type == 'GROUP' and ('FPv3' in node.name or 'FPv3' in node.label or 'Fortnite' in node.name):
        return True
    
    # Check for custom nodes with fpv3 in the identifier
    if hasattr(node, 'bl_idname') and ('fpv3' in node.bl_idname.lower() or 'fortnite' in node.bl_idname.lower()):
        return True
    
    # Check if it's a shader node with specific Fortnite inputs
    if node.type == 'BSDF_PRINCIPLED' or node.type == 'GROUP':
        # Check for common Fortnite-specific input names
        fortnite_inputs = ['Base Color', 'Base Roughness', 'Metallic', 'Specular', 'Emissive', 'SubSurface']
        fortnite_input_count = sum(1 for name in fortnite_inputs if name in node.inputs)
        
        # If it has most of the Fortnite inputs, it's likely the FPv3 Material
        if fortnite_input_count >= 4:
            # Check for connected textures with Fortnite naming patterns
            for input_socket in node.inputs.values():
                if input_socket.is_linked:
                    from_node = input_socket.links[0].from_node
                    if from_node.type == 'TEX_IMAGE' and hasattr(from_node, 'image') and from_node.image:
                        if any(suffix in from_node.image.name for suffix in ['_M', '_S', '_D', '_N']):
                            print(f"Detected Fortnite FPv3 Material node: {node.name}")
                            return True
    
    return False

def get_fortnite_textures(fpv3_node: ShaderNode) -> tuple:
    """
    Extract Fortnite-specific textures from a FPv3 Material node
    
    Args:
        fpv3_node: The FPv3 Material node
        
    Returns:
        Tuple of (m_texture, s_texture, d_texture, is_skin_material)
        m_texture: The _M texture (contains AO in red, subsurface in blue)
        s_texture: The _S texture (contains specular in red, roughness in green, metal in blue)
        d_texture: The _D texture (diffuse/albedo)
        is_skin_material: Whether this is a skin material
    """
    m_texture = None  # The _M texture (contains AO in red, subsurface in blue)
    s_texture = None  # The _S texture (contains specular in red, roughness in green, metal in blue)
    d_texture = None  # The _D texture (diffuse/albedo)
    is_skin_material = False
    
    # Check if this is a skin material by looking at subsurface inputs
    if hasattr(fpv3_node.inputs.get('Subsurface', None), 'default_value'):
        is_skin_material = fpv3_node.inputs['Subsurface'].default_value > 0.01
    elif hasattr(fpv3_node.inputs.get('Subsurface Weight', None), 'default_value'):
        is_skin_material = fpv3_node.inputs['Subsurface Weight'].default_value > 0.01
    
    # Look for specific texture connections
    for input_name in fpv3_node.inputs.keys():
        input_socket = fpv3_node.inputs.get(input_name)
        
        if not input_socket or not input_socket.is_linked:
            continue
        
        from_node = input_socket.links[0].from_node
        if from_node.type != 'TEX_IMAGE' or not from_node.image:
            continue
            
        image = from_node.image
        
        # Check image name for _M, _S, or _D patterns
        if image.name.endswith('_M') or '_M.' in image.name:
            m_texture = image
        elif image.name.endswith('_S') or '_S.' in image.name:
            s_texture = image
        elif image.name.endswith('_D') or '_D.' in image.name:
            d_texture = image
        
        # If we couldn't identify by name, try by input name
        if not any([m_texture, s_texture, d_texture]):
            if any(x in input_name.lower() for x in ['mask', 'ao', 'subsurface', 'sss']):
                m_texture = image
            elif any(x in input_name.lower() for x in ['specular', 'roughness', 'metallic', 'metal']):
                s_texture = image
            elif any(x in input_name.lower() for x in ['diffuse', 'albedo', 'base color', 'basecolor']):
                d_texture = image
    
    return m_texture, s_texture, d_texture, is_skin_material

def convert_fpv3_material(fpv3_node: ShaderNode, material: Material, vertex_color_count: int, uv_layer_count: int):
    """Convert a FPv3 Material to Smash Ultimate format"""
    print(f"Converting FPv3 Material node '{fpv3_node.name}' for material '{material.name}'")
    
    # First, create a standard PBR material as a base
    if uv_layer_count >= 2:
        create_sub_matl_data_from_shader_label(material, "SFX_PBS_010000000800824f_opaque")  # PBR, 2 Layer
        print(f"Created 2-layer PBR material for '{material.name}'")
    else:
        if vertex_color_count >= 1:
            create_sub_matl_data_from_shader_label(material, "SFX_PBS_0100000008088269_opaque")  # PBR, 1 Layer + colorSet1
            print(f"Created 1-layer PBR material with vertex colors for '{material.name}'")
        else:
            create_sub_matl_data_from_shader_label(material, "SFX_PBS_0100000008008269_opaque")  # PBR, 1 Layer
            print(f"Created 1-layer PBR material for '{material.name}'")
    
    sub_matl_data: SUB_PG_sub_matl_data = material.sub_matl_data
    
    # Look for Fortnite-specific textures first
    print(f"Searching for Fortnite textures in material '{material.name}'...")
    m_texture, s_texture, d_texture, is_skin_material = get_fortnite_textures(fpv3_node)
    
    if is_skin_material:
        print(f"Material '{material.name}' detected as a skin material - using _M Blue for metalness")
    else:
        print(f"Material '{material.name}' is a non-skin material - using _S Blue for metalness")
    
    print(f"Found textures: M={m_texture.name if m_texture else 'None'}, "
          f"S={s_texture.name if s_texture else 'None'}, "
          f"D={d_texture.name if d_texture else 'None'}")
    
    # Default texture connections
    diffuse_tex = d_texture  # Use the _D texture if found
    normal_tex = None
    emissive_tex = None
    
    # Get the texture connections from the FPv3 node
    print(f"Looking for additional texture connections in FPv3 node...")
    for input_name in fpv3_node.inputs.keys():
        input_socket = fpv3_node.inputs.get(input_name)
        
        if not input_socket or not input_socket.is_linked:
            continue
        
        from_node = input_socket.links[0].from_node
        if from_node.type != 'TEX_IMAGE' or not from_node.image:
            continue
            
        image = from_node.image
        
        # Skip _M and _S textures as they'll be handled specially
        if image == m_texture or image == s_texture or image == d_texture:
            continue
        
        # Map Fortnite texture inputs to Smash Ultimate texture slots
        if any(x in input_name.lower() for x in ['diffuse', 'albedo', 'color', 'base color']) and not diffuse_tex:
            diffuse_tex = image
            print(f"Found diffuse texture '{diffuse_tex.name}' from input '{input_name}'")
        elif 'normal' in input_name.lower() or input_name.endswith('_N'):
            normal_tex = image
            print(f"Found normal texture '{normal_tex.name}' from input '{input_name}'")
        elif 'emissive' in input_name.lower() or 'emission' in input_name.lower() or input_name.endswith('_E'):
            emissive_tex = image
            print(f"Found emission texture '{emissive_tex.name}' from input '{input_name}'")
    
    # Assign textures to appropriate slots
    print(f"Assigning textures to Smash Ultimate material slots...")
    texture0: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture0.name)  # COL/Diffuse
    texture4: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture4.name)  # NOR/Normal
    texture6: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture6.name)  # PRM/Params
    texture5: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture5.name)  # EMI/Emission
    
    if diffuse_tex:
        texture0.image = diffuse_tex
        print(f"Assigned '{diffuse_tex.name}' to Texture0 (COL)")
    
    if normal_tex:
        texture4.image = normal_tex
        print(f"Assigned '{normal_tex.name}' to Texture4 (NOR)")
    
    # For emission, if present
    if emissive_tex and texture5:
        texture5.image = emissive_tex
        print(f"Assigned '{emissive_tex.name}' to Texture5 (EMI)")
        
        # Set emission color vector
        cv3: SUB_PG_matl_vector = sub_matl_data.vectors.get(ParamId.CustomVector3.name)
        if cv3:
            cv3.value = [1.0, 1.0, 1.0, 1.0]  # White emission color, intensity controlled by texture
            print(f"Set emission color to white")
    
    # CustomVector47 holds metalness, roughness, ao, specular values when using mesh-wide values
    cv47: SUB_PG_matl_vector = sub_matl_data.vectors.get(ParamId.CustomVector47.name)
    if cv47:
        # Default values
        metalness = 0.0
        roughness = 0.5
        ao = 1.0
        specular = 0.16
        
        # Set values based on inputs
        if hasattr(fpv3_node.inputs.get('Metallic', None), 'default_value'):
            metalness = fpv3_node.inputs['Metallic'].default_value
            print(f"Using metallic value from input: {metalness}")
        
        if hasattr(fpv3_node.inputs.get('Roughness', None), 'default_value'):
            roughness = fpv3_node.inputs['Roughness'].default_value
            print(f"Using roughness value from input: {roughness}")
        
        if hasattr(fpv3_node.inputs.get('Specular', None), 'default_value'):
            # Scale to Smash Ultimate's range (0-0.2)
            specular = min(fpv3_node.inputs['Specular'].default_value * 0.2, 0.2)
            print(f"Using specular value from input: {specular}")
        
        # Set mesh-wide values (these will be overridden by the PRM texture if available)
        cv47.value = (metalness, roughness, ao, specular)
        print(f"Set CustomVector47 to ({metalness}, {roughness}, {ao}, {specular})")
    
    print(f"FPv3 Material conversion complete for '{material.name}'")
    print(f"Now the PRM converter will handle texture creation using the Fortnite texture channel mapping")
    # Let the PRM converter handle the actual texture creation from the _M and _S textures

def convert_from_nodes(operator: bpy.types.Operator, material: bpy.types.Material):
    '''
    Just trys to handle common simple shader node setups, anything too complicated or broken
    will just be assigned a standard PBR material.
    '''
    # Gets the output node, prioritizing the EEVEE-specific node if multiple are present
    output_node: ShaderNodeOutputMaterial = material.node_tree.get_output_node('EEVEE')
    
    # This would mean the model isn't rendering in eevee, which indicates the material is incomplete.
    if output_node is None:
        operator.report({'WARNING'}, f'The material "{material.name}" has no eevee output! Converting to default PBR material.')
        create_sub_matl_data_from_shader_label(material, "SFX_PBS_0100000008008269_opaque")
        return
    
    # An eevee output with no links to its surface indicates the material is incomplete.
    if len(output_node.inputs['Surface'].links) == 0:
        operator.report({'WARNING'}, f'The material "{material.name}" has an eevee output but nothing connected to it! Converting to default PBR material.')
        create_sub_matl_data_from_shader_label(material, "SFX_PBS_0100000008008269_opaque")
        return
    
    # For now, will handle vertex color counts of 0 or 1, as those can get assigned a standard PBR material.
    # Assume 1st is `colorSet1`
    # 2+ is a more advanced material, the user can just create from the needed shader label manually.
    vertex_color_count = get_vertex_color_count(material.node_tree.nodes)

    # For now, will handle 1 or 2 UV maps, assume 1st is `map1` and 2nd `uvSet`
    # 3+ is a more advanced material, the user can just create from the needed shader label manually.
    uv_layer_count = get_uv_layer_count(material.node_tree.nodes)

    final_node:ShaderNode = output_node.inputs['Surface'].links[0].from_node
    
    # Check for FPv3 Material first
    if is_fpv3_material(final_node):
        convert_fpv3_material(final_node, material, vertex_color_count, uv_layer_count)
    elif isinstance(final_node, ShaderNodeEmission):
        convert_emission(final_node, material, vertex_color_count, uv_layer_count)
    elif isinstance(final_node, ShaderNodeBsdfPrincipled):
        if principled_uses_emission(final_node):
            convert_principled_emission(final_node, material, vertex_color_count, uv_layer_count)
        elif principled_uses_subsurface(final_node):
            convert_principled_subsurface(operator, final_node, material, vertex_color_count, uv_layer_count)
        else:
            convert_principled_standard(final_node, material, vertex_color_count, uv_layer_count)
    else: # More complex node setup
        if uv_layer_count >= 2:
            create_sub_matl_data_from_shader_label(material, "SFX_PBS_010000000800824f_opaque") # PBR, 2 Layer
        else:
            if vertex_color_count >= 1:
                create_sub_matl_data_from_shader_label(material,"SFX_PBS_0100000008088269_opaque") # PBR, 1 Layer + colorSet1
            else:
                create_sub_matl_data_from_shader_label(material, "SFX_PBS_0100000008008269_opaque") # PBR, 1 Layer
   
def convert_blender_material(operator: bpy.types.Operator, material: bpy.types.Material, bake_size=1024):
    """
    Convert a Blender material to Smash Ultimate format with PRM and normal textures
    
    Args:
        operator: The operator calling this function (for reporting)
        material: The Blender material to convert
        bake_size: The size of textures to bake
    
    Returns:
        True if successful, False otherwise
    """
    start_time = time.time()
    operator.report({'INFO'}, f"Starting conversion of material '{material.name}'")
    
    # Check if bake_size is reasonable - limit to prevent freezing
    max_safe_size = 2048
    if bake_size > max_safe_size:
        operator.report({'WARNING'}, f"Reducing texture size from {bake_size} to {max_safe_size} to prevent potential freezing")
        bake_size = max_safe_size
    
    original_engine = bpy.context.scene.render.engine
    
    # Check if Cycles render engine is available and active
    try:
        cycles_available = 'CYCLES' in [getattr(render, 'bl_idname', '') for render in bpy.types.RenderEngine.__subclasses__()]
        if not cycles_available:
            operator.report({'WARNING'}, "Cycles render engine is required for baking textures but is not available")
            # We'll still try to convert but without baking
        
        # Look for existing textures
        normal_map_img = None
        if material.use_nodes and material.node_tree:
            for node in material.node_tree.nodes:
                if node.type == 'NORMAL_MAP':
                    if node.inputs['Color'].links:
                        tex_node = node.inputs['Color'].links[0].from_node
                        if tex_node.type == 'TEX_IMAGE' and tex_node.image:
                            normal_map_img = tex_node.image
                            break
                elif node.type == 'TEX_IMAGE' and node.image:
                    # Check if the image name contains common normal map identifiers
                    if any(x in node.image.name.lower() for x in ['normal', 'nor', '_n.', '_n_']):
                        normal_map_img = node.image
                        break
        
        # Check if this is an FPv3 material 
        is_fpv3_material_type = False
        fpv3_node = None
        if material.use_nodes and material.node_tree:
            output_node = material.node_tree.get_output_node('EEVEE')
            if output_node and len(output_node.inputs['Surface'].links) > 0:
                final_node = output_node.inputs['Surface'].links[0].from_node
                if is_fpv3_material(final_node):
                    is_fpv3_material_type = True
                    fpv3_node = final_node
                    operator.report({'INFO'}, f"Detected Fortnite FPv3 Material node: {final_node.name}")
        
        # Create the PRM texture
        prm_path = os.path.join(tempfile.gettempdir(), f"{material.name}_PRM.png")
        
        # Set a timeout for texture creation
        timeout = 120  # seconds
        
        if cycles_available:
            try:
                operator.report({'INFO'}, f"Creating PRM texture for material '{material.name}'")
                
                # Try to create the PRM texture
                try:
                    # First try with specified bake_size
                    texture_start_time = time.time()
                    operator.report({'INFO'}, f"Attempting to create PRM texture with size {bake_size}...")
                    
                    # Use the new direct image creation approach
                    if is_fpv3_material_type:
                        operator.report({'INFO'}, f"Material '{material.name}' is an FPv3 Material, using Fortnite texture mappings")
                    
                    # First attempt
                    prm_img = create_prm_from_material(material, None, bake_size=bake_size)
                    
                    # Check if we timed out or not
                    time_taken = time.time() - texture_start_time
                    operator.report({'INFO'}, f"PRM creation took {time_taken:.2f} seconds")
                    
                    # If it took too long, warn the user that future conversions might be slow
                    if time_taken > 30:
                        operator.report({'WARNING'}, f"PRM creation took longer than expected ({time_taken:.2f}s). Consider using a smaller texture size.")
                    
                except Exception as e:
                    # If failed with specified size, try with a smaller size
                    operator.report({'WARNING'}, f"Failed to create PRM with size {bake_size}: {e}. Trying with smaller size...")
                    
                    # Try with half the size
                    fallback_size = max(bake_size // 2, 512)
                    operator.report({'INFO'}, f"Attempting to create PRM texture with reduced size {fallback_size}...")
                    prm_img = create_prm_from_material(material, None, bake_size=fallback_size)
                
                # No path means it returned the image object directly
                if isinstance(prm_img, bpy.types.Image):
                    operator.report({'INFO'}, f"Successfully created PRM texture as internal Blender image")
                else:
                    operator.report({'INFO'}, f"Successfully created PRM texture at: {prm_img}")
                
            except Exception as e:
                import traceback
                traceback.print_exc()
                operator.report({'ERROR'}, f"Failed to create PRM texture: {e}")
                return False
        else:
            operator.report({'WARNING'}, "Cycles render engine not available. Using default PRM texture.")
            # Create a simple default PRM texture as fallback
            create_default_prm_texture(prm_path, bake_size)
        
        # Convert the material to Smash Ultimate format
        # Get number of UV layers and vertex color attributes
        uv_layer_count = 0
        vertex_color_count = 0
        meshes: set[bpy.types.Mesh] = {mesh for mesh in bpy.data.meshes if material in mesh.materials.values()}
        if meshes:
            uv_layer_count = max([len(mesh.uv_layers) for mesh in meshes])
            vertex_color_count = max([len(mesh.color_attributes) for mesh in meshes])
        
        operator.report({'INFO'}, f"Converting material with {uv_layer_count} UV layers and {vertex_color_count} vertex color sets")
        
        # First convert from nodes to get the basic material setup
        if material.use_nodes and material.node_tree:
            convert_from_nodes(operator, material)
        else:
            # Create a simple PBR material
            if uv_layer_count >= 2:
                create_sub_matl_data_from_shader_label(material, "SFX_PBS_010000000800824f_opaque") # PBR, 2 Layer
            else:
                if vertex_color_count >= 1:
                    create_sub_matl_data_from_shader_label(material,"SFX_PBS_0100000008088269_opaque") # PBR, 1 Layer + colorSet1
                else:
                    create_sub_matl_data_from_shader_label(material, "SFX_PBS_0100000008008269_opaque") # PBR, 1 Layer
            
            # Apply a simple diffuse color
            if hasattr(material, "diffuse_color"):
                cv13: SUB_PG_matl_vector = material.sub_matl_data.vectors.get(ParamId.CustomVector13.name)
                if cv13:
                    cv13.value = material.diffuse_color
        
        # Assign the PRM texture
        sub_matl_data: SUB_PG_sub_matl_data = material.sub_matl_data
        texture6: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture6.name)
        if texture6:
            # Find the PRM image
            prm_img = None
            for img in bpy.data.images:
                if img.name == f"{material.name}_PRM":
                    prm_img = img
                    break
            
            if prm_img:
                texture6.image = prm_img
                operator.report({'INFO'}, f"Assigning PRM texture: {prm_img.name}")
                
                # IMPORTANT: When using a PRM texture, we should NOT use CustomVector47
                # CustomVector47 is for mesh-wide uniform values, while Texture6 is for per-pixel values
                # Make sure CustomVector47 is cleared or set to use texture mode
                cv47: SUB_PG_matl_vector = sub_matl_data.vectors.get(ParamId.CustomVector47.name)
                if cv47:
                    # Set CustomVector47 to default values that won't interfere with the texture
                    # These values should be ignored when a PRM texture is present
                    cv47.value = (0.0, 0.5, 1.0, 0.16)  # Default: non-metallic, medium roughness, full AO, low specular
                    operator.report({'INFO'}, f"Set CustomVector47 to default values since PRM texture is being used")
                
                # Set the color space correctly for PRM texture
                prm_img.colorspace_settings.name = 'Non-Color'
                operator.report({'INFO'}, f"Set PRM texture color space to Non-Color")
            else:
                operator.report({'WARNING'}, "Could not find PRM texture")
        
        # Assign the normal map
        texture4: SUB_PG_matl_texture = sub_matl_data.textures.get(ParamId.Texture4.name)
        if texture4 and normal_map_img:
            texture4.image = normal_map_img
            operator.report({'INFO'}, f"Assigning existing normal map: {normal_map_img.name}")
        elif texture4:
            # Look for a normal map image with a matching name
            for img in bpy.data.images:
                if img.name.lower().endswith('_n') or '_n.' in img.name.lower():
                    if material.name.lower() in img.name.lower():
                        texture4.image = img
                        operator.report({'INFO'}, f"Found and assigned matching normal map: {img.name}")
                        break
            
            # If still no normal map, check for one in the material's node tree
            if not texture4.image and material.use_nodes and material.node_tree:
                for node in material.node_tree.nodes:
                    if node.type == 'TEX_IMAGE' and node.image:
                        if node.image.name.lower().endswith('_n') or '_n.' in node.image.name.lower():
                            texture4.image = node.image
                            operator.report({'INFO'}, f"Found normal map in node tree: {node.image.name}")
                            break
        
        # Restore original render engine if needed
        if bpy.context.scene.render.engine != original_engine:
            bpy.context.scene.render.engine = original_engine
        
        operator.report({'INFO'}, f"Material conversion completed in {time.time() - start_time:.2f} seconds")
        return True
        
    except Exception as e:
        operator.report({'ERROR'}, f"Error converting material: {e}")
        import traceback
        traceback.print_exc()
        
        # Restore original render engine if needed
        if bpy.context.scene.render.engine != original_engine:
            bpy.context.scene.render.engine = original_engine
            
        return False

def has_principled_bsdf_node(material):
    """Check if the material has a Principled BSDF node"""
    if not material or not material.node_tree:
        return False
    
    for node in material.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            return True
    
    return False

def load_and_assign_texture(material, texture_path, param_id_name):
    """Load a texture from a file and assign it to the appropriate texture parameter in the material"""
    if not os.path.exists(texture_path):
        print(f"Texture path does not exist: {texture_path}")
        return None
    
    # Load the image
    image_name = os.path.basename(texture_path)
    try:
        # Check if image is already loaded
        if image_name in bpy.data.images:
            image = bpy.data.images[image_name]
        else:
            image = bpy.data.images.load(texture_path)
            image.name = image_name
            
        # Pack the image into the .blend file
        if not image.packed_file:
            image.pack()
        
        # Set correct color space for NOR/PRM
        if param_id_name in ["Texture4", "Texture6"]:  # NOR and PRM
            image.colorspace_settings.name = 'Non-Color'
            
        # Assign the image to the material's texture parameter
        sub_matl_data = material.sub_matl_data
        
        # Debug info
        print(f"Available textures in material:")
        for texture in sub_matl_data.textures:
            print(f"  - {texture.param_id_name}")
            
        for texture in sub_matl_data.textures:
            if texture.param_id_name == param_id_name:
                print(f"Found matching texture param: {param_id_name}")
                texture.image = image
                return image
        
        print(f"No matching texture parameter found: {param_id_name}")
        
    except Exception as e:
        print(f"Error loading texture {texture_path}: {str(e)}")
        return None
    
    return None




    

