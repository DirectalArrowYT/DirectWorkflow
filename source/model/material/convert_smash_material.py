import bpy
import tempfile
import os
from bpy.types import ShaderNode, ShaderNodeTexImage, ShaderNodeBsdfPrincipled, ShaderNodeOutputMaterial, ShaderNodeSeparateRGB, ShaderNodeNormalMap, Material, Operator
from ....dependencies import ssbh_data_py

ParamId = ssbh_data_py.matl_data.ParamId

def create_separate_rgb_node(node_tree, location=(0, 0)):
    """Create a Separate RGB node (or Separate Color in newer Blender versions)"""
    # Try newer Separate Color node first, fallback to legacy Separate RGB
    try:
        separate_node = node_tree.nodes.new('ShaderNodeSeparateColor')
        separate_node.mode = 'RGB'  # Set mode for color separation
    except:
        # Fallback to legacy Separate RGB node for older Blender versions
        try:
            separate_node = node_tree.nodes.new('ShaderNodeSeparateRGB')
        except:
            # If both fail, create a group node that mimics the functionality
            separate_node = node_tree.nodes.new('ShaderNodeGroup')
            # We would need to create a custom node group here
            # For now, return None to indicate failure
            return None
    
    separate_node.location = location
    return separate_node

def decompose_prm_texture(material: Material, prm_image: bpy.types.Image) -> dict:
    """
    Decompose a PRM texture into separate components for metalness, roughness, ambient occlusion, and specular.
    Returns a dictionary with the decomposed node outputs.
    """
    if not material.use_nodes:
        material.use_nodes = True
    
    node_tree = material.node_tree
    nodes = node_tree.nodes
    links = node_tree.links
    
    # Create a PRM texture node
    prm_tex_node = nodes.new('ShaderNodeTexImage')
    prm_tex_node.image = prm_image
    prm_tex_node.location = (-800, 0)
    prm_tex_node.label = "PRM Texture"
    prm_tex_node.name = "PRM_Source"
    
    # Set color space to Non-Color since PRM contains data, not color
    if prm_image:
        prm_image.colorspace_settings.name = 'Non-Color'
    
    # Create separate RGB nodes to extract individual channels
    separate_rgb_node = create_separate_rgb_node(node_tree, (-600, 0))
    if separate_rgb_node is None:
        # Fallback: use the texture node outputs directly
        return {
            'metalness': prm_tex_node.outputs['Color'],  # Red channel
            'roughness': prm_tex_node.outputs['Color'],  # Green channel - will need manual extraction
            'ambient_occlusion': prm_tex_node.outputs['Color'],  # Blue channel - will need manual extraction
            'specular': prm_tex_node.outputs['Alpha']  # Alpha channel
        }
    
    separate_rgb_node.label = "PRM Channel Separation"
    separate_rgb_node.name = "PRM_Separate"
    
    # Connect PRM texture to separate RGB
    links.new(prm_tex_node.outputs['Color'], separate_rgb_node.inputs[0])
    
    return {
        'metalness': separate_rgb_node.outputs[0],     # Red channel
        'roughness': separate_rgb_node.outputs[1],     # Green channel  
        'ambient_occlusion': separate_rgb_node.outputs[2],  # Blue channel
        'specular': prm_tex_node.outputs['Alpha'],     # Alpha channel
        'prm_texture_node': prm_tex_node,
        'prm_separate_node': separate_rgb_node
    }

