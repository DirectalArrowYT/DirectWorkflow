"""Build temporary Smash ModelFolders so non-Smash meshes render in ssbh_wgpu.

ssbh_wgpu only draws .numshb folders. Jump Force / retarget sources have no
Smash files, so this writes a rest-pose mesh + skel with default fighter
materials (shared Smash lights, GPU skinning). Missing textures use the
engine defaults — same as an unfinished SSBH model.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
from mathutils import Matrix

from ...dependencies import ssbh_data_py
from ..model.export_model import default_ssbh_material, per_loop_to_per_vertex

BONE_SEP = "__vp__"
LOOSE_ARM = "_vp_loose"
_SAFE = re.compile(r"[^A-Za-z0-9_]+")
_Z_UP_TO_Y_UP = Matrix.Rotation(math.radians(-90.0), 4, "X")
_Y_MAJOR_TO_X_MAJOR = Matrix.Rotation(math.radians(90.0), 4, "Z")
_MAT_LABEL = "SmashVP_Default"
_MAX_BONES = 512
_MAX_SKIN_VERTS = 65535


def safe_token(name, limit=40):
    text = _SAFE.sub("_", name or "obj").strip("_") or "obj"
    if text[0].isdigit():
        text = "o_" + text
    return text[:limit]


def extra_bone_name(arm_name, bone_name):
    return f"{safe_token(arm_name)}{BONE_SEP}{bone_name}"


def extra_mesh_name(arm_name, obj_name):
    name = f"{safe_token(arm_name, 24)}_{safe_token(obj_name, 32)}"
    return name[:64]


def loose_bone_name(obj_name):
    return extra_bone_name(LOOSE_ARM, safe_token(obj_name, 48))


def extra_temp_dir():
    root = Path(tempfile.gettempdir()) / "smash_vp_extra"
    root.mkdir(parents=True, exist_ok=True)
    return root


def bone_prefix(arm_name):
    return f"{safe_token(arm_name)}{BONE_SEP}"


def prefix_smash_folder(src_folder, arm_name):
    """Copy a real Smash folder and namespace Hip/Trans so extra models do not share pose."""
    src = Path(src_folder)
    dest = extra_temp_dir() / f"{safe_token(arm_name, 40)}_numshb"
    stamp = dest / ".src"
    want = str(src.resolve()) if src.exists() else str(src)
    if dest.is_dir() and stamp.is_file():
        try:
            if stamp.read_text(encoding="utf-8") == want:
                return str(dest)
        except Exception:
            pass
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    rewrite = {".numshb", ".nusktb"}
    for item in src.iterdir():
        if not item.is_file():
            continue
        suffix = item.suffix.lower()
        if suffix == ".nuhlpb":
            continue
        target = dest / item.name
        if suffix in rewrite:
            shutil.copy2(item, target)
            continue
        try:
            os.link(item, target)
        except OSError:
            shutil.copy2(item, target)
    pfx = bone_prefix(arm_name)
    skel_path = dest / "model.nusktb"
    mesh_path = dest / "model.numshb"
    if skel_path.is_file():
        skel = ssbh_data_py.skel_data.read_skel(str(skel_path))
        for bone in skel.bones:
            name = getattr(bone, "name", "") or ""
            if name and not name.startswith(pfx):
                bone.name = pfx + name
        skel.save(str(skel_path))
    if mesh_path.is_file():
        mesh = ssbh_data_py.mesh_data.read_mesh(str(mesh_path))
        for obj in mesh.objects:
            parent = getattr(obj, "parent_bone_name", "") or ""
            if parent and not parent.startswith(pfx):
                obj.parent_bone_name = pfx + parent
            for inf in getattr(obj, "bone_influences", []) or []:
                bname = getattr(inf, "bone_name", None) or getattr(inf, "name", "") or ""
                if bname and not bname.startswith(pfx):
                    if hasattr(inf, "bone_name"):
                        inf.bone_name = pfx + bname
                    elif hasattr(inf, "name"):
                        inf.name = pfx + bname
        mesh.save(str(mesh_path))
    for helper in dest.glob("*.nuhlpb"):
        try:
            helper.unlink()
        except Exception:
            pass
    try:
        stamp.write_text(want, encoding="utf-8")
    except Exception:
        pass
    return str(dest)


def extra_gpu_matrix(blender_matrix, smash_bones=False):
    """Y-up like Smash. X-major only for Smash .numshb extras (Pokken/JF keep axes)."""
    matrix = _Z_UP_TO_Y_UP @ blender_matrix
    if smash_bones:
        matrix = matrix @ _Y_MAJOR_TO_X_MAJOR
    return matrix


def _ssbh_world(blender_matrix, smash_bones=False):
    return _ssbh_from_gpu(extra_gpu_matrix(blender_matrix, smash_bones=smash_bones))


def _ssbh_from_gpu(gpu_matrix):
    return np.array(gpu_matrix.transposed(), dtype=np.float32)


def _mesh_to_armature_rest(obj, arm):
    """Armature-space rest matrix. Never use posed matrix_world (that double-skins)."""
    if obj is None or arm is None:
        return Matrix.Identity(4)
    if getattr(obj, "parent", None) == arm:
        local = obj.matrix_local.copy()
        bone_name = getattr(obj, "parent_bone", "") or ""
        if bone_name:
            bone = arm.data.bones.get(bone_name)
            if bone is not None:
                return bone.matrix_local @ local
        return local
    # Modifier-only bind: object transform relative to the armature object.
    try:
        return arm.matrix_world.inverted() @ obj.matrix_world
    except Exception:
        return Matrix.Identity(4)


def _parent_first_bones(arm, keep_names=None):
    bones = [
        bone
        for bone in arm.data.bones
        if bone.name and not bone.name.startswith("BL_")
    ]
    if keep_names:
        bones = [bone for bone in bones if bone.name in keep_names]
    by_name = {bone.name: bone for bone in bones}
    ordered = []
    seen = set()

    def walk(bone):
        if bone.name in seen:
            return
        parent = bone.parent
        if parent is not None and parent.name in by_name and parent.name not in seen:
            walk(parent)
        seen.add(bone.name)
        ordered.append(bone)

    for bone in bones:
        walk(bone)
    return ordered


def _gpu_bone_names(arm, meshes):
    used = set()
    for obj in meshes or []:
        groups = getattr(obj, "vertex_groups", None)
        if not groups:
            continue
        for vg in groups:
            if vg.name:
                used.add(vg.name)
    by_name = {
        bone.name: bone
        for bone in arm.data.bones
        if bone.name and not bone.name.startswith("BL_")
    }
    keep = set()

    def add_chain(name):
        bone = by_name.get(name)
        while bone is not None and bone.name not in keep:
            keep.add(bone.name)
            bone = bone.parent

    if used:
        for name in used:
            add_chain(name)
        if keep:
            return keep
    for bone in by_name.values():
        if getattr(bone, "use_deform", True):
            keep.add(bone.name)
    return keep or set(by_name)


def _build_skel(arm, arm_name, smash_bones=False, meshes=None):
    """World rest in GPU space, no parent chain. Pose uploads the same space so
    ssbh_wgpu's world * rest_inv matches Blender LBS without Smash X-major."""
    skel = ssbh_data_py.skel_data.SkelData()
    bones = _parent_first_bones(arm, _gpu_bone_names(arm, meshes))[:_MAX_BONES]
    world = getattr(arm, "matrix_world", None)
    if world is None:
        world = Matrix.Identity(4)
    if not bones:
        rest = extra_gpu_matrix(world, smash_bones=smash_bones)
        skel.bones.append(
            ssbh_data_py.skel_data.BoneData(
                extra_bone_name(arm_name, "Root"), _ssbh_from_gpu(rest), None
            )
        )
        return skel, {"Root": extra_bone_name(arm_name, "Root")}

    name_map = {}
    for bone in bones:
        export = extra_bone_name(arm_name, bone.name)
        name_map[bone.name] = export
        rest = extra_gpu_matrix(world @ bone.matrix_local, smash_bones=smash_bones)
        skel.bones.append(ssbh_data_py.skel_data.BoneData(export, _ssbh_from_gpu(rest), None))
    return skel, name_map


