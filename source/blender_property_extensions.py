import bpy
import re
from bpy.types import Scene, Object, Armature, PropertyGroup, Camera, Material, Bone, Mesh, Collection
from bpy.props import IntProperty, StringProperty, EnumProperty, BoolProperty, FloatProperty, CollectionProperty, PointerProperty
from bpy.props import FloatVectorProperty

from .exo import magic_exo_skel

from .model import export_model

from .anim import anim_data, import_anim, export_anim
from .swing import sub_swing_data
from .model.material import sub_matl_data
from .model.skel import helper_bone_data

def register():
    Armature.sub_anim_properties = PointerProperty(
        type=anim_data.SUB_PG_sub_anim_data
    )
    Scene.sub_scene_properties = PointerProperty(
        type=SubSceneProperties
    )
    Armature.sub_helper_bone_data = PointerProperty(
        type=helper_bone_data.SubHelperBoneData
    )
    Material.sub_matl_data = PointerProperty(
        type=sub_matl_data.SUB_PG_sub_matl_data
    )
    Armature.sub_swing_data = PointerProperty(
        type=sub_swing_data.SUB_PG_sub_swing_data
    )
    Bone.sub_swing_blender_bone_data = PointerProperty(
        type=sub_swing_data.SUB_PG_blender_bone_data
    )
    Mesh.sub_swing_data_linked_mesh = PointerProperty(
        type=sub_swing_data.SUB_PG_sub_swing_data_linked_mesh
    )
    Collection.sub_swing_collection_props = PointerProperty(
        type=sub_swing_data.SUB_PG_sub_swing_master_collection_props
    )

class ModelImportFile(PropertyGroup):
    name: StringProperty()

class ModelImportItem(PropertyGroup):
    name: StringProperty()
    path: StringProperty()
    files: CollectionProperty(type=ModelImportFile)

class AnimationImportFile(PropertyGroup):
    name: StringProperty()
    path: StringProperty()

class IdlePoseItem(PropertyGroup):
    name: StringProperty(
        name="Pose Name",
        description="Name of the idle pose",
        default=""
    )
    data: StringProperty(
        name="Pose Data", 
        description="JSON string containing the pose data",
        default=""
    )
    
bpy.utils.register_class(ModelImportFile)
bpy.utils.register_class(ModelImportItem)
bpy.utils.register_class(AnimationImportFile)

class UserPoseItem(PropertyGroup):
    name: StringProperty(
        name="Pose Name",
        description="Name of the user pose",
        default=""
    )
    data: StringProperty(
        name="Pose Data",
        description="JSON string containing the saved transforms for selected bones",
        default=""
    )


