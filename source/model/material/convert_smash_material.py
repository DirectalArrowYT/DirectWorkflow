import bpy
import tempfile
import os
from bpy.types import Material, Operator
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

def _texture_slot_names(texture):
    return {
        str(getattr(texture, "param_id_name", "") or ""),
        str(getattr(texture, "node_name", "") or ""),
        str(getattr(texture, "name", "") or ""),
    }


def _slot_number(texture):
    number = getattr(texture, "texture_number", None)
    if isinstance(number, int):
        return number
    for name in _texture_slot_names(texture):
        if name.startswith("Texture") and name[7:].isdigit():
            return int(name[7:])
    return None


def _image_name(image) -> str:
    return (getattr(image, "name", "") or "").replace("\\", "/").lower()


def _is_dummy_image(image) -> bool:
    if image is None:
        return True
    name = _image_name(image)
    dummy_tokens = (
        "default_white",
        "default_black",
        "default_gray",
        "default_normal",
        "default_params",
        "/common/shader/sfxpbs/default",
        "#replace_cubemap",
    )
    return any(token in name for token in dummy_tokens)


def _is_usable_color_image(image) -> bool:
    if image is None or _is_dummy_image(image):
        return False
    name = _image_name(image)
    return not any(token in name for token in ("cubemap", "irradiance"))


def _uv_from_tex_node(node) -> str:
    visited = set()
    sockets = [node.inputs.get("Vector")] if node.inputs.get("Vector") else []
    while sockets:
        socket = sockets.pop(0)
        if socket is None:
            continue
        for link in socket.links:
            from_node = link.from_node
            if from_node in visited:
                continue
            visited.add(from_node)
            uv_map = getattr(from_node, "uv_map", None)
            if uv_map:
                return uv_map
            attr = getattr(from_node, "attribute_name", None)
            if attr:
                return attr
            for input_socket in from_node.inputs:
                if input_socket.is_linked:
                    sockets.append(input_socket)
    return ""


def _harvest_tex_nodes(material: Material):
    harvested = []
    if not material.node_tree:
        return harvested
    for node in material.node_tree.nodes:
        image = getattr(node, "image", None)
        if image is None:
            continue
        harvested.append({
            "name": str(node.name),
            "label": str(node.label or ""),
            "image": image,
            "uv_map": _uv_from_tex_node(node) or "",
        })
    return harvested


def _images_from_harvest(harvested) -> dict:
    found = {}
    for entry in harvested:
        image = entry["image"]
        found[entry["name"]] = image
        if entry["label"]:
            found[entry["label"]] = image
        name = entry["name"]
        if name.startswith("Texture") and name[7:].isdigit():
            found[f"slot:{name[7:]}"] = image
    return found


def _image_for_slot(sub_matl_data, node_images, number: int):
    slot_name = f"Texture{number}"
    candidates = []
    for texture in sub_matl_data.textures:
        if _slot_number(texture) == number or slot_name in _texture_slot_names(texture):
            if texture.image:
                candidates.append(texture.image)
            for name in _texture_slot_names(texture):
                if name in node_images:
                    candidates.append(node_images[name])
    for key in (slot_name, f"slot:{number}"):
        if key in node_images:
            candidates.append(node_images[key])
    return _first_real_image(*candidates)


def _first_real_image(*images):
    for image in images:
        if _is_usable_color_image(image):
            return image
    for image in images:
        if image is not None and not _is_dummy_image(image):
            return image
    return None


def _uv_for_slot(number: int) -> str:
    if number in (3, 9):
        return "bake1"
    if number in (1, 11, 14):
        return "uvSet"
    return "map1"


def _uv_for_image(harvested, image, slot: int) -> str:
    for entry in harvested:
        if entry["image"] is image and entry["uv_map"]:
            return entry["uv_map"]
    return _uv_for_slot(slot)