def _loose_skel(entries):
    """entries: list of (obj, bone_name)."""
    skel = ssbh_data_py.skel_data.SkelData()
    rest = _ssbh_world(Matrix.Identity(4))
    for _obj, bone_name in entries:
        skel.bones.append(ssbh_data_py.skel_data.BoneData(bone_name, rest, None))
    if not skel.bones:
        skel.bones.append(
            ssbh_data_py.skel_data.BoneData(extra_bone_name(LOOSE_ARM, "Root"), rest, None)
        )
    return skel


def _albedo_image(obj):
    materials = getattr(obj.data, "materials", None) if obj is not None else None
    if not materials:
        return None
    mat = materials[0]
    if mat is None:
        return None
    try:
        if mat.use_nodes and mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.type != "BSDF_PRINCIPLED":
                    continue
                link = node.inputs["Base Color"].links
                if not link:
                    continue
                src = link[0].from_node
                image = getattr(src, "image", None)
                if image is not None:
                    return image
            for node in mat.node_tree.nodes:
                image = getattr(node, "image", None)
                if image is not None:
                    return image
    except Exception:
        return None
    return None


def _write_col_nutexb(image, folder, stem):
    if image is None:
        return None
    try:
        from ..model.material.texture.convert_nutexb_to_png import get_ultimate_tex_path
        from subprocess import run
    except Exception:
        return None
    try:
        cli = get_ultimate_tex_path()
        if not Path(cli).exists():
            return None
    except Exception:
        return None
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    png = folder / f"{stem}.png"
    nutexb = folder / f"{stem}.nutexb"
    try:
        image.file_format = "PNG"
        image.save(filepath=str(png))
    except Exception:
        try:
            image.save_render(filepath=str(png))
        except Exception:
            return None
    try:
        run(
            [str(cli), str(png), str(nutexb), "--format", "BC7Srgb"],
            capture_output=True,
            check=True,
        )
    except Exception:
        try:
            png.unlink()
        except Exception:
            pass
        return None
    try:
        png.unlink()
    except Exception:
        pass
    return stem


