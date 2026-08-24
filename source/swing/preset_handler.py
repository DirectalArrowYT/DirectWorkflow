import os
import importlib.util
import shutil

import bpy
from .sub_swing_data import SUB_PG_sub_swing_data

PRESETS_SUBDIR = os.path.join("swing", "bone_values")
CHAIN_PRESETS_SUBDIR = os.path.join("swing", "chain_values")

def get_swing_presets_dir():
    presets_dir = bpy.utils.user_resource('SCRIPTS', path="presets")
    swing_presets_dir = os.path.join(presets_dir, PRESETS_SUBDIR)
    
    return swing_presets_dir

def get_swing_chain_presets_dir():
    presets_dir = bpy.utils.user_resource('SCRIPTS', path="presets")
    swing_chain_presets_dir = os.path.join(presets_dir, CHAIN_PRESETS_SUBDIR)
    
    return swing_chain_presets_dir

def install_presets():
    """Install bundled presets if they exist"""
    swing_presets_dir = get_swing_presets_dir()
    bundled_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "presets")
    
    # Create the presets directory if it doesn't exist
    os.makedirs(swing_presets_dir, exist_ok=True)
    
    # Copy any bundled presets (if they exist)
    if os.path.exists(bundled_dir):
        for f in os.listdir(bundled_dir):
            if f.endswith('.py') and not f.startswith('default_chain'):
                shutil.copy2(os.path.join(bundled_dir, f), swing_presets_dir)
    
    # Create chain presets directory
    swing_chain_presets_dir = get_swing_chain_presets_dir()
    os.makedirs(swing_chain_presets_dir, exist_ok=True)
    
    # Copy chain presets
    if os.path.exists(bundled_dir):
        for f in os.listdir(bundled_dir):
            if f.endswith('.py') and f.startswith('default_chain'):
                shutil.copy2(os.path.join(bundled_dir, f), swing_chain_presets_dir)

def get_preset_items(self, context):
    """Get preset items for EnumProperty"""
    enum_items = []
    
    # Make sure the directory exists
    presets_dir = get_swing_presets_dir()
    os.makedirs(presets_dir, exist_ok=True)
    
    # Add None entry - shorter text to match image
    enum_items.append(('--', "-- Bone Values Presets --", "Select a preset"))
    
    # Get all presets
    for f in sorted(os.listdir(presets_dir)):
        if f.endswith('.py'):
            name = os.path.splitext(f)[0]
            enum_items.append((name, name.title().replace('_', ' '), f"Apply the {name} preset"))
    
    return enum_items

def get_chain_preset_items(self, context):
    """Get chain preset items for EnumProperty"""
    enum_items = []
    
    # Make sure the directory exists
    presets_dir = get_swing_chain_presets_dir()
    os.makedirs(presets_dir, exist_ok=True)
    
    # Add None entry
    enum_items.append(('--', "-- Chain Presets --", "Select a chain preset"))
    
    # Get all chain presets
    for f in sorted(os.listdir(presets_dir)):
        if f.endswith('.py'):
            name = os.path.splitext(f)[0]
            enum_items.append((name, name.title().replace('_', ' '), f"Apply the {name} chain preset"))
    
    return enum_items

def update_enum_items(context):
    """Force an update of the enum items by changing and restoring a property"""
    preset = context.scene.sub_swing_preset
    # This triggers a refresh of the enum items
    context.scene.sub_swing_preset = '--'
    if preset in [item[0] for item in get_preset_items(None, context)]:
        context.scene.sub_swing_preset = preset

def update_chain_enum_items(context):
    """Force an update of the chain enum items by changing and restoring a property"""
    preset = context.scene.sub_swing_chain_preset
    # This triggers a refresh of the enum items
    context.scene.sub_swing_chain_preset = '--'
    if preset in [item[0] for item in get_chain_preset_items(None, context)]:
        context.scene.sub_swing_chain_preset = preset

def register():
    """Register properties"""
    bpy.types.Scene.sub_swing_preset = bpy.props.EnumProperty(
        name="Bone Values Presets",
        description="Presets for swing bone physics values",
        items=get_preset_items
    )
    
    bpy.types.Scene.sub_swing_chain_preset = bpy.props.EnumProperty(
        name="Chain Presets",
        description="Presets for entire swing bone chains",
        items=get_chain_preset_items
    )
    
    # Properties for handling mapping requests from dialog
    bpy.types.Scene.sub_swing_auto_map_request = bpy.props.BoolProperty(
        name="Auto Map Request",
        description="Flag for auto-mapping request",
        default=False
    )
    
    bpy.types.Scene.sub_swing_clear_map_request = bpy.props.BoolProperty(
        name="Clear Map Request",
        description="Flag for clear-mapping request",
        default=False
    )
    
    # Properties for handling individual mapping updates
    bpy.types.Scene.sub_swing_mapping_index = bpy.props.IntProperty(
        name="Mapping Index",
        description="Index of mapping to update",
        default=-1
    )
    
    bpy.types.Scene.sub_swing_mapping_target = bpy.props.IntProperty(
        name="Mapping Target",
        description="Target index for mapping update",
        default=-1
    )
    
    bpy.types.Scene.sub_swing_mapping_update = bpy.props.BoolProperty(
        name="Mapping Update Request",
        description="Flag for mapping update request",
        default=False
    )