def _collect_maps(material: Material, sub_matl_data, emission_shader: bool):
    harvested = _harvest_tex_nodes(material)
    node_images = _images_from_harvest(harvested)
    extras = []
    for entry in harvested:
        image = entry["image"]
        if _is_usable_color_image(image) and image not in extras:
            extras.append(image)
    for texture in sub_matl_data.textures:
        if _is_usable_color_image(texture.image) and texture.image not in extras:
            extras.append(texture.image)

    # 0100 / ShaderFX color lives on Texture2, not Texture0. Do not prefer bake maps
    # as albedo on those shaders — bake1 is lighting, not the ring/emissive art.
    if emission_shader:
        albedo_order = (2, 5, 0, 10, 1, 11, 14)
    elif "bake" in (material.name or "").lower():
        albedo_order = (0, 10, 2, 5, 1, 11, 9, 3)
    else:
        albedo_order = (0, 10, 2, 5, 1, 11, 9, 3)

    albedo = None
    albedo_slot = 0
    for number in albedo_order:
        image = _image_for_slot(sub_matl_data, node_images, number)
        if _is_usable_color_image(image):
            albedo = image
            albedo_slot = number
            break
    if albedo is None:
        for entry in harvested:
            label = f"{entry['name']} {entry['label']}".lower()
            if _is_usable_color_image(entry["image"]) and any(
                token in label for token in ("emiss", "col", "texture2", "texture0", "texture5")
            ):
                albedo = entry["image"]
                break
    if albedo is None and extras:
        albedo = extras[0]

    emissive = _first_real_image(
        _image_for_slot(sub_matl_data, node_images, 5),
        _image_for_slot(sub_matl_data, node_images, 2),
        _image_for_slot(sub_matl_data, node_images, 14),
        albedo if emission_shader else None,
    )

    return {
        "albedo": albedo,
        "albedo_slot": albedo_slot,
        "albedo_uv": _uv_for_image(harvested, albedo, albedo_slot),
        "normal": _first_real_image(_image_for_slot(sub_matl_data, node_images, 4)),
        "prm": _first_real_image(_image_for_slot(sub_matl_data, node_images, 6)),
        "emissive": emissive,
    }


def _vector_by_name(sub_matl_data, *names):
    wanted = set(names)
    for vector in sub_matl_data.vectors:
        candidates = {
            str(getattr(vector, "param_id_name", "") or ""),
            str(getattr(vector, "name", "") or ""),
        }
        if wanted.intersection(candidates):
            return list(vector.value)
    return None


def _is_emission_shader(sub_matl_data, material: Material) -> bool:
    label = (getattr(sub_matl_data, "shader_label", "") or "").lower()
    name = (material.name or "").lower()
    # SFX_PBS_<hex> — shadeless emissive shaders end in 0100, not the leading 0100...
    hex_id = ""
    marker = "sfx_pbs_"
    if marker in label:
        hex_id = label.split(marker, 1)[1]
        hex_id = hex_id.split("_", 1)[0]
    if hex_id.endswith("0100") or "emiss" in label:
        return True
    return any(token in name for token in ("emi", "crystal", "glow", "fx_"))


def _principled_input(node, *names):
    for name in names:
        if name in node.inputs:
            return node.inputs[name]
    return None


def _add_image_node(nodes, links, image, location, label, name, non_color=False, uv_map="map1"):
    tex_node = nodes.new("ShaderNodeTexImage")
    tex_node.image = image
    tex_node.location = location
    tex_node.label = label
    tex_node.name = name
    if image is not None and non_color:
        try:
            image.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
    uv_node = nodes.new("ShaderNodeUVMap")
    uv_node.location = (location[0] - 200, location[1])
    uv_node.uv_map = uv_map
    uv_node.label = f"UV ({uv_map})"
    links.new(uv_node.outputs["UV"], tex_node.inputs["Vector"])
    return tex_node