def _opaque_material(label, col_name=None):
    """SFX_PBS PRM opaque, same default as Smash export."""
    entry = default_ssbh_material(label)
    tex0 = ssbh_data_py.matl_data.ParamId.Texture0
    col = col_name or "/common/shader/sfxpbs/default_white"
    for tex in entry.textures:
        if tex.param_id == tex0:
            tex.data = col
    return entry


def _apply_matrix_points(points, matrix):
    if len(points) == 0:
        return points
    mat = np.array(matrix, dtype=np.float32)
    hom = np.ones((len(points), 4), dtype=np.float32)
    hom[:, :3] = points
    return (mat @ hom.T).T[:, :3]


def _apply_matrix_vectors(vectors, matrix):
    if len(vectors) == 0:
        return vectors
    rot = np.array(matrix.to_3x3(), dtype=np.float32)
    try:
        nrm = np.linalg.inv(rot).T
    except Exception:
        nrm = rot
    out = vectors @ nrm.T
    lens = np.linalg.norm(out, axis=1, keepdims=True)
    lens[lens < 1e-8] = 1.0
    return (out / lens).astype(np.float32, copy=False)


def _pack_mesh_object(name, subindex, positions, normals4, uvs, tangents, indices, influences, fallback_bone):
    mesh_obj = ssbh_data_py.mesh_data.MeshObjectData(name, subindex)
    pos_attr = ssbh_data_py.mesh_data.AttributeData("Position0")
    pos_attr.data = np.ascontiguousarray(positions, dtype=np.float32)
    mesh_obj.positions = [pos_attr]
    nrm_attr = ssbh_data_py.mesh_data.AttributeData("Normal0")
    nrm_attr.data = np.ascontiguousarray(normals4, dtype=np.float32)
    mesh_obj.normals = [nrm_attr]
    tan_attr = ssbh_data_py.mesh_data.AttributeData("Tangent0")
    tan_attr.data = np.ascontiguousarray(tangents, dtype=np.float32)
    mesh_obj.tangents = [tan_attr]
    uv_attr = ssbh_data_py.mesh_data.AttributeData("map1")
    uv_attr.data = np.ascontiguousarray(uvs, dtype=np.float32)
    mesh_obj.texture_coordinates = [uv_attr]
    mesh_obj.vertex_indices = np.ascontiguousarray(indices, dtype=np.uint32)
    if influences:
        mesh_obj.bone_influences = influences
    elif fallback_bone:
        mesh_obj.parent_bone_name = fallback_bone
    return mesh_obj


