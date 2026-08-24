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


def poll_armature_object(_self, obj):
    return obj is not None and getattr(obj, 'type', None) == 'ARMATURE'


def register():
    from .extras.face_picker import SUB_PG_face_picker_data

    Armature.sub_anim_properties = PointerProperty(
        type=anim_data.SUB_PG_sub_anim_data
    )
    Armature.sub_face_picker = PointerProperty(
        type=SUB_PG_face_picker_data
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
    Object.sub_shpc = PointerProperty(
        type=SUB_PG_shpc_settings
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

class MirrorCustomBoneItem(PropertyGroup):
    name: StringProperty(
        name="Bone Name",
        description="Custom bone that is not part of a normal Smash Ultimate armature",
        default=""
    )
    include: BoolProperty(
        name="Mirror",
        description="Mirror this custom bone",
        default=False
    )


def _update_shpc_preview(self, context):
    obj = getattr(self, "id_data", None)
    if obj is None or not obj.get("sub_shpc_root"):
        return
    from .extras.stage_tools.shpcanim import refresh_shpc_preview_object
    refresh_shpc_preview_object(obj, context.scene.frame_current)


def _update_stage_light_preview(self, context):
    from .extras.stage_tools.light_nuanmb import apply_stage_light_preview
    apply_stage_light_preview(context)


class SUB_PG_shpc_settings(PropertyGroup):
    intensity: FloatProperty(
        name="Intensity",
        description="Scale every SH coefficient on preview and export",
        default=1.0,
        min=0.0,
        soft_max=8.0,
        max=50.0,
        update=_update_shpc_preview,
    )
    tint: FloatVectorProperty(
        name="Tint",
        description="Tint the ambient grid RGB channels",
        subtype="COLOR",
        size=3,
        min=0.0,
        soft_max=2.0,
        max=8.0,
        default=(1.0, 1.0, 1.0),
        update=_update_shpc_preview,
    )
    use_vertex_colors: BoolProperty(
        name="Export Painted Ambient",
        description="Replace L0 ambient from the Col vertex colors on export",
        default=False,
    )
    sync_scene_frame: BoolProperty(
        name="Sync to Scene Frame",
        description="Switch SHPC keyframes when the scene frame changes",
        default=True,
        update=_update_shpc_preview,
    )


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
    roll_copy_source: PointerProperty(
        name="Source",
        description="Armature to copy roll values from",
        type=Object,
        poll=poll_armature_object,
    )
    roll_copy_target: PointerProperty(
        name="Target",
        description="Armature whose matching bone rolls will be changed",
        type=Object,
        poll=poll_armature_object,
    )
    roll_copy_selected_only: BoolProperty(
        name="Selected Target Bones Only",
        description="Only copy rolls for bones currently selected on the target armature",
        default=False,
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
    mirror_custom_bones: CollectionProperty(
        name="Custom Mirror Bones",
        description="Extra bones that are not part of a normal Smash armature",
        type=MirrorCustomBoneItem
    )
    mirror_custom_bones_index: IntProperty(
        name="Custom Mirror Bones Index",
        default=0
    )
    mirror_custom_armature_name: StringProperty(
        name="Custom Mirror List Source Armature",
        description="Internal: armature the custom bone list was last scanned from",
        default=""
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
    stage_light_expanded: BoolProperty(
        name="Stage Lighting Expanded",
        description="Whether the Stage Lighting section is expanded",
        default=True,
    )
    stage_light_preview: EnumProperty(
        name="Viewport Preview",
        description="SSBH uses one directional light per mesh plus SH ambient, not every LightStg as a Blender sun",
        items=(
            ("AMBIENT", "Ambient Fill", "SH-like fill only. Closest to how SSBH lights a stage (baked maps + ambient)"),
            ("CHR", "Ambient + LightChr", "Fill plus LightChr. Smash fighter lighting"),
            ("STG0", "Ambient + LightStg0", "Fill plus LightStg0. Stage light set 0"),
            ("ALL", "All Lights (Debug)", "Every LightChr/LightStg sun contributes. Harsh and not how the game works"),
            ("NONE", "Gizmos Only", "Imported lights do not illuminate. Rotate them for export only"),
        ),
        default="CHR",
        update=_update_stage_light_preview,
    )
    stage_light_apply_ambient: BoolProperty(
        name="Apply Smash Ambient",
        description="Add an SH-like fill light and world so Smash EEVEE materials are not a black void",
        default=True,
        update=_update_stage_light_preview,
    )
    stage_shpc_expanded: BoolProperty(
        name="Ambient SH Expanded",
        description="Whether the Ambient SH section is expanded",
        default=True,
    )
    last_stage_light_dir: StringProperty(
        name="Last Stage Light Directory",
        description="Last folder used for light.nuanmb import or export",
        default="",
        subtype="DIR_PATH",
    )
    last_stage_shpc_dir: StringProperty(
        name="Last Stage SHPC Directory",
        description="Last folder used for shpcanim import or export",
        default="",
        subtype="DIR_PATH",
    )