def _new_mix_rgb(nodes, location, label="Mix"):
    try:
        mix_node = nodes.new("ShaderNodeMix")
        mix_node.data_type = "RGBA"
        mix_node.blend_type = "MULTIPLY"
        mix_node.location = location
        mix_node.label = label
        color1 = mix_node.inputs.get("A") or mix_node.inputs.get("Color1")
        color2 = mix_node.inputs.get("B") or mix_node.inputs.get("Color2")
        factor = mix_node.inputs.get("Factor") or mix_node.inputs.get("Fac")
        output = mix_node.outputs.get("Result") or mix_node.outputs.get("Color")
        return mix_node, factor, color1, color2, output
    except Exception:
        mix_node = nodes.new("ShaderNodeMixRGB")
        mix_node.blend_type = "MULTIPLY"
        mix_node.location = location
        mix_node.label = label
        return mix_node, mix_node.inputs["Fac"], mix_node.inputs["Color1"], mix_node.inputs["Color2"], mix_node.outputs["Color"]


def _is_fake_sss_826b(sub_matl_data) -> bool:
    label = (getattr(sub_matl_data, "shader_label", "") or "").lower()
    return "826b" in label


def _add_invert_color_node(nodes, location, label="Invert Color"):
    try:
        invert = nodes.new("ShaderNodeInvertColor")
    except Exception:
        invert = nodes.new("ShaderNodeInvert")
    invert.location = location
    invert.label = label
    invert.name = "PRM_Red_Invert"
    factor = invert.inputs.get("Factor") or invert.inputs.get("Fac")
    if factor is not None:
        factor.default_value = 1.0
    color_in = invert.inputs.get("Color") or invert.inputs[1]
    color_out = invert.outputs.get("Color") or invert.outputs[0]
    return invert, color_in, color_out


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
    
    sub_matl_data = material.sub_matl_data
    emission_shader = _is_emission_shader(sub_matl_data, material)
    maps = _collect_maps(material, sub_matl_data, emission_shader)
    texture0 = maps["albedo"]
    texture4 = maps["normal"]
    texture6 = maps["prm"]
    cv3 = _vector_by_name(sub_matl_data, "CustomVector3")
    cv8 = _vector_by_name(sub_matl_data, "CustomVector8")
    cv0 = _vector_by_name(sub_matl_data, "CustomVector0")
    
    # Clear existing nodes to start fresh
    material.node_tree.nodes.clear()
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    
    # Create the Principled BSDF node
    principled_node = nodes.new('ShaderNodeBsdfPrincipled')
    principled_node.location = (300, 0)
    principled_node.label = "Converted from Smash Material"
    principled_node.name = "Principled_BSDF"
    
    output = nodes.new('ShaderNodeOutputMaterial')
    output.location = (600, 0)
    output.label = 'Material Output'
    try:
        output.target = 'ALL'
    except Exception:
        pass
    bsdf_out = principled_node.outputs.get('BSDF') or principled_node.outputs[0]
    links.new(bsdf_out, output.inputs[0])
    
    base_color_in = _principled_input(principled_node, 'Base Color')
    emission_color_in = _principled_input(principled_node, 'Emission Color', 'Emission')
    emission_strength_in = _principled_input(principled_node, 'Emission Strength')
    color_socket = None

    # Set up diffuse/albedo / emission texture. Stage FX 0100 shaders use Texture2.
    if texture0:
        slot = maps["albedo_slot"]
        uv_map = maps["albedo_uv"] or _uv_for_slot(slot)
        label = f"Emission (Texture{slot})" if emission_shader else f"Diffuse/Albedo (Texture{slot})"
        diffuse_tex_node = _add_image_node(
            nodes, links, texture0, (-600, 300), label, "COL_Texture", uv_map=uv_map
        )
        color_socket = diffuse_tex_node.outputs['Color']
        if base_color_in is not None:
            links.new(color_socket, base_color_in)
        operator.report({'INFO'}, f"Connected texture: {texture0.name} (Texture{slot}, UV {uv_map})")
    else:
        fallback = cv3 or cv0 or cv8
        if fallback and base_color_in is not None:
            rgb = nodes.new('ShaderNodeRGB')
            rgb.location = (-300, 300)
            rgb.outputs[0].default_value = (float(fallback[0]), float(fallback[1]), float(fallback[2]), 1.0)
            links.new(rgb.outputs[0], base_color_in)
            color_socket = rgb.outputs[0]
            operator.report({'WARNING'}, "No Smash color/emissive texture found; used a CustomVector color")

    if emission_shader:
        if emission_strength_in is not None:
            emission_strength_in.default_value = 1.0
        if color_socket is not None and emission_color_in is not None:
            tint = cv8 or cv3
            if tint and any(abs(float(v) - 1.0) > 0.001 for v in tint[:3]):
                _mix, factor, color1, color2, mix_out = _new_mix_rgb(nodes, (0, 400), "Emission Tint")
                if factor is not None:
                    factor.default_value = 1.0
                links.new(color_socket, color1)
                color2.default_value = (float(tint[0]), float(tint[1]), float(tint[2]), 1.0)
                links.new(mix_out, emission_color_in)
            else:
                links.new(color_socket, emission_color_in)
        elif cv3 and emission_color_in is not None:
            emission_color_in.default_value = (float(cv3[0]), float(cv3[1]), float(cv3[2]), 1.0)
        metallic_in = _principled_input(principled_node, 'Metallic')
        roughness_in = _principled_input(principled_node, 'Roughness')
        if metallic_in is not None:
            metallic_in.default_value = 0.0
        if roughness_in is not None:
            roughness_in.default_value = 1.0
        operator.report({'INFO'}, "Connected as an emission / shadeless Smash shader")
    
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
        metallic_in = principled_node.inputs['Metallic']
        if _is_fake_sss_826b(sub_matl_data):
            _invert, invert_in, invert_out = _add_invert_color_node(nodes, (0, -80))
            links.new(prm_components['metalness'], invert_in)
            links.new(invert_out, metallic_in)
            operator.report({'INFO'}, "Inverted PRM red into Metallic for fake SSS (826b)")
        else:
            links.new(prm_components['metalness'], metallic_in)
        links.new(prm_components['roughness'], principled_node.inputs['Roughness'])
        
        # For ambient occlusion, we'll need to create a mix node to multiply with base color
        if texture0 and base_color_in is not None:
            _mix, factor, color1, color2, mix_out = _new_mix_rgb(nodes, (0, 200), "AO Mix")
            if factor is not None:
                factor.default_value = 1.0

            base_color_from_socket = None
            for link in list(base_color_in.links):
                base_color_from_socket = link.from_socket
                links.remove(link)
                break

            if base_color_from_socket is not None:
                links.new(base_color_from_socket, color1)
                color2.default_value = (1.0, 1.0, 1.0, 1.0)
                links.new(prm_components['ambient_occlusion'], factor)
                links.new(mix_out, base_color_in)
        
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
        names = _texture_slot_names(texture)
        if ("Texture6" in names or getattr(texture, "texture_number", None) == 6) and texture.image:
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