def unregister():
    """Unregister properties"""
    if hasattr(bpy.types.Scene, "sub_swing_preset"):
        del bpy.types.Scene.sub_swing_preset
    
    if hasattr(bpy.types.Scene, "sub_swing_chain_preset"):
        del bpy.types.Scene.sub_swing_chain_preset
    
    if hasattr(bpy.types.Scene, "sub_swing_auto_map_request"):
        del bpy.types.Scene.sub_swing_auto_map_request
    
    if hasattr(bpy.types.Scene, "sub_swing_clear_map_request"):
        del bpy.types.Scene.sub_swing_clear_map_request
    
    if hasattr(bpy.types.Scene, "sub_swing_mapping_index"):
        del bpy.types.Scene.sub_swing_mapping_index
    
    if hasattr(bpy.types.Scene, "sub_swing_mapping_target"):
        del bpy.types.Scene.sub_swing_mapping_target
    
    if hasattr(bpy.types.Scene, "sub_swing_mapping_update"):
        del bpy.types.Scene.sub_swing_mapping_update

def iterate_presets(scene, context):
    """Callback for EnumProperty to list available presets"""
    swing_presets_dir = get_swing_presets_dir()
    
    # Make sure directory exists
    os.makedirs(swing_presets_dir, exist_ok=True)
    
    # Empty entry
    yield '--', "--", "None"
    
    # List all Python presets
    for f in os.listdir(swing_presets_dir):
        if f.endswith('.py'):
            yield f, os.path.splitext(f)[0].title(), ""

def set_preset(preset, context):
    """Apply a preset to the active swing bone"""
    if not preset or preset == '--':
        return False
    
    if not preset.endswith(".py"):
        preset += ".py"
    
    preset_path = os.path.join(get_swing_presets_dir(), preset)
    if not os.path.isfile(preset_path):
        return False
    
    # Execute the preset - it will apply values to the active swing bone
    spec = importlib.util.spec_from_file_location("swing_preset", preset_path)
    preset_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preset_mod)
    
    return True

def set_chain_preset(preset, context):
    """Apply a chain preset to the active swing bone chain"""
    if not preset or preset == '--':
        return False
    
    if not preset.endswith(".py"):
        preset += ".py"
    
    preset_path = os.path.join(get_swing_chain_presets_dir(), preset)
    if not os.path.isfile(preset_path):
        return False
    
    # Load preset data safely
    preset_data = get_chain_preset_data(preset.replace('.py', ''))
    if not preset_data:
        return False
    
    # Apply the preset data manually
    swing_data = context.object.data.sub_swing_data
    active_chain = swing_data.swing_bone_chains[swing_data.active_swing_bone_chain_index]
    
    # Apply chain configuration
    if 'chain_config' in preset_data:
        chain_config = preset_data['chain_config']
        active_chain.is_skirt = chain_config["is_skirt"]
        active_chain.rotate_order = chain_config["rotate_order"]
        active_chain.curve_rotate_x = chain_config["curve_rotate_x"]
        active_chain.has_unk_8 = chain_config["has_unk_8"]
        active_chain.unk_8 = chain_config["unk_8"]
    
    # Check bone count and handle mismatches
    preset_bone_count = len(preset_data['bone_values'])
    target_bone_count = len(active_chain.swing_bones)
    
    if preset_bone_count == target_bone_count:
        # Perfect match - apply directly
        for i, bone_values in enumerate(preset_data['bone_values']):
            apply_bone_values_to_bone(active_chain.swing_bones[i], bone_values)
        return True
    else:
        # Bone count mismatch - return False to trigger mismatch dialog
        return False

def apply_bone_values_to_bone(bone, values):
    """Apply bone values to a swing bone"""
    bone.air_resistance = values["air_resistance"]
    bone.water_resistance = values["water_resistance"]
    bone.angle_z = values["angle_z"]
    bone.angle_y = values["angle_y"]
    bone.collision_size = values["collision_size"]
    bone.friction_rate = values["friction_rate"]
    bone.goal_strength = values["goal_strength"]
    bone.inertial_mass = values["inertial_mass"]
    bone.local_gravity = values["local_gravity"]
    bone.fall_speed_scale = values["fall_speed_scale"]
    bone.ground_hit = values["ground_hit"]
    bone.wind_affect = values["wind_affect"]

def get_chain_preset_data(preset_name):
    """Load chain preset data without applying it"""
    if not preset_name or preset_name == '--':
        return None
    
    if not preset_name.endswith(".py"):
        preset_name += ".py"
    
    preset_path = os.path.join(get_swing_chain_presets_dir(), preset_name)
    if not os.path.isfile(preset_path):
        return None
    
    # Read the file content and extract data without executing
    try:
        with open(preset_path, 'r') as f:
            content = f.read()
        
        # Create a safe namespace for execution
        namespace = {}
        
        # More specific replacements to avoid breaking function definitions
        import re
        
        # Replace standalone function calls (not in function definitions)
        # This matches apply_chain_preset() when it's not preceded by 'def '
        safe_content = re.sub(r'(?<!def\s)apply_chain_preset\(\)', 'pass  # apply_chain_preset() call removed', content)
        
        # Handle bpy.ops calls while preserving indentation
        # This captures the indentation and replaces the call with properly indented pass
        safe_content = re.sub(r'^(\s*)bpy\.ops\.[^(]+\([^)]*\)', r'\1pass  # bpy.ops call removed', safe_content, flags=re.MULTILINE)
        
        # Execute the safe content
        exec(safe_content, namespace)
        
        # Return the preset data
        if 'CHAIN_CONFIG' in namespace and 'BONE_VALUES' in namespace:
            return {
                'chain_config': namespace['CHAIN_CONFIG'],
                'bone_values': namespace['BONE_VALUES']
            }
        else:
            print(f"Missing CHAIN_CONFIG or BONE_VALUES in preset {preset_name}")
            print(f"Available keys: {list(namespace.keys())}")
            return None
    
    except Exception as e:
        print(f"Error loading preset {preset_name}: {e}")
        import traceback
        traceback.print_exc()
        return None
    
    return None 