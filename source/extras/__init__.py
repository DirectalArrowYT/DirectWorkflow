from . import attribute_renamer
from . import create_meshes
from . import eye_material_custom_vector_31_modal
from . import face_picker
from . import misc_panel
from . import set_linear_vertex_color
from . import create_ik_arms
from . import create_ik_armsandlegs
from . import create_ik_legs
from . import bone_removal
from . import apply_ik_animation
from . import hip_animation_transfer
from . import idle_pose_library
from . import ik_influence_toggle
from . import ik_fk_switch
from . import ik_pole_alignment
from . import limit_weights
from . import unstack_uvs
from . import smart_hair_seams
from . import protect_datablocks
from . import rename_utils
from . import fk_to_ik
from . import user_poses

# Import reset_animation module and ensure it's properly registered
from . import reset_animation
from . import mirror_animation
from . import roll_copy
from . import bone_symmetry
from . import eye_rig
from . import rig_helper
from . import stage_tools

# Import animation_scroll module
from . import animation_scroll
from . import vis_mesh_bake

# Explicit registration function for the package
def register():
    # Register reset_animation first to ensure it's available for the panel
    reset_animation.register()
    
    # Register mirror_animation
    mirror_animation.register()

    roll_copy.register()
    bone_symmetry.register()
    eye_rig.register()
    stage_tools.register()
    
    # Register rename_utils
    rename_utils.register()
    
    # Register animation_scroll
    animation_scroll.register()
    vis_mesh_bake.register()
    face_picker.register()
    
    # Register misc_panel first since IK panel depends on it
    misc_panel.register()
    # Ensure user_poses module is imported so class references are available
    # (classes are registered via new_classes_to_register)
    
    # Register FK/IK modules
    fk_to_ik.register()
    create_ik_arms.register()
    create_ik_legs.register()
    create_ik_armsandlegs.register()
    ik_fk_switch.register()
    ik_pole_alignment.register()
    
def unregister():
    face_picker.unregister()
    vis_mesh_bake.unregister()
    # Unregister animation_scroll
    animation_scroll.unregister()
    
    # Unregister rename_utils
    rename_utils.unregister()
    
    # Unregister reset_animation
    reset_animation.unregister()
    
    roll_copy.unregister()
    bone_symmetry.unregister()
    eye_rig.unregister()
    stage_tools.unregister()

    # Unregister mirror_animation
    mirror_animation.unregister()
    
    # Unregister FK/IK modules (reverse order)
    ik_pole_alignment.unregister()
    create_ik_armsandlegs.unregister()
    create_ik_legs.unregister()
    create_ik_arms.unregister()
    ik_fk_switch.unregister()
    fk_to_ik.unregister()
    
    # Unregister misc_panel last
    misc_panel.unregister()