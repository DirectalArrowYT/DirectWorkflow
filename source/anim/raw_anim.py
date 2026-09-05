"""Sparse pose-bone animation export/import for animator round-trips."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import bpy

from ..blender_compat import assign_action, ensure_action_slot
from .fcurve_compat import find_fcurve, get_all_action_fcurves, new_fcurve, remove_fcurve

RAW_ANIM_FORMAT = "sub_raw_anim"
RAW_ANIM_VERSION = 1
RAW_ANIM_EXTENSION = ".rawanim"

_BONE_FCURVE_REGEX = re.compile(
    r'^pose\.bones\["([^"]+)"\]\.(location|rotation_quaternion|rotation_euler|scale)$'
)
_VIS_FCURVE_REGEX = re.compile(
    r'^sub_anim_properties\.vis_track_entries\[(\d+)\]\.value$'
)
_BLENDER_DUPLICATE_SUFFIX = re.compile(r"\.\d+$")


def normalize_anim_stem(name: str) -> str:
    """Remove export extensions and Blender duplicate suffixes like .001 from a name."""
    stripped = name
    while True:
        changed = False
        lower = stripped.lower()
        if lower.endswith(".nuanmb"):
            stripped = stripped[:-7]
            changed = True
        elif lower.endswith(RAW_ANIM_EXTENSION):
            stripped = stripped[: -len(RAW_ANIM_EXTENSION)]
            changed = True
        else:
            match = _BLENDER_DUPLICATE_SUFFIX.search(stripped)
            if match:
                stripped = stripped[: match.start()]
                changed = True
        if not changed:
            break
    return stripped


def is_fighter_motion_body_path(path_str: str) -> bool:
    """True when path ends with motion/body/<subdir> (Smash fighter anim layout)."""
    if not path_str:
        return False
    parts = [part.lower() for part in Path(path_str).parts]
    try:
        motion_idx = parts.index("motion")
    except ValueError:
        return False
    return motion_idx + 2 < len(parts) and parts[motion_idx + 1] == "body"


def motion_path_to_rawanims_path(path_str: str) -> str | None:
    """Map .../motion/body/... to .../rawanims/body/... when applicable."""
    if not is_fighter_motion_body_path(path_str):
        return None
    parts = list(Path(path_str).parts)
    for index, part in enumerate(parts):
        if part.lower() == "motion":
            parts[index] = "rawanims"
            return str(Path(*parts))
    return None


def rawanims_path_to_motion_path(path_str: str) -> str | None:
    """Map .../rawanims/body/... back to .../motion/body/... when applicable."""
    if not path_str:
        return None
    parts = [part.lower() for part in Path(path_str).parts]
    try:
        raw_idx = parts.index("rawanims")
    except ValueError:
        return None
    if raw_idx + 2 >= len(parts) or parts[raw_idx + 1] != "body":
        return None
    mapped = list(Path(path_str).parts)
    for index, part in enumerate(mapped):
        if part.lower() == "rawanims":
            mapped[index] = "motion"
            return str(Path(*mapped))
    return None


def should_auto_create_raw_anim_directory(directory: str) -> bool:
    """Only auto-create folders for fighter motion/rawanims body layouts."""
    return rawanims_path_to_motion_path(directory) is not None


def ensure_fighter_raw_anim_directory(directory: str) -> bool:
    """Create rawanims/body/<subdir> when motion/body/<subdir> exists but rawanims does not."""
    if not directory or not should_auto_create_raw_anim_directory(directory):
        return False
    motion_path = rawanims_path_to_motion_path(directory)
    if motion_path is None or not os.path.isdir(motion_path):
        return False
    if not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    return True


def get_anim_folder_path(ssp) -> str:
    if getattr(ssp, "animation_import_folder_path", ""):
        return ssp.animation_import_folder_path
    if ssp.last_anim_import_dir:
        return ssp.last_anim_import_dir
    if ssp.last_anim_export_dir:
        return ssp.last_anim_export_dir
    return ""


def get_suggested_raw_anim_directory(ssp) -> str | None:
    for candidate in (
        getattr(ssp, "animation_import_folder_path", ""),
        ssp.last_anim_import_dir,
        ssp.last_anim_export_dir,
    ):
        if not candidate:
            continue
        raw_dir = motion_path_to_rawanims_path(candidate)
        if raw_dir is not None:
            return raw_dir
    return None


def strip_rawanim_suffix(name: str) -> str:
    return normalize_anim_stem(name)


def ensure_rawanim_filename(filename: str) -> str:
    return normalize_anim_stem(filename) + RAW_ANIM_EXTENSION


def ensure_rawanim_filepath(filepath: str) -> str:
    directory, filename = os.path.split(filepath)
    filename = ensure_rawanim_filename(filename)
    if directory:
        return os.path.join(directory, filename)
    return filename


def resolve_raw_anim_export_path(source_filepath: str, ssp) -> str:
    """Pick a raw anim destination from a nuanmb path or known anim folders."""
    source_dir = os.path.dirname(source_filepath)
    raw_name = ensure_rawanim_filename(os.path.basename(source_filepath))

    for candidate in (source_dir, get_anim_folder_path(ssp)):
        if not candidate:
            continue
        raw_dir = motion_path_to_rawanims_path(candidate)
        if raw_dir is not None:
            return os.path.join(raw_dir, raw_name)

    return os.path.join(source_dir, raw_name)


def _serialize_keyframe(keyframe: bpy.types.Keyframe) -> dict:
    data = {
        "frame": float(keyframe.co[0]),
        "value": float(keyframe.co[1]),
        "interpolation": keyframe.interpolation,
    }
    if keyframe.interpolation == "BEZIER":
        data["handle_left"] = [float(keyframe.handle_left[0]), float(keyframe.handle_left[1])]
        data["handle_right"] = [float(keyframe.handle_right[0]), float(keyframe.handle_right[1])]
    return data


def _serialize_visibility_keyframe(keyframe: bpy.types.Keyframe) -> dict:
    return {
        "frame": float(keyframe.co[0]),
        "value": bool(round(keyframe.co[1])),
        "interpolation": keyframe.interpolation,
    }


def _get_sap_action(arma: bpy.types.Object, bone_action: bpy.types.Action) -> bpy.types.Action | None:
    expected_name = f"{arma.name} {bone_action.name} SAP Data"
    sap_action = bpy.data.actions.get(expected_name)
    if sap_action is not None:
        return sap_action
    animation_data = getattr(arma.data, "animation_data", None)
    data_action = getattr(animation_data, "action", None) if animation_data else None
    if data_action is not None and data_action.name.endswith(" SAP Data"):
        return data_action
    return None


def _export_visibility_tracks(
    arma: bpy.types.Object,
    sap_action: bpy.types.Action,
    frame_start: int,
    frame_end: int,
) -> list[dict]:
    sap = arma.data.sub_anim_properties
    visibility_tracks: list[dict] = []
    for fcurve in get_all_action_fcurves(sap_action):
        match = _VIS_FCURVE_REGEX.match(fcurve.data_path)
        if match is None:
            continue
        entry_index = int(match.group(1))
        if entry_index >= len(sap.vis_track_entries):
            continue
        keys = []
        for keyframe in fcurve.keyframe_points:
            frame = keyframe.co[0]
            if frame < frame_start or frame > frame_end:
                continue
            keys.append(_serialize_visibility_keyframe(keyframe))
        if not keys:
            continue
        visibility_tracks.append(
            {
                "name": sap.vis_track_entries[entry_index].name,
                "keys": keys,
            }
        )
    visibility_tracks.sort(key=lambda track: track["name"])
    return visibility_tracks


def _import_visibility_tracks(
    context: bpy.types.Context,
    arma: bpy.types.Object,
    action: bpy.types.Action,
    visibility_tracks: list[dict],
) -> tuple[bpy.types.Action | None, int]:
    if not visibility_tracks:
        return None, 0

    if arma.data.animation_data is None:
        arma.data.animation_data_create()

    sap_action_name = f"{arma.name} {action.name} SAP Data"
    sap_action = bpy.data.actions.get(sap_action_name)
    if sap_action is None:
        sap_action = bpy.data.actions.new(sap_action_name)
    else:
        for existing_fcurve in list(get_all_action_fcurves(sap_action)):
            if _VIS_FCURVE_REGEX.match(existing_fcurve.data_path):
                remove_fcurve(sap_action, existing_fcurve, id_type="ARMATURE")
    ensure_action_slot(sap_action, arma.data)

    sap = arma.data.sub_anim_properties
    for track_data in visibility_tracks:
        track_name = track_data.get("name", "")
        if not track_name:
            continue
        entry = sap.vis_track_entries.get(track_name)
        if entry is None:
            entry = sap.vis_track_entries.add()
            entry.name = track_name

    assign_action(arma.data.animation_data, sap_action)

    keyframe_count = 0
    for track_data in visibility_tracks:
        track_name = track_data.get("name", "")
        if not track_name:
            continue
        entry_index = sap.vis_track_entries.find(track_name)
        if entry_index < 0:
            continue
        data_path = f"sub_anim_properties.vis_track_entries[{entry_index}].value"
        vis_entry = sap.vis_track_entries[entry_index]
        for key_data in track_data.get("keys", []):
            vis_entry.value = bool(key_data.get("value", False))
            arma.data.keyframe_insert(
                data_path=data_path,
                frame=float(key_data["frame"]),
                group="Visibility",
            )
            keyframe_count += 1
        fcurve = find_fcurve(sap_action, data_path, id_type="ARMATURE")
        if fcurve is not None:
            key_data_by_frame = {
                float(key_data["frame"]): key_data for key_data in track_data.get("keys", [])
            }
            for keyframe in fcurve.keyframe_points:
                key_data = key_data_by_frame.get(float(keyframe.co[0]))
                keyframe.interpolation = (
                    key_data.get("interpolation", "CONSTANT") if key_data else "CONSTANT"
                )
            fcurve.update()

    from .import_anim import setup_visibility_drivers

    setup_visibility_drivers(arma)
    return sap_action, keyframe_count


def export_raw_animation(
    arma: bpy.types.Object,
    action: bpy.types.Action,
    filepath: str,
    frame_start: int,
    frame_end: int,
    operator: bpy.types.Operator | None = None,
) -> bool:
    if action is None:
        if operator is not None:
            operator.report({"ERROR"}, "No action to export.")
        return False

    fcurve_entries: list[dict] = []
    for fcurve in get_all_action_fcurves(action, id_type="OBJECT"):
        match = _BONE_FCURVE_REGEX.match(fcurve.data_path)
        if match is None:
            continue

        keys = []
        for keyframe in fcurve.keyframe_points:
            frame = keyframe.co[0]
            if frame < frame_start or frame > frame_end:
                continue
            keys.append(_serialize_keyframe(keyframe))

        if not keys:
            continue

        bone_name, transform_property = match.groups()
        fcurve_entries.append(
            {
                "bone": bone_name,
                "property": transform_property,
                "index": fcurve.array_index,
                "keys": keys,
            }
        )

    visibility_tracks: list[dict] = []
    sap_action = _get_sap_action(arma, action)
    if sap_action is not None:
        visibility_tracks = _export_visibility_tracks(arma, sap_action, frame_start, frame_end)

    if not fcurve_entries and not visibility_tracks:
        if operator is not None:
            operator.report({"ERROR"}, "No pose or visibility keyframes to export in the selected frame range.")
        return False

    payload = {
        "format": RAW_ANIM_FORMAT,
        "version": RAW_ANIM_VERSION,
        "armature_name": arma.name,
        "action_name": action.name,
        "frame_start": int(frame_start),
        "frame_end": int(frame_end),
        "fcurves": fcurve_entries,
        "visibility_tracks": visibility_tracks,
    }

    filepath = ensure_rawanim_filepath(filepath)
    directory = os.path.dirname(filepath)
    if directory:
        ensure_fighter_raw_anim_directory(directory)

    with open(filepath, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    return True


def get_raw_anim_import_directory(ssp, folder_path: str = "") -> str:
    if folder_path:
        return folder_path
    motion_path = getattr(ssp, "animation_import_folder_path", "")
    if is_fighter_motion_body_path(motion_path):
        mapped = motion_path_to_rawanims_path(motion_path)
        if mapped:
            return mapped
    return getattr(ssp, "raw_animation_import_folder_path", "")


def refresh_raw_animation_import_list(ssp, folder_path: str = "") -> str:
    resolved_folder = get_raw_anim_import_directory(ssp, folder_path)
    ssp.raw_animation_import_files.clear()
    ssp.raw_animation_import_folder_path = resolved_folder

    if resolved_folder and os.path.isdir(resolved_folder):
        for file_name in sorted(os.listdir(resolved_folder)):
            if not file_name.endswith(RAW_ANIM_EXTENSION):
                continue
            item = ssp.raw_animation_import_files.add()
            item.name = os.path.splitext(file_name)[0]
            item.path = os.path.join(resolved_folder, file_name)

    return resolved_folder


_scheduled_raw_refresh_scenes: set[str] = set()


def schedule_raw_animation_list_refresh(context: bpy.types.Context) -> None:
    """Refresh raw anim list outside panel draw (RNA writes are not allowed in draw)."""
    scene_name = context.scene.name
    if scene_name in _scheduled_raw_refresh_scenes:
        return
    _scheduled_raw_refresh_scenes.add(scene_name)

    def _refresh_once():
        _scheduled_raw_refresh_scenes.discard(scene_name)
        scene = bpy.data.scenes.get(scene_name)
        if scene is None:
            return None
        ssp = scene.sub_scene_properties
        motion_path = getattr(ssp, "animation_import_folder_path", "")
        if not is_fighter_motion_body_path(motion_path):
            return None
        expected_raw_folder = motion_path_to_rawanims_path(motion_path)
        if expected_raw_folder and ssp.raw_animation_import_folder_path != expected_raw_folder:
            refresh_raw_animation_import_list(ssp)
        window_manager = getattr(bpy.context, "window_manager", None)
        if window_manager is not None:
            for window in window_manager.windows:
                screen = window.screen
                if screen is None:
                    continue
                for area in screen.areas:
                    area.tag_redraw()
        return None

    bpy.app.timers.register(_refresh_once, first_interval=0.0)


_LEG_IK_BONE = re.compile(r"^(FootIK|KneeIK)[LR]$", re.IGNORECASE)
_ARM_IK_BONE = re.compile(r"^(HandIK|ArmIK)[LR]$", re.IGNORECASE)


def get_animated_ik_bones(fcurve_entries: list[dict]) -> set[str]:
    bones: set[str] = set()
    for entry in fcurve_entries:
        bone_name = entry.get("bone", "")
        if _LEG_IK_BONE.match(bone_name) or _ARM_IK_BONE.match(bone_name):
            bones.add(bone_name)
    return bones


def plan_ik_rig_setup(armature_data, fcurve_entries: list[dict]) -> str | None:
    """Return BOTH, LEGS, ARMS, or None when IK bones must be created for import."""
    animated_ik = get_animated_ik_bones(fcurve_entries)
    if not animated_ik:
        return None

    missing = {bone for bone in animated_ik if bone not in armature_data.bones}
    if not missing:
        return None

    anim_has_legs = any(_LEG_IK_BONE.match(bone) for bone in animated_ik)
    anim_has_arms = any(_ARM_IK_BONE.match(bone) for bone in animated_ik)
    missing_legs = any(_LEG_IK_BONE.match(bone) for bone in missing)
    missing_arms = any(_ARM_IK_BONE.match(bone) for bone in missing)

    need_legs = anim_has_legs and missing_legs
    need_arms = anim_has_arms and missing_arms

    if need_legs and need_arms:
        return "BOTH"
    if need_legs:
        return "LEGS"
    if need_arms:
        return "ARMS"
    return None


def ensure_ik_rig_for_raw_import(
    context: bpy.types.Context,
    arma: bpy.types.Object,
    setup_kind: str,
    operator: bpy.types.Operator | None = None,
) -> bool:
    if context.view_layer.objects.active != arma:
        for obj in context.view_layer.objects:
            obj.select_set(obj == arma)
        context.view_layer.objects.active = arma

    if context.mode != "POSE":
        bpy.ops.object.mode_set(mode="POSE", toggle=False)

    if setup_kind == "BOTH":
        result = bpy.ops.sub.create_ik_bones("EXEC_DEFAULT", match_position=False)
        label = "arms and legs"
    elif setup_kind == "LEGS":
        result = bpy.ops.sub.create_foot_ik("EXEC_DEFAULT", match_position=False)
        label = "legs"
    else:
        result = bpy.ops.sub.create_arm_ik("EXEC_DEFAULT", match_position=False)
        label = "arms"

    if result != {"FINISHED"}:
        if operator is not None:
            operator.report({"WARNING"}, f"Automatic {label} IK rig creation failed.")
        return False

    if operator is not None:
        operator.report({"INFO"}, f"Created {label} IK bones for raw animation import.")
    return True


def import_raw_animation(
    context: bpy.types.Context,
    arma: bpy.types.Object,
    filepath: str,
    operator: bpy.types.Operator | None = None,
) -> bool:
    with open(filepath, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    if data.get("format") != RAW_ANIM_FORMAT:
        if operator is not None:
            operator.report({"ERROR"}, "File is not a supported raw animation.")
        return False

    fcurve_entries = data.get("fcurves", [])
    visibility_tracks = data.get("visibility_tracks", [])
    if not fcurve_entries and not visibility_tracks:
        if operator is not None:
            operator.report({"ERROR"}, "Raw animation file contains no keyframe data.")
        return False

    if arma.animation_data is None:
        arma.animation_data_create()

    ik_setup = plan_ik_rig_setup(arma.data, fcurve_entries) if fcurve_entries else None
    if ik_setup and not ensure_ik_rig_for_raw_import(context, arma, ik_setup, operator):
        return False

    action_name = data.get("action_name") or Path(filepath).stem
    action = bpy.data.actions.get(action_name)
    if action is None:
        action = bpy.data.actions.new(name=action_name)
    elif fcurve_entries:
        for existing_fcurve in list(get_all_action_fcurves(action)):
            remove_fcurve(action, existing_fcurve, id_type="OBJECT")

    if fcurve_entries:
        ensure_action_slot(action, arma)
        assign_action(arma.animation_data, action)

    quaternion_bones: set[str] = set()
    keyframe_count = 0

    for fcurve_data in fcurve_entries:
        bone_name = fcurve_data["bone"]
        transform_property = fcurve_data["property"]
        array_index = int(fcurve_data["index"])
        data_path = f'pose.bones["{bone_name}"].{transform_property}'
        fcurve = new_fcurve(
            action,
            data_path,
            index=array_index,
            action_group=bone_name,
            id_type="OBJECT",
        )

        for key_data in fcurve_data.get("keys", []):
            keyframe = fcurve.keyframe_points.insert(float(key_data["frame"]), float(key_data["value"]))
            keyframe.interpolation = key_data.get("interpolation", "BEZIER")
            if keyframe.interpolation == "BEZIER":
                if "handle_left" in key_data:
                    keyframe.handle_left = key_data["handle_left"]
                if "handle_right" in key_data:
                    keyframe.handle_right = key_data["handle_right"]
            keyframe_count += 1

        fcurve.update()

        if transform_property == "rotation_quaternion":
            quaternion_bones.add(bone_name)

    for bone_name in quaternion_bones:
        pose_bone = arma.pose.bones.get(bone_name)
        if pose_bone is not None:
            pose_bone.rotation_mode = "QUATERNION"

    sap_action, vis_keyframe_count = _import_visibility_tracks(
        context,
        arma,
        action,
        visibility_tracks,
    )
    keyframe_count += vis_keyframe_count

    frame_start = int(data.get("frame_start", context.scene.frame_start))
    frame_end = int(data.get("frame_end", frame_start))
    context.scene.frame_start = frame_start
    context.scene.frame_end = frame_end
    context.scene.frame_current = frame_start

    if fcurve_entries:
        assign_action(arma.animation_data, action)
    if sap_action is not None:
        assign_action(arma.data.animation_data, sap_action)

    if fcurve_entries or sap_action is not None:
        from .anim_data import mark_sap_sync_known

        mark_sap_sync_known(arma)

    if ik_setup:
        from ..extras.fk_to_ik import run_fk_to_ik_match_for_raw_import

        match_result = run_fk_to_ik_match_for_raw_import(context, cleanup_mode=ik_setup)
        if match_result != {"FINISHED"} and operator is not None:
            operator.report({"WARNING"}, "IK to FK matching did not complete successfully.")

    if operator is not None:
        parts = [f"{len(fcurve_entries)} pose f-curves", f"{keyframe_count} keyframes"]
        if visibility_tracks:
            parts.append(f"{len(visibility_tracks)} visibility tracks")
        operator.report(
            {"INFO"},
            f"Imported raw animation '{action.name}' ({', '.join(parts)})",
        )

    return True