class SubSceneProperties(PropertyGroup):
    model_import_folder_path: StringProperty(
        name="Model Import Folder Path",
        description="Path to the folder containing the model files",
        default=""
    )
    last_model_folder: StringProperty(
        name="Last Model Folder",
        description="Path to the last folder used for model import",
        default=""
    )
    model_import_numdlb_file_name: StringProperty(
        name="NUMDLB File Name",
        description="Name of the NUMDLB file",
        default=""
    )
    model_import_numshb_file_name: StringProperty(
        name="NUMSHB File Name",
        description="Name of the NUMSHB file",
        default=""
    )
    model_import_nusktb_file_name: StringProperty(
        name="NUSKTB File Name",
        description="Name of the NUSKTB file",
        default=""
    )
    model_import_numatb_file_name: StringProperty(
        name="NUMATB File Name",
        description="Name of the NUMATB file",
        default=""
    )
    model_import_nuhlpb_file_name: StringProperty(
        name="NUHLPB File Name",
        description="Name of the NUHLPB file",
        default=""
    )
    model_import_models: CollectionProperty(
        name="Model Import Models",
        description="List of found models",
        type=ModelImportItem
    )
    model_import_models_index: IntProperty(
        name="Model Import Models Index",
        default=0
    )
    model_export_arma: PointerProperty(
        name='Armature',
        description='Select the Armature',
        type=Object,
        poll=magic_exo_skel.poll_armatures,
        update=export_model.model_export_arma_update,
    )
    model_export_show_all_new_bones: BoolProperty(
        name='Show All',
        description='True if more than the first 5 bones should be displayed',
        default=False,
    )
    model_export_show_all_missing_bones: BoolProperty(
        name='Show All',
        description='True if more than the first 5 bones should be displayed',
        default=False,
    )
    last_swing_directory: StringProperty(
        name="Last Swing Directory",
        description="Path to the last directory used for importing or exporting swing files",
        default="",
        subtype='DIR_PATH'
    )
    vanilla_nusktb: StringProperty(
        name='Vanilla .NUSKTB file path',
        description='The path to the vanilla nusktb file',
        default='',
    )
    vanilla_update_prc: StringProperty(
        name='Vanilla update.prc file path',
        description='The path to the vanilla update.prc file',
        default='',
    )
    smash_armature: PointerProperty(
        name='Smash Armature',
        description='Select the Smash armature',
        type=Object,
        poll=magic_exo_skel.poll_armatures,
    )
    other_armature: PointerProperty(
        name='Other Armature',
        description='Select the Other armature',
        type=Object,
        poll=magic_exo_skel.poll_other_armatures,
    )
    bone_list: CollectionProperty(
        type=magic_exo_skel.BoneListItem
    )
    saved_bone_list: CollectionProperty(
        type=magic_exo_skel.BoneListItem
    )
    bone_list_index: IntProperty(
        name="Index for the exo bone list",
        default=0
    )
    pairable_bone_list: CollectionProperty(
        type=magic_exo_skel.PairableBoneListItem
    )
    armature_prefix: StringProperty(
        name="Prefix",
        description="The Prefix that will be added to the bones in the 'Other' armature. Must begin with H_ or else it wont work!",
        default="H_Exo_"
    )
    material_reimport_arma: PointerProperty(
        name='Armature',
        description='Select the Armature',
        type=Object,
        poll=magic_exo_skel.poll_armatures,  
    )
    material_reimport_folder: StringProperty(
        name='Material Reimport folder',
        description='The folder w/ .numatb & textures',
        default='',
    )
    material_reimport_numatb_path: StringProperty(
        name='Material Reimport .numatb',
        description='The selected .numatb',
        default='',
    )
    cv31_modal_last_mode: StringProperty(
        name='Last Eye Material CV31 Modal Operator Mode',
        description='the last used mode for this operator',
        default='LEFT',
    )
    cv31_modal_use_auto_keyframe: BoolProperty(
        name='Use Auto Keyframe',
        description='True if a keyframe should be automatically inserted on confirm',
        default=True,
    )
    cv31_modal_reset_on_mode_switch: BoolProperty(
        name='Reset on Mode Switch',
        description='If true, switching modes will "undo" the changes made while in that mode',
        default=False,
    )
    last_anim_import_dir: StringProperty(
        subtype="DIR_PATH",
        default=""
    )
    last_anim_export_dir: StringProperty(
        subtype="DIR_PATH",
        default=""
    )
    last_imported_model_path: StringProperty(
        name="Last Imported Model Path",
        description="Path to the last imported model",
        default=""
    )
    animation_import_folder_path: StringProperty(
        name="Animation Import Folder Path",
        description="Path to the folder containing animation files related to imported model",
        default=""
    )
    animation_import_files: CollectionProperty(
        name="Animation Import Files",
        description="List of found animations for imported model",
        type=AnimationImportFile
    )
    animation_import_files_index: IntProperty(
        name="Animation Import Files Index",
        default=0
    )
    action_export_list: CollectionProperty(
        name="Action Export List",
        description="List of actions for batch export",
        type=export_anim.SUB_PG_anim_action_item
    )
    action_export_list_index: IntProperty(
        name="Action Export List Index",
        default=0
    )
    
    # Idle Pose Library Properties
    idle_pose_list: CollectionProperty(
        name="Idle Pose List",
        description="List of stored idle poses",
        type=IdlePoseItem
    )
    idle_pose_list_index: IntProperty(
        name="Idle Pose List Index",
        default=0
    )
    idle_pose_include_trans: BoolProperty(
        name="Include Trans Bone",
        description="Include the Trans bone when applying idle pose",
        default=True
    )
    idle_pose_mirrored: BoolProperty(
        name="Mirrored",
        description="Mirror the pose using Y axis",
        default=False
    )
    idle_pose_180_rotate: BoolProperty(
        name="180 Rotate",
        description="Rotate hip bone 180 degrees on Y-axis after mirroring",
        default=False
    )
    idle_pose_library_expanded: BoolProperty(
        name="Idle Pose Library Expanded",
        description="Whether the Idle Pose Library section is expanded",
        default=False
    )
    user_poses_expanded: BoolProperty(
        name="User Poses Expanded",
        description="Whether the User Poses section is expanded",
        default=False
    )
    ik_tools_expanded: BoolProperty(
        name="IK Tools Expanded",
        description="Whether the IK Tools section is expanded",
        default=False
    )
    related_animations_expanded: BoolProperty(
        name="Related Animations Expanded",
        description="Whether the Related Animations section is expanded",
        default=False
    )
    batch_export_actions_expanded: BoolProperty(
        name="Batch Export Actions Expanded",
        description="Whether the Batch Export Actions section is expanded",
        default=False
    )
    import_options_expanded: BoolProperty(
        name="Import Options Expanded",
        description="Whether the Import Options section is expanded",
        default=False
    )
    model_tools_expanded: BoolProperty(
        name="Model Tools Expanded",
        description="Whether the Model Tools section is expanded",
        default=False
    )
    mirror_animation_expanded: BoolProperty(
        name="Mirror Animation Expanded",
        description="Whether the Mirror Animation section is expanded",
        default=False
    )
    mirror_space: EnumProperty(
        name="Mirror Space",
        description="Coordinate space to use for mirroring",
        default='LOCAL',
        items = (
            ('LOCAL', 'Local', "Mirror using local/bone coordinate space"),
            ('GLOBAL', 'Global', "Mirror using world/global coordinate space"),
        )
    )
    mirror_include_fingers: BoolProperty(
        name="Include Fingers",
        description="Include finger bones (FingerL11, FingerR23, etc.) in the mirroring process",
        default=True
    )
    anim_include_transform: BoolProperty(
        name="Include Transform",
        description="Include Transform Track",
        default=True
    )
    anim_include_material: BoolProperty(
        name="Include Material",
        description="Include Material Track",
        default=True
    )
    anim_include_visibility: BoolProperty(
        name="Include Visibility",
        description="Include Visibility Track",
        default=True
    )
    shape_keys_prefix: StringProperty(
        name="Shape Keys Prefix",
        description="Prefix to use when converting shape keys to meshes",
        default=""
    )

    # Transform override bone filtering (shared by exporters)
    anim_override_bone_list: CollectionProperty(
        name="Transform Override Bone List",
        description="Bones listed here are used to filter which bones receive transform overrides",
        type=export_anim.SUB_PG_bone_override_item
    )
    anim_override_bone_list_index: IntProperty(
        name="Transform Override Bone List Index",
        default=0
    )
    anim_override_use_exclude_list: BoolProperty(
        name="Use Exclude List",
        description="If enabled, overrides apply to all bones except those in the list. If disabled, overrides apply only to bones in the list.",
        default=True
    )
    anim_override_armature_name: StringProperty(
        name="Override List Source Armature",
        description="Internal: name of the armature the override bone list was last populated from",
        default=""
    )

    # Transient flag: set by presets to force-enable translation overrides for the next export
    anim_preset_force_override_translation: BoolProperty(
        name="Preset: Force Override Translation",
        description="Internal flag toggled by presets to enable translation override for next export",
        default=False
    )

    # User Pose Library Properties
    user_pose_list: CollectionProperty(
        name="User Pose List",
        description="List of user-created poses (selected bones at a specific frame)",
        type=UserPoseItem
    )
    user_pose_list_index: IntProperty(
        name="User Pose List Index",
        default=0
    )
    user_pose_apply_only_selected: BoolProperty(
        name="Apply to only selected bones",
        description="If enabled, applying a user pose will affect only currently selected pose bones",
        default=False
    )




