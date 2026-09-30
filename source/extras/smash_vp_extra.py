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


def _albedo_image(mat):
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


# (image identity, folder) -> stem of the .nutexb already written for it.
_col_written = {}


def _write_col_nutexb(image, folder, stem):
    if image is None:
        return None
    try:
        key = (int(image.as_pointer()), image.name, image.filepath, tuple(image.size), str(folder))
    except Exception:
        key = None
    done = _col_written.get(key) if key is not None else None
    if done and (Path(folder) / f"{done}.nutexb").is_file():
        return done
    result = _write_col_nutexb_uncached(image, folder, stem)
    if key is not None and result:
        _col_written[key] = result
    return result


def _write_col_nutexb_uncached(image, folder, stem):
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


def _loop_normals(mesh, nloop):
    normals = np.zeros(nloop * 3, dtype=np.float32)
    corner = getattr(mesh, "corner_normals", None)
    if corner is not None:
        corner.foreach_get("vector", normals)
    else:
        try:
            mesh.calc_normals_split()
        except Exception:
            pass
        mesh.loops.foreach_get("normal", normals)
    return normals.reshape(-1, 3)


def _loop_tangents(mesh, uv_layer, nloop):
    """MikkTSpace tangents (w = the exporter's flipped bitangent sign), or None."""
    if uv_layer is None:
        return None
    try:
        mesh.calc_tangents(uvmap=uv_layer.name)
    except Exception:
        return None
    try:
        tangents = np.zeros(nloop * 3, dtype=np.float32)
        mesh.loops.foreach_get("tangent", tangents)
        signs = np.zeros(nloop, dtype=np.float32)
        mesh.loops.foreach_get("bitangent_sign", signs)
    finally:
        try:
            mesh.free_tangents()
        except Exception:
            pass
    return tangents.reshape(-1, 3), signs * -1.0


def _mesh_uv_layer(mesh):
    layers = getattr(mesh, "uv_layers", None)
    if not layers:
        return None
    layer = layers.get("map1") or layers.get("UVMap") or layers.active
    if layer is None and len(layers) > 0:
        layer = layers[0]
    return layer


# Packed meshes keyed on everything that shapes them, so a rebuild for a
# material change (HB Master slider) does not re-pack every mesh.
_pack_cache = {}


def _geometry_key(obj, mesh):
    parts = [len(mesh.vertices), len(mesh.loops), len(mesh.polygons)]
    try:
        co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        parts.append(hash(co.tobytes()))
        mats = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get("material_index", mats)
        parts.append(hash(mats.tobytes()))
        layer = _mesh_uv_layer(mesh)
        if layer is not None:
            uv = np.empty(len(mesh.loops) * 2, dtype=np.float32)
            layer.data.foreach_get("uv", uv)
            parts.append((layer.name, hash(uv.tobytes())))
        attr = mesh.attributes.get("custom_normal") or mesh.attributes.get("_smush_blender_custom_normals")
        if attr is not None:
            data = np.empty(len(attr.data) * 3, dtype=np.float32)
            try:
                attr.data.foreach_get("vector", data)
                parts.append(hash(data.tobytes()))
            except Exception:
                pass
        parts.append(bool(getattr(mesh, "has_custom_normals", False)))
    except Exception:
        return None
    parts.append(tuple(vg.name for vg in obj.vertex_groups))
    return tuple(parts)


def forget_packed_meshes():
    _pack_cache.clear()


def _mesh_to_objects(obj, name, start_sub, smash_xf, name_map, fallback_bone):
    mesh = obj.data
    if mesh is None:
        return []
    geo = _geometry_key(obj, mesh)
    key = None
    if geo is not None:
        key = (int(obj.as_pointer()), int(mesh.as_pointer()), name, start_sub, fallback_bone,
               tuple(round(v, 6) for row in smash_xf for v in row),
               tuple(sorted(name_map.items())) if name_map else (), geo)
        cached = _pack_cache.get(int(obj.as_pointer()))
        if cached is not None and cached[0] == key:
            return cached[1]
    packed = _mesh_to_objects_uncached(obj, name, start_sub, smash_xf, name_map, fallback_bone)
    if key is not None:
        _pack_cache[int(obj.as_pointer())] = (key, packed)
    return packed


