import math
from collections import deque

import bmesh
import bpy
from bpy.types import Operator


# Match Blender's STD_UV_CONNECT_LIMIT so island splits agree with the UV editor.
UV_CONNECT_EPS = 1e-4
STACK_BBOX_IOU = 0.5
STACK_OCCUPANCY_IOU = 0.7
STACK_COVERAGE = 0.85
STACK_AREA_RATIO = 0.75
OCCUPANCY_RES = 64
V_OFFSET = 1.0
_FOLD_EPS = 1e-10


def _mesh_edit_or_object_context(context):
    if context.mode in {"POSE", "EDIT_ARMATURE"}:
        return False
    active = getattr(context, "active_object", None)
    if active is not None and active.type == "MESH":
        return True
    return any(obj.type == "MESH" for obj in getattr(context, "selected_objects", []) or [])


def _iter_target_meshes(context):
    seen = set()
    objects = list(context.selected_objects)
    active = context.view_layer.objects.active
    if active is not None and active not in objects:
        objects.append(active)

    for obj in objects:
        if obj is None or obj.type != "MESH" or obj.data in seen:
            continue
        seen.add(obj.data)
        yield obj


def _uvs_match(loop_a, loop_b, uv_layer):
    ua = loop_a[uv_layer].uv
    ub = loop_b[uv_layer].uv
    return abs(ua.x - ub.x) < UV_CONNECT_EPS and abs(ua.y - ub.y) < UV_CONNECT_EPS


def _edge_loop_uvs(face, edge, uv_layer):
    uvs = {}
    for loop in face.loops:
        if loop.vert in edge.verts:
            uvs[loop.vert] = loop[uv_layer].uv
    return uvs


def _edge_uv_connected(face_a, face_b, edge, uv_layer):
    loops_a = [loop for loop in face_a.loops if loop.vert in edge.verts]
    loops_b = [loop for loop in face_b.loops if loop.vert in edge.verts]
    if len(loops_a) != 2 or len(loops_b) != 2:
        return False
    for loop_a in loops_a:
        loop_b = next((loop for loop in loops_b if loop.vert == loop_a.vert), None)
        if loop_b is None or not _uvs_match(loop_a, loop_b, uv_layer):
            return False
    uvs = _edge_loop_uvs(face_a, edge, uv_layer)
    if len(uvs) != 2:
        return False
    (uv0, uv1) = tuple(uvs.values())
    if abs(uv0.x - uv1.x) < UV_CONNECT_EPS and abs(uv0.y - uv1.y) < UV_CONNECT_EPS:
        return False
    return True


def _xy(point):
    if hasattr(point, "x"):
        return point.x, point.y
    return point[0], point[1]


def _uv_side(uv0, uv1, point):
    x0, y0 = _xy(uv0)
    x1, y1 = _xy(uv1)
    px, py = _xy(point)
    return (x1 - x0) * (py - y0) - (y1 - y0) * (px - x0)


def _off_edge_uv_centroid(face, edge, uv_layer):
    xs = []
    ys = []
    for loop in face.loops:
        if loop.vert in edge.verts:
            continue
        uv = loop[uv_layer].uv
        xs.append(uv.x)
        ys.append(uv.y)
    if not xs:
        return None
    count = len(xs)
    return (sum(xs) / count, sum(ys) / count)


def _faces_fold_in_uv(face_a, face_b, edge, uv_layer):
    uvs = _edge_loop_uvs(face_a, edge, uv_layer)
    if len(uvs) != 2:
        return False
    uv0, uv1 = tuple(uvs.values())
    centroid_a = _off_edge_uv_centroid(face_a, edge, uv_layer)
    centroid_b = _off_edge_uv_centroid(face_b, edge, uv_layer)
    if centroid_a is None or centroid_b is None:
        return False
    side_a = _uv_side(uv0, uv1, centroid_a)
    side_b = _uv_side(uv0, uv1, centroid_b)
    if abs(side_a) < _FOLD_EPS or abs(side_b) < _FOLD_EPS:
        return False
    return side_a * side_b > 0


def _should_connect_uv(face_a, face_b, edge, uv_layer):
    if not _edge_uv_connected(face_a, face_b, edge, uv_layer):
        return False
    return not _faces_fold_in_uv(face_a, face_b, edge, uv_layer)