def convert_smash_material_to_principled(operator: Operator, material: Material):
    """
    Convert a Smash Ultimate material to a standard Principled BSDF material.
    This function decomposes the PRM texture and sets up proper connections.
    """
    if not hasattr(material, 'sub_matl_data') or not material.sub_matl_data:
        operator.report({'ERROR'}, f"Material '{material.name}' is not a Smash Ultimate material")
        return False
    
    if not material.use_nodes:
        material.use_nodes = True
    
    # Clear existing nodes to start fresh
    material.node_tree.nodes.clear()
    
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    
    # Get the Smash material data
    sub_matl_data = material.sub_matl_data
    
    # Find the relevant textures
    texture0 = None  # COL (Diffuse/Albedo)
    texture4 = None  # NOR (Normal map)
    texture6 = None  # PRM (Metalness, Roughness, AO, Specular)
    
    for texture in sub_matl_data.textures:
        if texture.param_id_name == ParamId.Texture0.name and texture.image:
            texture0 = texture.image
        elif texture.param_id_name == ParamId.Texture4.name and texture.image:
            texture4 = texture.image
        elif texture.param_id_name == ParamId.Texture6.name and texture.image:
            texture6 = texture.image
    
    # Create the Principled BSDF node
    principled_node = nodes.new('ShaderNodeBsdfPrincipled')
    principled_node.location = (300, 0)
    principled_node.label = "Converted from Smash Material"
    principled_node.name = "Principled_BSDF"
    
    # Create Material Output nodes for both EEVEE and Cycles
    eevee_output = nodes.new('ShaderNodeOutputMaterial')
    eevee_output.location = (600, 100)
    eevee_output.target = 'EEVEE'
    eevee_output.label = 'EEVEE Output'
    eevee_output.name = 'eevee_output'
    
    cycles_output = nodes.new('ShaderNodeOutputMaterial')
    cycles_output.location = (600, -100)
    cycles_output.target = 'CYCLES'
    cycles_output.label = 'Cycles Output'
    cycles_output.name = 'cycles_output'
    
    # Connect Principled BSDF to both outputs
    links.new(principled_node.outputs['BSDF'], eevee_output.inputs['Surface'])
    links.new(principled_node.outputs['BSDF'], cycles_output.inputs['Surface'])
    
    # Set up diffuse/albedo texture (Texture0)
    if texture0:
        diffuse_tex_node = nodes.new('ShaderNodeTexImage')
        diffuse_tex_node.image = texture0
        diffuse_tex_node.location = (-600, 300)
        diffuse_tex_node.label = "Diffuse/Albedo (COL)"
        diffuse_tex_node.name = "COL_Texture"
        
        # Create UV map node for diffuse
        uv_map_node = nodes.new('ShaderNodeUVMap')
        uv_map_node.location = (-800, 300)
        uv_map_node.uv_map = 'map1'  # Default UV map for COL textures
        uv_map_node.label = "UV Map (map1)"
        
        # Connect UV to diffuse texture
        links.new(uv_map_node.outputs['UV'], diffuse_tex_node.inputs['Vector'])
        
        # Connect diffuse to principled base color
        links.new(diffuse_tex_node.outputs['Color'], principled_node.inputs['Base Color'])
        
        operator.report({'INFO'}, f"Connected diffuse texture: {texture0.name}")
    
    # Set up normal map (Texture4)
    if texture4:
        normal_tex_node = nodes.new('ShaderNodeTexImage')
        normal_tex_node.image = texture4
        normal_tex_node.location = (-600, 100)
        normal_tex_node.label = "Normal Map (NOR)"
        normal_tex_node.name = "NOR_Texture"
        
        # Set color space to Non-Color for normal maps
        texture4.colorspace_settings.name = 'Non-Color'
        
        # Create normal map node
        normal_map_node = nodes.new('ShaderNodeNormalMap')
        normal_map_node.location = (-300, 100)
        normal_map_node.label = "Normal Map"
        normal_map_node.name = "Normal_Map"
        
        # Create UV map node for normal
        normal_uv_node = nodes.new('ShaderNodeUVMap')
        normal_uv_node.location = (-800, 100)
        normal_uv_node.uv_map = 'map1'  # Default UV map for normal textures
        normal_uv_node.label = "UV Map (map1)"
        
        # Connect UV to normal texture
        links.new(normal_uv_node.outputs['UV'], normal_tex_node.inputs['Vector'])
        
        # Connect normal texture to normal map node
        links.new(normal_tex_node.outputs['Color'], normal_map_node.inputs['Color'])
        
        # Connect normal map to principled normal input
        links.new(normal_map_node.outputs['Normal'], principled_node.inputs['Normal'])
        
        operator.report({'INFO'}, f"Connected normal map: {texture4.name}")
    
    # Set up PRM texture decomposition (Texture6)
    if texture6:
        prm_components = decompose_prm_texture(material, texture6)
        
        # Position the PRM nodes
        prm_components['prm_texture_node'].location = (-600, -300)
        if 'prm_separate_node' in prm_components:
            prm_components['prm_separate_node'].location = (-300, -300)
        
        # Create UV map node for PRM
        prm_uv_node = nodes.new('ShaderNodeUVMap')
        prm_uv_node.location = (-800, -300)
        prm_uv_node.uv_map = 'map1'  # Default UV map for PRM textures
        prm_uv_node.label = "UV Map (map1)"
        
        # Connect UV to PRM texture
        links.new(prm_uv_node.outputs['UV'], prm_components['prm_texture_node'].inputs['Vector'])
        
        # Connect PRM components to Principled BSDF
        links.new(prm_components['metalness'], principled_node.inputs['Metallic'])
        links.new(prm_components['roughness'], principled_node.inputs['Roughness'])
        
        # For ambient occlusion, we'll need to create a mix node to multiply with base color
        if texture0:  # Only if we have a diffuse texture to mix with
            ao_mix_node = nodes.new('ShaderNodeMixRGB')
            ao_mix_node.location = (0, 200)
            ao_mix_node.blend_type = 'MULTIPLY'
            ao_mix_node.inputs['Fac'].default_value = 1.0
            ao_mix_node.label = "AO Mix"
            ao_mix_node.name = "AO_Mix"
            
            # Find and store the base color connection info before removing
            base_color_from_socket = None
            base_color_from_node = None
            for link in links:
                if link.to_node == principled_node and link.to_socket.name == 'Base Color':
                    base_color_from_socket = link.from_socket
                    base_color_from_node = link.from_node
                    break
            
            if base_color_from_socket and base_color_from_node:
                # Remove the direct connection by removing all links to this input
                # Create a list copy to avoid modifying collection while iterating
                base_color_links = list(principled_node.inputs['Base Color'].links)
                for link in base_color_links:
                    links.remove(link)
                
                # Connect diffuse to mix node Color1
                links.new(base_color_from_socket, ao_mix_node.inputs['Color1'])
                
                # Connect AO to mix node Color2 (using white as base, AO as factor)
                ao_mix_node.inputs['Color2'].default_value = (1.0, 1.0, 1.0, 1.0)  # White base
                links.new(prm_components['ambient_occlusion'], ao_mix_node.inputs['Fac'])
                
                # Connect mix result to principled base color
                links.new(ao_mix_node.outputs['Color'], principled_node.inputs['Base Color'])
        
        # Connect specular (note: Blender's specular input changed in 4.0+)
        # Check which specular input is available
        specular_input_name = None
        if 'Specular' in principled_node.inputs:
            specular_input_name = 'Specular'
        elif 'Specular IOR' in principled_node.inputs:
            specular_input_name = 'Specular IOR'
        
        if specular_input_name:
            if specular_input_name == 'Specular':
                # Legacy Blender: Smash Ultimate: 0-0.2, Blender legacy: 0-1.0, so we need to scale
                specular_math_node = nodes.new('ShaderNodeMath')
                specular_math_node.operation = 'MULTIPLY'
                specular_math_node.location = (0, -400)
                specular_math_node.inputs[1].default_value = 5.0  # Scale factor to convert from Smash to Blender range
                specular_math_node.label = "Specular Scale"
                specular_math_node.name = "Specular_Scale"
                
                links.new(prm_components['specular'], specular_math_node.inputs[0])
                links.new(specular_math_node.outputs['Value'], principled_node.inputs[specular_input_name])
            else:
                # Modern Blender (4.0+): Use IOR mapping
                # Smash specular range 0-0.2 maps to IOR range roughly 1.0-1.5
                specular_math_node = nodes.new('ShaderNodeMath')
                specular_math_node.operation = 'MULTIPLY'
                specular_math_node.location = (0, -400)
                specular_math_node.inputs[1].default_value = 2.5  # Scale factor for IOR
                specular_math_node.label = "Specular to IOR"
                specular_math_node.name = "Specular_to_IOR"
                
                # Add offset to get into proper IOR range (1.0 base + scaled specular)
                specular_add_node = nodes.new('ShaderNodeMath')
                specular_add_node.operation = 'ADD'
                specular_add_node.location = (150, -400)
                specular_add_node.inputs[1].default_value = 1.0  # Base IOR
                specular_add_node.label = "IOR Offset"
                specular_add_node.name = "IOR_Offset"
                
                links.new(prm_components['specular'], specular_math_node.inputs[0])
                links.new(specular_math_node.outputs['Value'], specular_add_node.inputs[0])
                links.new(specular_add_node.outputs['Value'], principled_node.inputs[specular_input_name])
        else:
            operator.report({'WARNING'}, "Could not find Specular or Specular IOR input on Principled BSDF")
        
        operator.report({'INFO'}, f"Decomposed PRM texture: {texture6.name}")
        operator.report({'INFO'}, "PRM channels mapped: Red->Metallic, Green->Roughness, Blue->AO, Alpha->Specular")
    else:
        # If no PRM texture, use CustomVector47 values if available
        cv47_vector = None
        for vector in sub_matl_data.vectors:
            if vector.param_id_name == ParamId.CustomVector47.name:
                cv47_vector = vector
                break
        
        if cv47_vector:
            # CustomVector47 format: (metalness, roughness, ao, specular)
            principled_node.inputs['Metallic'].default_value = cv47_vector.value[0]
            principled_node.inputs['Roughness'].default_value = cv47_vector.value[1]
            # AO (value[2]) and Specular (value[3]) are handled differently in Principled BSDF
            
            # Handle specular input based on Blender version
            if 'Specular' in principled_node.inputs:
                principled_node.inputs['Specular'].default_value = cv47_vector.value[3] * 5.0  # Scale to Blender range
            elif 'Specular IOR' in principled_node.inputs:
                principled_node.inputs['Specular IOR'].default_value = 1.0 + (cv47_vector.value[3] * 2.5)  # Map to IOR range
            
            operator.report({'INFO'}, f"Applied CustomVector47 values: Metallic={cv47_vector.value[0]:.3f}, Roughness={cv47_vector.value[1]:.3f}, Specular={cv47_vector.value[3]:.3f}")
    
    # Set some default material properties
    material.use_backface_culling = True  # Default for most materials
    material.blend_method = 'OPAQUE'  # Default blend method
    
    # Add a label to identify this as a converted material
    principled_node.label = f"Converted from {sub_matl_data.shader_label}"
    
    operator.report({'INFO'}, f"Successfully converted Smash material '{material.name}' to Principled BSDF")
    return True