def _chunk_triangles(indices, max_verts=_MAX_SKIN_VERTS):
    ntri = len(indices) // 3
    chunks = []
    remap = {}
    old_ids = []
    local = []
    for tri in range(ntri):
        verts = indices[tri * 3 : tri * 3 + 3]
        needed = sum(1 for vid in verts if vid not in remap)
        if old_ids and len(old_ids) + needed > max_verts:
            chunks.append((old_ids, local))
            remap = {}
            old_ids = []
            local = []
        packed = []
        for vid in verts:
            nid = remap.get(int(vid))
            if nid is None:
                nid = len(old_ids)
                remap[int(vid)] = nid
                old_ids.append(int(vid))
            packed.append(nid)
        local.extend(packed)
    if local:
        chunks.append((old_ids, local))
    return chunks


def _slice_influences(influences, old_ids):
    if not influences:
        return []
    lookup = {old: new for new, old in enumerate(old_ids)}
    sliced = []
    for inf in influences:
        weights = []
        for weight in inf.vertex_weights:
            nid = lookup.get(int(weight.vertex_index))
            if nid is None:
                continue
            weights.append(ssbh_data_py.mesh_data.VertexWeight(nid, float(weight.vertex_weight)))
        if weights:
            sliced.append(ssbh_data_py.mesh_data.BoneInfluence(inf.bone_name, weights))
    return sliced