def _uv_islands(bm, uv_layer):
    bm.faces.ensure_lookup_table()
    visited = [False] * len(bm.faces)
    islands = []

    for start in bm.faces:
        if visited[start.index]:
            continue
        island = []
        queue = deque([start])
        visited[start.index] = True
        while queue:
            face = queue.popleft()
            island.append(face)
            for edge in face.edges:
                for other in edge.link_faces:
                    if visited[other.index]:
                        continue
                    if _should_connect_uv(face, other, edge, uv_layer):
                        visited[other.index] = True
                        queue.append(other)
        islands.append(island)
    return islands


def _island_bbox(faces, uv_layer):
    min_u = min_v = float("inf")
    max_u = max_v = float("-inf")
    for face in faces:
        for loop in face.loops:
            uv = loop[uv_layer].uv
            min_u = min(min_u, uv.x)
            min_v = min(min_v, uv.y)
            max_u = max(max_u, uv.x)
            max_v = max(max_v, uv.y)
    if min_u == float("inf"):
        return None
    return (min_u, min_v, max_u, max_v)


def _bbox_iou(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max((ax1 - ax0) * (ay1 - ay0), 1e-12)
    area_b = max((bx1 - bx0) * (by1 - by0), 1e-12)
    return inter / (area_a + area_b - inter)


def _point_in_tri(px, py, a, b, c):
    ax, ay = a
    bx, by = b
    cx, cy = c
    v0x, v0y = cx - ax, cy - ay
    v1x, v1y = bx - ax, by - ay
    v2x, v2y = px - ax, py - ay
    dot00 = v0x * v0x + v0y * v0y
    dot01 = v0x * v1x + v0y * v1y
    dot02 = v0x * v2x + v0y * v2y
    dot11 = v1x * v1x + v1y * v1y
    dot12 = v1x * v2x + v1y * v2y
    denom = dot00 * dot11 - dot01 * dot01
    if abs(denom) < 1e-20:
        return False
    inv = 1.0 / denom
    u = (dot11 * dot02 - dot01 * dot12) * inv
    v = (dot00 * dot12 - dot01 * dot02) * inv
    return u >= -1e-6 and v >= -1e-6 and (u + v) <= 1.0 + 1e-6


def _add_cell(cells, u, v):
    cells.add((int(math.floor(u * OCCUPANCY_RES)), int(math.floor(v * OCCUPANCY_RES))))


def _rasterize_edge(cells, a, b):
    dx = abs(int(b[0] * OCCUPANCY_RES) - int(a[0] * OCCUPANCY_RES))
    dy = abs(int(b[1] * OCCUPANCY_RES) - int(a[1] * OCCUPANCY_RES))
    steps = max(dx, dy, 1)
    for i in range(steps + 1):
        t = i / steps
        _add_cell(cells, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _rasterize_triangle(cells, a, b, c):
    _rasterize_edge(cells, a, b)
    _rasterize_edge(cells, b, c)
    _rasterize_edge(cells, c, a)
    min_u = min(a[0], b[0], c[0])
    max_u = max(a[0], b[0], c[0])
    min_v = min(a[1], b[1], c[1])
    max_v = max(a[1], b[1], c[1])
    x0 = int(math.floor(min_u * OCCUPANCY_RES))
    x1 = int(math.floor(max_u * OCCUPANCY_RES))
    y0 = int(math.floor(min_v * OCCUPANCY_RES))
    y1 = int(math.floor(max_v * OCCUPANCY_RES))
    if (x1 - x0 + 1) * (y1 - y0 + 1) > 8192:
        return
    scale = float(OCCUPANCY_RES)
    for gy in range(y0, y1 + 1):
        py = (gy + 0.5) / scale
        for gx in range(x0, x1 + 1):
            px = (gx + 0.5) / scale
            if _point_in_tri(px, py, a, b, c):
                cells.add((gx, gy))


def _island_occupancy(faces, uv_layer):
    cells = set()
    for face in faces:
        uvs = [(loop[uv_layer].uv.x, loop[uv_layer].uv.y) for loop in face.loops]
        if len(uvs) < 3:
            continue
        origin = uvs[0]
        for index in range(1, len(uvs) - 1):
            _rasterize_triangle(cells, origin, uvs[index], uvs[index + 1])
    return cells


def _occupancy_match(a, b):
    if not a or not b:
        return False
    inter = len(a & b)
    if inter == 0:
        return False
    union = len(a | b)
    iou = inter / union
    if iou >= STACK_OCCUPANCY_IOU:
        return True
    smaller = min(len(a), len(b))
    larger = max(len(a), len(b))
    coverage = inter / smaller
    area_ratio = smaller / larger
    return coverage >= STACK_COVERAGE and area_ratio >= STACK_AREA_RATIO


def _islands_are_stacked(bbox_a, bbox_b, occupancy_a, occupancy_b):
    if bbox_a is None or bbox_b is None:
        return False
    if _bbox_iou(bbox_a, bbox_b) < STACK_BBOX_IOU:
        return False
    return _occupancy_match(occupancy_a, occupancy_b)


def _island_centroid_3d(faces):
    verts = set()
    for face in faces:
        verts.update(face.verts)
    if not verts:
        return (0.0, 0.0, 0.0)
    count = len(verts)
    return (
        sum(vert.co.x for vert in verts) / count,
        sum(vert.co.y for vert in verts) / count,
        sum(vert.co.z for vert in verts) / count,
    )


def _group_stacked_islands(bboxes, occupancies):
    parent = list(range(len(bboxes)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a, b):
        root_a = find(a)
        root_b = find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    for i in range(len(bboxes)):
        if bboxes[i] is None:
            continue
        for j in range(i + 1, len(bboxes)):
            if _islands_are_stacked(bboxes[i], bboxes[j], occupancies[i], occupancies[j]):
                union(i, j)

    groups = {}
    for i in range(len(bboxes)):
        groups.setdefault(find(i), []).append(i)
    return [members for members in groups.values() if len(members) > 1]


def _offset_island(faces, uv_layer, delta_v):
    for face in faces:
        for loop in face.loops:
            loop[uv_layer].uv.y += delta_v


def _unstack_uv_layer(bm, uv_layer):
    islands = _uv_islands(bm, uv_layer)
    bboxes = [_island_bbox(island, uv_layer) for island in islands]
    occupancies = [_island_occupancy(island, uv_layer) for island in islands]
    moved = 0
    for group in _group_stacked_islands(bboxes, occupancies):
        group.sort(key=lambda index: _island_centroid_3d(islands[index]))
        for stack_index, island_index in enumerate(group):
            if stack_index == 0:
                continue
            _offset_island(islands[island_index], uv_layer, V_OFFSET * stack_index)
            moved += 1
    return moved


def unstack_uvs_on_mesh(obj):
    if obj is None or obj.type != "MESH" or obj.data is None:
        return 0
    mesh = obj.data
    if not mesh.uv_layers:
        return 0

    in_edit = obj.mode == "EDIT"
    if in_edit:
        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
    else:
        bm = bmesh.new()
        bm.from_mesh(mesh)

    moved = 0
    if bm.faces:
        for uv_map in mesh.uv_layers:
            uv_layer = bm.loops.layers.uv.get(uv_map.name)
            if uv_layer is None:
                continue
            moved += _unstack_uv_layer(bm, uv_layer)

    if in_edit:
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    else:
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()

    return moved


class SUB_OP_unstack_uv_islands(Operator):
    bl_idname = "sub.unstack_uv_islands"
    bl_label = "Unstack UV Islands"
    bl_description = (
        "Move truly stacked UV copies up by 1 UV unit on the selected mesh. "
        "Does not run on armatures or bones."
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _mesh_edit_or_object_context(context)

    def execute(self, context):
        if context.mode in {"POSE", "EDIT_ARMATURE"}:
            self.report({"WARNING"}, "Unstack UV Islands only works on a mesh, not on bones.")
            return {"CANCELLED"}

        meshes = list(_iter_target_meshes(context))
        if not meshes:
            self.report({"WARNING"}, "Select a mesh in Object or Edit Mesh mode.")
            return {"CANCELLED"}

        meshes_changed = 0
        islands_moved = 0
        skipped_no_uv = 0

        for obj in meshes:
            if obj.type != "MESH" or obj.data is None:
                continue
            if not obj.data.uv_layers:
                skipped_no_uv += 1
                continue
            try:
                moved = unstack_uvs_on_mesh(obj)
            except Exception as exc:
                self.report({"WARNING"}, f"Skipped {obj.name}: {exc}")
                continue
            if moved:
                meshes_changed += 1
                islands_moved += moved

        if islands_moved:
            self.report(
                {"INFO"},
                f"Unstacked {islands_moved} UV island(s) on {meshes_changed} mesh(es).",
            )
        elif skipped_no_uv == len(meshes):
            self.report({"WARNING"}, "Selected meshes have no UV maps.")
        else:
            self.report({"INFO"}, "No stacked UV islands found.")
        return {"FINISHED"}


def register():
    bpy.utils.register_class(SUB_OP_unstack_uv_islands)


def unregister():
    bpy.utils.unregister_class(SUB_OP_unstack_uv_islands)