def has_smash_material_data(material: Material) -> bool:
    """Check if the material has Smash Ultimate material data"""
    return hasattr(material, 'sub_matl_data') and material.sub_matl_data and material.sub_matl_data.shader_label != ""

def has_prm_texture(material: Material) -> bool:
    """Check if the material has a PRM texture (Texture6)"""
    if not has_smash_material_data(material):
        return False
    
    for texture in material.sub_matl_data.textures:
        if texture.param_id_name == ParamId.Texture6.name and texture.image:
            return True
    return False

def is_converted_to_principled(material: Material) -> bool:
    """Check if a Smash material has been converted to Principled BSDF (nodes cleared/replaced)"""
    if not has_smash_material_data(material):
        return False
    
    if not material.use_nodes or not material.node_tree:
        return False
    
    # Check if the current node tree has the "Converted from" label on a Principled BSDF
    for node in material.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            if 'Converted from' in node.label:
                return True
    
    return False

def revert_to_smash_material(operator: Operator, material: Material):
    """
    Revert a converted Principled BSDF material back to the original Smash Ultimate material.
    Uses the preserved sub_matl_data to rebuild the Smash material node tree.
    """
    if not has_smash_material_data(material):
        operator.report({'ERROR'}, f"Material '{material.name}' does not have Smash Ultimate material data")
        return False
    
    # Import the function to rebuild the Smash material node tree
    from .create_blender_materials_from_matl import setup_blender_material_node_tree, setup_blender_material_settings
    
    try:
        # Rebuild the Smash material node tree from sub_matl_data
        setup_blender_material_node_tree(material)
        setup_blender_material_settings(material)
        
        operator.report({'INFO'}, f"Successfully reverted material '{material.name}' to Smash Ultimate material")
        return True
    except Exception as e:
        operator.report({'ERROR'}, f"Failed to revert material '{material.name}': {str(e)}")
        return False
