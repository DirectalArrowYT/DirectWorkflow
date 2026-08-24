import bpy

# This preset contains physical property values for swing bones
# Apply this to any active swing bone to use these values

def apply_preset(swing_bone):
    swing_bone.air_resistance = 0.10
    swing_bone.water_resistance = 10.00
    swing_bone.angle_z = (-0.2094, 0.2094)  # -12°, 12°
    swing_bone.angle_y = (-0.2094, 0.2094)  # -12°, 12°
    swing_bone.collision_size = (0.1, 0.1)
    swing_bone.friction_rate = 0.10
    swing_bone.goal_strength = 300.00
    swing_bone.inertial_mass = 100.00
    swing_bone.local_gravity = -0.00
    swing_bone.fall_speed_scale = 400.00
    swing_bone.ground_hit = True
    swing_bone.wind_affect = 0.15

# Get the active swing bone when preset is loaded
swing_data = bpy.context.object.data.sub_swing_data
active_chain = swing_data.swing_bone_chains[swing_data.active_swing_bone_chain_index]
active_bone = active_chain.swing_bones[active_chain.active_swing_bone_index]
apply_preset(active_bone)

# Swing Bone Chain
swing_data.active_swing_bone_chain_index = 0
active_chain = swing_data.swing_bone_chains[0]
active_chain.is_skirt = False
active_chain.rotate_order = 0
active_chain.curve_rotate_x = False
active_chain.has_unk_8 = False

# Swing Bone: First bone in chain
swing_bone = active_chain.swing_bones[0]
swing_bone.air_resistance = 0.10
swing_bone.water_resistance = 10.00
swing_bone.angle_z = (-0.2094, 0.2094)  # -12°, 12°
swing_bone.angle_y = (-0.2094, 0.2094)  # -12°, 12°
swing_bone.collision_size = (0.1, 0.1)
swing_bone.friction_rate = 0.10
swing_bone.goal_strength = 300.00
swing_bone.inertial_mass = 100.00
swing_bone.local_gravity = -0.00
swing_bone.fall_speed_scale = 400.00
swing_bone.ground_hit = True
swing_bone.wind_affect = 0.15 