def find_target_armature(context):
    obj = getattr(context, "object", None)
    if obj is not None and obj.type == "ARMATURE":
        return obj
    for selected in getattr(context, "selected_objects", []) or []:
        if selected.type == "ARMATURE":
            return selected
    if obj is not None and obj.type == "MESH":
        armature = obj.find_armature()
        if armature is not None:
            return armature
    return None


def iter_armature_meshes(armature):
    seen = set()
    if armature is None:
        return
    children = getattr(armature, "children_recursive", None)
    if children is None:
        children = armature.children
    for obj in children:
        if obj.type == "MESH" and obj.name not in seen:
            seen.add(obj.name)
            yield obj
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.name in seen:
            continue
        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.object == armature:
                seen.add(obj.name)
                yield obj
                break


def smash_materials_on_armature(armature):
    materials = []
    seen = set()
    for mesh in iter_armature_meshes(armature):
        for slot in mesh.material_slots:
            material = slot.material
            if material is None or material.name in seen:
                continue
            if not has_smash_material_data(material):
                continue
            seen.add(material.name)
            materials.append(material)
    return materials


def armature_has_unconverted_smash_materials(armature) -> bool:
    return any(not is_converted_to_principled(material) for material in smash_materials_on_armature(armature))


def armature_has_converted_smash_materials(armature) -> bool:
    materials = smash_materials_on_armature(armature)
    return bool(materials) and all(is_converted_to_principled(material) for material in materials)


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