def _mesh_to_objects_uncached(obj, name, start_sub, smash_xf, name_map, fallback_bone):
    """Pack a mesh as Smash mesh objects, one run of subindices per material slot.

    Vertices are split where their UVs or normals differ between faces, like the
    exporter does, so texture seams and custom (e.g. Smart) normals come out as
    they will in game. Returns [(MeshObjectData, material_index), ...].
    """
    mesh = obj.data
    if mesh is None or len(mesh.vertices) == 0:
        return []
    try:
        mesh.calc_loop_triangles()
    except Exception:
        return []
    ntri = len(mesh.loop_triangles)
    nloop = len(mesh.loops)
    if ntri < 1 or nloop < 1:
        return []
    nvert = len(mesh.vertices)
    positions = np.zeros(nvert * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", positions)
    positions = _apply_matrix_points(positions.reshape((-1, 3)), smash_xf)

    loop_vert = np.zeros(nloop, dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vert)
    normals = _loop_normals(mesh, nloop)
    uv_layer = _mesh_uv_layer(mesh)
    uvs = np.zeros((nloop, 2), dtype=np.float32)
    if uv_layer is not None:
        flat = np.zeros(nloop * 2, dtype=np.float32)
        uv_layer.data.foreach_get("uv", flat)
        uvs = flat.reshape(-1, 2)
    tangent_data = _loop_tangents(mesh, uv_layer, nloop)

    # One output vertex per distinct (vertex, uv, normal, tangent sign).
    keys = [loop_vert[:, None].astype(np.float64),
            np.round(uvs * 1e5).astype(np.float64),
            np.round(normals * 1e4).astype(np.float64)]
    if tangent_data is not None:
        keys.append(tangent_data[1][:, None].astype(np.float64))
    _unique, first_loop, loop_to_new = np.unique(
        np.concatenate(keys, axis=1), axis=0, return_index=True, return_inverse=True)
    loop_to_new = loop_to_new.reshape(-1)
    new_vert = loop_vert[first_loop]
    new_pos = positions[new_vert]
    new_nrm = _apply_matrix_vectors(normals[first_loop], smash_xf)
    new_nrm4 = np.append(new_nrm, np.zeros((len(first_loop), 1), dtype=np.float32), axis=1)
    new_uv = uvs[first_loop].copy()
    new_uv[:, 1] = 1.0 - new_uv[:, 1]
    new_tan = np.zeros((len(first_loop), 4), dtype=np.float32)
    if tangent_data is not None:
        new_tan[:, :3] = _apply_matrix_vectors(tangent_data[0][first_loop], smash_xf)
        new_tan[:, 3] = tangent_data[1][first_loop]
    else:
        new_tan[:, 0] = 1.0
        new_tan[:, 3] = 1.0

    tri_loops = np.zeros(ntri * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("loops", tri_loops)
    tri_mat = np.zeros(ntri, dtype=np.int32)
    mesh.loop_triangles.foreach_get("material_index", tri_mat)
    tris = loop_to_new[tri_loops].reshape(-1, 3)
    weights = _collect_weights(obj, name_map, nvert)

    out = []
    sub = start_sub
    for mat_index in np.unique(tri_mat):
        part = tris[tri_mat == mat_index].reshape(-1)
        used, local = np.unique(part, return_inverse=True)
        local = local.astype(np.uint32).reshape(-1)
        influences = _influences_for(weights, new_vert[used])
        pieces = [(np.arange(len(used)), local)]
        if influences and len(used) > _MAX_SKIN_VERTS:
            pieces = [(np.asarray(ids, dtype=np.int64), np.asarray(idx, dtype=np.uint32))
                      for ids, idx in _chunk_triangles(local)]
        for ids, idx in pieces:
            src = used[ids]
            packed = _pack_mesh_object(
                name, sub, new_pos[src], new_nrm4[src], new_uv[src], new_tan[src], idx,
                _slice_influences(influences, ids.tolist()) if len(pieces) > 1 else influences,
                fallback_bone,
            )
            if packed is not None:
                out.append((packed, int(mat_index)))
                sub += 1
    return out


def _collect_weights(obj, name_map, nvert):
    """Per original vertex: [(export bone, normalised weight), ...] (max 4)."""
    groups = obj.vertex_groups
    if not groups or not name_map:
        return None
    group_index_to_export = {}
    for vg in groups:
        export = name_map.get(vg.name)
        if export:
            group_index_to_export[vg.index] = export
    if not group_index_to_export:
        return None
    weights = [None] * nvert
    for vertex in obj.data.vertices:
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
        weights[vertex.index] = [(export, weight / total) for export, weight in pairs]
    return weights


def _influences_for(weights, original_ids):
    if weights is None:
        return []
    by_bone = {}
    for new_id, old_id in enumerate(original_ids.tolist()):
        pairs = weights[old_id]
        if not pairs:
            continue
        for export, weight in pairs:
            by_bone.setdefault(export, []).append(
                ssbh_data_py.mesh_data.VertexWeight(new_id, weight))
    return [ssbh_data_py.mesh_data.BoneInfluence(export, items)
            for export, items in by_bone.items()]


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


class _Materials:
    """One Smash material per Blender material, shared by every mesh using it.

    HB Master Shader materials get the maps and shader export would ship
    (smash_vp_hb). Anything else keeps the plain preview: its colour texture on
    the default fighter material.
    """

    def __init__(self, folder):
        self.folder = folder
        self.matl = ssbh_data_py.matl_data.MatlData()
        self.labels = {}
        self.col_cache = {}
        self.keep = set()
        self.hb = {}
        self.metal_skin = []

    def label(self, mat):
        key = mat.name if mat is not None else ""
        label = self.labels.get(key)
        if label is not None:
            return label
        label = f"{_MAT_LABEL}_{safe_token(key or 'none', 32)}"
        self.labels[key] = label
        entry = None
        if mat is not None:
            try:
                entry = self._hb_entry(mat, label)
            except Exception:
                entry = None
        if entry is None:
            entry = _opaque_material(label, self._col(mat))
        self.matl.entries.append(entry)
        return label

    def _hb_entry(self, mat, label):
        from . import smash_vp_hb as hb
        enabled, size = hb.preview_settings()
        if not enabled or hb.master_node(mat) is None:
            return None
        textures, result = hb.build_textures(mat, self.folder, size)
        if textures is None:
            return None
        self.keep.update(textures.values())
        self.hb[mat.name] = result["status"]
        entry = hb.matl_entry(label, mat, textures)
        if result.get("subsurface") and not hb.shader_is_subsurface(entry.shader_label):
            self.metal_skin.append(mat.name)
        return entry

    def _col(self, mat):
        image = _albedo_image(mat)
        if image is None:
            return None
        key = int(image.as_pointer())
        if key not in self.col_cache:
            self.col_cache[key] = _write_col_nutexb(
                image, self.folder, f"vpcol_{safe_token(image.name, 28)}"
            )
        if self.col_cache[key]:
            self.keep.add(self.col_cache[key])
        return self.col_cache[key]

    def finish(self):
        from . import smash_vp_hb as hb
        hb.prune_textures(self.folder, self.keep)
        global last_hb_status, last_hb_metal_skin
        last_hb_status = dict(self.hb)
        last_hb_metal_skin = list(self.metal_skin)


# Material name -> 'baked' / 'quick' / 'approx' for the last folder built.
last_hb_status = {}
# HB materials marked as skin (SSS Mask) that export would put on a plain shader,
# where PRM.r - the SSS Mask - reads as metalness.
last_hb_metal_skin = []


def _add_packed(obj, packed, group, materials, mesh_data, modl):
    slots = obj.material_slots
    for mesh_obj, mat_index in packed:
        mat = slots[mat_index].material if 0 <= mat_index < len(slots) else None
        label = materials.label(mat)
        mesh_data.objects.append(mesh_obj)
        modl.entries.append(
            ssbh_data_py.modl_data.ModlEntryData(group, mesh_obj.subindex, label)
        )


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
    materials = _Materials(folder)
    modl = _new_modl()
    used_names = {}
    uploaded = []
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
        _add_packed(obj, packed, group, materials, mesh_data, modl)
        uploaded.append((int(obj.as_pointer()), group, packed[0][0].subindex, len(packed)))

    if not mesh_data.objects:
        return None, []
    materials.finish()
    return _write_folder(folder, mesh_data, skel, materials.matl, modl), uploaded


def build_loose_folder(meshes):
    """Write one ModelFolder for meshes with no armature."""
    if not meshes:
        return None, []
    folder = extra_temp_dir() / LOOSE_ARM
    entries = [(obj, loose_bone_name(obj.name)) for obj in meshes]
    skel = _loose_skel(entries)
    name_map = {}
    mesh_data = ssbh_data_py.mesh_data.MeshData()
    materials = _Materials(folder)
    modl = _new_modl()
    used_names = {}
    uploaded = []
    for obj, bone_name in entries:
        group = extra_mesh_name(LOOSE_ARM, obj.name)
        sub = used_names.get(group, 0)
        smash_xf = _Z_UP_TO_Y_UP
        packed = _mesh_to_objects(obj, group, sub, smash_xf, name_map, bone_name)
        if not packed:
            continue
        used_names[group] = sub + len(packed)
        _add_packed(obj, packed, group, materials, mesh_data, modl)
        uploaded.append((int(obj.as_pointer()), group, packed[0][0].subindex, len(packed)))

    if not mesh_data.objects:
        return None, []
    materials.finish()
    return _write_folder(folder, mesh_data, skel, materials.matl, modl), uploaded


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