def _mesh_to_objects(obj, name, start_sub, smash_xf, name_map, fallback_bone):
    mesh = obj.data
    if mesh is None or len(mesh.vertices) == 0:
        return []
    try:
        mesh.calc_loop_triangles()
    except Exception:
        return []
    ntri = len(mesh.loop_triangles)
    if ntri < 1:
        return []
    nvert = len(mesh.vertices)
    positions = np.zeros(nvert * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", positions)
    positions = _apply_matrix_points(positions.reshape((-1, 3)), smash_xf)

    normals = np.zeros(nvert * 3, dtype=np.float32)
    mesh.vertices.foreach_get("normal", normals)
    normals = _apply_matrix_vectors(normals.reshape((-1, 3)), smash_xf)
    normals4 = np.append(normals, np.zeros((nvert, 1), dtype=np.float32), axis=1)

    indices = np.zeros(ntri * 3, dtype=np.uint32)
    mesh.loop_triangles.foreach_get("vertices", indices)

    uvs = np.zeros((nvert, 2), dtype=np.float32)
    uv_layer = None
    layers = getattr(mesh, "uv_layers", None)
    if layers:
        uv_layer = layers.get("map1") or layers.get("UVMap") or (layers.active if layers else None)
        if uv_layer is None and len(layers) > 0:
            uv_layer = layers[0]
    if uv_layer is not None and len(mesh.loops) > 0:
        loop_uvs = np.zeros(len(mesh.loops) * 2, dtype=np.float32)
        uv_layer.data.foreach_get("uv", loop_uvs)
        loop_index = np.zeros(len(mesh.loops), dtype=np.uint32)
        mesh.loops.foreach_get("vertex_index", loop_index)
        uvs = per_loop_to_per_vertex(loop_uvs, loop_index, (nvert, 2))
        uvs[:, 1] = 1.0 - uvs[:, 1]

    # Dummy tangents: calculate_tangents_vec4 panics on some imported meshes
    # and would crash the viewport draw callback.
    tangents = np.zeros((nvert, 4), dtype=np.float32)
    tangents[:, 0] = 1.0
    tangents[:, 3] = 1.0

    influences = _collect_influences(obj, name_map, nvert)
    if not influences or nvert <= _MAX_SKIN_VERTS:
        packed = _pack_mesh_object(
            name,
            start_sub,
            positions,
            normals4,
            uvs,
            tangents,
            indices,
            influences,
            fallback_bone,
        )
        return [packed] if packed is not None else []

    objects = []
    for offset, (old_ids, local) in enumerate(_chunk_triangles(indices)):
        old_ids = np.asarray(old_ids, dtype=np.int64)
        objects.append(
            _pack_mesh_object(
                name,
                start_sub + offset,
                positions[old_ids],
                normals4[old_ids],
                uvs[old_ids],
                tangents[old_ids],
                local,
                _slice_influences(influences, old_ids.tolist()),
                fallback_bone,
            )
        )
    return objects


def _collect_influences(obj, name_map, nvert):
    groups = obj.vertex_groups
    if not groups or not name_map:
        return []
    group_index_to_export = {}
    for vg in groups:
        export = name_map.get(vg.name)
        if export:
            group_index_to_export[vg.index] = export
    if not group_index_to_export:
        return []
    weights_by_bone = {name: [] for name in group_index_to_export.values()}
    mesh = obj.data
    for vertex in mesh.vertices:
        pairs = []
        for grp in vertex.groups:
            export = group_index_to_export.get(grp.group)
            if export is None or grp.weight <= 0.0:
                continue
            pairs.append((export, float(grp.weight)))
        if not pairs:
            continue
        pairs.sort(key=lambda item: item[1], reverse=True)
        pairs = pairs[:4]
        total = sum(weight for _name, weight in pairs)
        if total <= 1e-8:
            continue
        for export, weight in pairs:
            weights_by_bone[export].append(
                ssbh_data_py.mesh_data.VertexWeight(vertex.index, weight / total)
            )
    influences = []
    for export, weights in weights_by_bone.items():
        if weights:
            influences.append(ssbh_data_py.mesh_data.BoneInfluence(export, weights))
    return influences


def _clear_folder(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name in (
        "model.numshb",
        "model.nusktb",
        "model.numatb",
        "model.numdlb",
        "model.nuhlpb",
    ):
        path = folder / name
        try:
            if path.is_file():
                path.unlink()
        except Exception:
            pass


def _write_folder(folder, mesh_data, skel, matl, modl):
    _clear_folder(folder)
    mesh_data.save(str(folder / "model.numshb"))
    skel.save(str(folder / "model.nusktb"))
    matl.save(str(folder / "model.numatb"))
    modl.save(str(folder / "model.numdlb"))
    return str(folder)


def _new_modl():
    modl = ssbh_data_py.modl_data.ModlData()
    modl.model_name = "model"
    modl.skeleton_file_name = "model.nusktb"
    modl.material_file_names = ["model.numatb"]
    modl.animation_file_name = None
    modl.mesh_file_name = "model.numshb"
    return modl


def build_armature_folder(arm, meshes, smash_bones=False):
    """Write one ModelFolder for a GPU extra armature. Returns (path, uploaded)."""
    if arm is None or not meshes:
        return None, []
    arm_name = arm.name
    folder = extra_temp_dir() / safe_token(arm_name, 48)
    skel, name_map = _build_skel(arm, arm_name, smash_bones=smash_bones, meshes=meshes)
    fallback = None
    for prefer in ("Hip", "Trans", "Root"):
        if prefer in name_map:
            fallback = name_map[prefer]
            break
    if fallback is None and skel.bones:
        fallback = skel.bones[0].name

    mesh_data = ssbh_data_py.mesh_data.MeshData()
    matl = ssbh_data_py.matl_data.MatlData()
    modl = _new_modl()
    used_names = {}
    uploaded = []
    col_cache = {}
    arm_world = getattr(arm, "matrix_world", None) or Matrix.Identity(4)

    for obj in meshes:
        group = extra_mesh_name(arm_name, obj.name)
        sub = used_names.get(group, 0)
        smash_xf = extra_gpu_matrix(
            arm_world @ _mesh_to_armature_rest(obj, arm), smash_bones=smash_bones
        )
        rigid = fallback
        parent_bone = getattr(obj, "parent_bone", "") or ""
        if parent_bone in name_map:
            rigid = name_map[parent_bone]
        try:
            packed = _mesh_to_objects(obj, group, sub, smash_xf, name_map, rigid)
        except BaseException:
            packed = []
        if not packed:
            continue
        used_names[group] = sub + len(packed)
        label = f"{_MAT_LABEL}_{safe_token(obj.name, 20)}"
        image = _albedo_image(obj)
        col_name = None
        if image is not None:
            key = int(image.as_pointer())
            if key not in col_cache:
                col_cache[key] = _write_col_nutexb(
                    image, folder, f"vpcol_{safe_token(image.name, 28)}"
                )
            col_name = col_cache[key]
        for mesh_obj in packed:
            matl.entries.append(_opaque_material(label, col_name))
            mesh_data.objects.append(mesh_obj)
            modl.entries.append(
                ssbh_data_py.modl_data.ModlEntryData(group, mesh_obj.subindex, label)
            )
        uploaded.append((int(obj.as_pointer()), group, packed[0].subindex))

    if not mesh_data.objects:
        return None, []
    return _write_folder(folder, mesh_data, skel, matl, modl), uploaded


def build_loose_folder(meshes):
    """Write one ModelFolder for meshes with no armature."""
    if not meshes:
        return None, []
    folder = extra_temp_dir() / LOOSE_ARM
    entries = [(obj, loose_bone_name(obj.name)) for obj in meshes]
    skel = _loose_skel(entries)
    name_map = {}
    mesh_data = ssbh_data_py.mesh_data.MeshData()
    matl = ssbh_data_py.matl_data.MatlData()
    modl = _new_modl()
    used_names = {}
    uploaded = []
    col_cache = {}
    for obj, bone_name in entries:
        group = extra_mesh_name(LOOSE_ARM, obj.name)
        sub = used_names.get(group, 0)
        smash_xf = _Z_UP_TO_Y_UP
        packed = _mesh_to_objects(obj, group, sub, smash_xf, name_map, bone_name)
        if not packed:
            continue
        used_names[group] = sub + len(packed)
        label = f"{_MAT_LABEL}_{safe_token(obj.name, 20)}"
        image = _albedo_image(obj)
        col_name = None
        if image is not None:
            key = int(image.as_pointer())
            if key not in col_cache:
                col_cache[key] = _write_col_nutexb(
                    image, folder, f"vpcol_{safe_token(image.name, 28)}"
                )
            col_name = col_cache[key]
        for mesh_obj in packed:
            matl.entries.append(_opaque_material(label, col_name))
            mesh_data.objects.append(mesh_obj)
            modl.entries.append(
                ssbh_data_py.modl_data.ModlEntryData(group, mesh_obj.subindex, label)
            )
        uploaded.append((int(obj.as_pointer()), group, packed[0].subindex))

    if not mesh_data.objects:
        return None, []
    return _write_folder(folder, mesh_data, skel, matl, modl), uploaded


def iter_bound_meshes(scene, arm, skip_mesh):
    objects = getattr(scene, "objects", None)
    if not objects:
        return
    for obj in objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        if skip_mesh(obj):
            continue
        name = obj.name or ""
        if name.startswith("SUB_WGT_") or name.startswith("."):
            continue
        try:
            if obj.hide_get():
                continue
        except Exception:
            pass
        bound = False
        try:
            if obj.find_armature() == arm:
                bound = True
        except Exception:
            pass
        if not bound:
            for modifier in getattr(obj, "modifiers", []) or []:
                if getattr(modifier, "type", "") == "ARMATURE" and getattr(modifier, "object", None) == arm:
                    bound = True
                    break
        if not bound and getattr(obj, "parent", None) == arm:
            bound = True
        if bound:
            yield obj


def iter_extra_armatures(scene, skip_armature):
    objects = getattr(scene, "objects", None)
    if not objects:
        return
    for obj in objects:
        if getattr(obj, "type", "") != "ARMATURE":
            continue
        if skip_armature(obj):
            continue
        yield obj


def iter_loose_meshes(scene, is_smash_mesh, is_smash_armature):
    objects = getattr(scene, "objects", None)
    if not objects:
        return
    for obj in objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        if is_smash_mesh(obj):
            continue
        name = obj.name or ""
        if name.startswith("SUB_WGT_") or name.startswith("."):
            continue
        arm = None
        try:
            arm = obj.find_armature()
        except Exception:
            arm = None
        if arm is not None:
            continue
        parent = getattr(obj, "parent", None)
        if parent is not None and getattr(parent, "type", "") == "ARMATURE":
            continue
        yield obj


def extra_scene_fingerprint(scene, skip_armature, skip_mesh, include_loose=False):
    parts = []
    for arm in iter_extra_armatures(scene, skip_armature):
        meshes = tuple(
            (
                int(obj.as_pointer()),
                int(obj.data.as_pointer()) if obj.data else 0,
                len(obj.data.vertices) if obj.data else 0,
            )
            for obj in iter_bound_meshes(scene, arm, skip_mesh)
        )
        if not meshes:
            continue
        bone_count = len(getattr(getattr(arm, "data", None), "bones", []) or [])
        parts.append((int(arm.as_pointer()), arm.name, bone_count, meshes))
    if include_loose:
        loose = tuple(
            (
                int(obj.as_pointer()),
                int(obj.data.as_pointer()) if obj.data else 0,
                len(obj.data.vertices) if obj.data else 0,
            )
            for obj in iter_loose_meshes(scene, skip_mesh, skip_armature)
        )
        if loose:
            parts.append(("loose", loose))
    return tuple(parts)
