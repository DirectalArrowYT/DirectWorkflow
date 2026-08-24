import bpy

# This preset contains chain settings and all bone values for a swing bone chain
# Apply this to any active swing bone chain to use these values

# Chain configuration
CHAIN_CONFIG = {
    "is_skirt": False,
    "rotate_order": 0,
    "curve_rotate_x": False,
    "has_unk_8": False,
    "unk_8": 0
}

# Bone values for each bone in the chain
BONE_VALUES = [
    {
        "air_resistance": 0.10,
        "water_resistance": 10.00,
        "angle_z": (-0.2094, 0.2094),  # -12°, 12°
        "angle_y": (-0.2094, 0.2094),  # -12°, 12°
        "collision_size": (0.1, 0.1),
        "friction_rate": 0.10,
        "goal_strength": 300.00,
        "inertial_mass": 100.00,
        "local_gravity": -0.00,
        "fall_speed_scale": 400.00,
        "ground_hit": True,
        "wind_affect": 0.15
    },
    {
        "air_resistance": 0.12,
        "water_resistance": 12.00,
        "angle_z": (-0.2618, 0.2618),  # -15°, 15°
        "angle_y": (-0.2618, 0.2618),  # -15°, 15°
        "collision_size": (0.08, 0.08),
        "friction_rate": 0.12,
        "goal_strength": 250.00,
        "inertial_mass": 80.00,
        "local_gravity": -0.00,
        "fall_speed_scale": 350.00,
        "ground_hit": True,
        "wind_affect": 0.18
    },
    {
        "air_resistance": 0.15,
        "water_resistance": 15.00,
        "angle_z": (-0.3491, 0.3491),  # -20°, 20°
        "angle_y": (-0.3491, 0.3491),  # -20°, 20°
        "collision_size": (0.06, 0.06),
        "friction_rate": 0.15,
        "goal_strength": 200.00,
        "inertial_mass": 60.00,
        "local_gravity": -0.00,
        "fall_speed_scale": 300.00,
        "ground_hit": True,
        "wind_affect": 0.20
    },
    {
        "air_resistance": 0.18,
        "water_resistance": 18.00,
        "angle_z": (-0.4363, 0.4363),  # -25°, 25°
        "angle_y": (-0.4363, 0.4363),  # -25°, 25°
        "collision_size": (0.04, 0.04),
        "friction_rate": 0.18,
        "goal_strength": 150.00,
        "inertial_mass": 40.00,
        "local_gravity": -0.00,
        "fall_speed_scale": 250.00,
        "ground_hit": True,
        "wind_affect": 0.25
    }
]

def apply_chain_config(chain):
    """Apply chain configuration to a swing bone chain"""
    chain.is_skirt = CHAIN_CONFIG["is_skirt"]
    chain.rotate_order = CHAIN_CONFIG["rotate_order"]
    chain.curve_rotate_x = CHAIN_CONFIG["curve_rotate_x"]
    chain.has_unk_8 = CHAIN_CONFIG["has_unk_8"]
    chain.unk_8 = CHAIN_CONFIG["unk_8"]

def apply_bone_values(bone, values):
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

def apply_chain_preset():
    """Apply the chain preset to the active swing bone chain"""
    swing_data = bpy.context.object.data.sub_swing_data
    active_chain = swing_data.swing_bone_chains[swing_data.active_swing_bone_chain_index]
    
    # Apply chain configuration
    apply_chain_config(active_chain)
    
    # Check bone count and handle mismatches
    preset_bone_count = len(BONE_VALUES)
    target_bone_count = len(active_chain.swing_bones)
    
    if preset_bone_count == target_bone_count:
        # Perfect match - apply directly
        for i, bone_values in enumerate(BONE_VALUES):
            apply_bone_values(active_chain.swing_bones[i], bone_values)
    else:
        # Bone count mismatch - trigger dialog
        bpy.ops.sub.swing_chain_preset_apply_mismatch(
            "INVOKE_DEFAULT",
            preset_name="default_chain"
        )

# Note: This function is available for manual execution but is not called automatically 