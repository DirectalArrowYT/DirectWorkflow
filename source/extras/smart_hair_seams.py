"""
Smart seams for hair.

Smart UV Project decides where to cut purely from face angle, which is the
wrong instinct for hair. On a solid hair mesh it shatters the surface into
dozens of little angle-bounded patches; on hair cards it keeps each card whole
but rotates every one arbitrarily. Neither gives the long, flow-aligned strips
hair actually wants, and neither guarantees the non-overlapping layout a bake
needs.

Measured on the Shigaraki hair, by rasterizing the actual triangles:

    Hair       1 loose part,   8 boundary edges    0.3% overlap  -  already fine
    Hair.001   1 loose part, 179 boundary edges    4.8% overlap, 23.8% coverage,
                                                   and 14.7% of its faces sitting
                                                   outside the 0-1 tile entirely
    Hair.003 739 loose parts, 15103 boundary      69.5% overlap

Two different failures. Hair.003 is 739 cards sharing one strip of a hair
texture - right for rendering the cards in game, fatal for baking, which needs
every texel to belong to one surface. Hair.001 overlaps far less but spills
across two UV tiles, so a single 0-1 bake silently loses a seventh of it.

Both need re-unwrapping; Hair does not, which is why the operator measures
before it touches anything.

WHAT THIS DOES
  Three passes, each aimed at a structure hair actually has.

  1. Creases. An edge whose two faces meet at more than the angle threshold is
     marked. Restricted to concave edges by default, because on hair the
     valleys are where strands separate - convex ridges are the middle of a
     strand and cutting there puts a seam down the visible face of it.

  2. Boundaries. Open edges are already cuts as far as unwrapping is
     concerned; marking them makes that visible and stops later edits from
     quietly re-joining a card to its neighbour.

  3. Closed strands. A loose part with no open edge is a tube, and no amount
     of crease marking will let it lie flat - it has to be cut end to end.
     The two ends come from the part's own long axis, and the cut is the
     cheapest edge path between them, with the cost biased to run along the
     strand and to prefer concave edges so the seam lands in a valley rather
     than across the visible length of the strand.

Marking seams is all this does by default. Unwrapping and packing are offered
as follow-up options because seams alone will not fix an already-stacked UV
map - the measured overlap above stays exactly where it is until something
re-unwraps.
"""

import heapq
from math import radians

import bmesh
import bpy
from bpy.types import Operator
from bpy.props import BoolProperty, FloatProperty, EnumProperty, IntProperty
from mathutils import Vector


def uv_overlap(obj, resolution=512):
    """(coverage, overlap, outside) for a mesh's active UV map, as fractions.

    Overlap is the share of used texels covered by more than one triangle -
    the thing that makes a bake unusable, since a texel claimed twice can only
    hold one surface's result.

    Outside is the share of faces reaching beyond the 0-1 tile. It is reported
    separately because it is a second, independent way for a UV map to be
    unbakeable, and one that overlap cannot see: a map can be perfectly
    non-overlapping and still lose everything sitting in the next tile along,
    since a bake only ever fills 0-1. This character's Hair.001 measured 0%
    overlap with 14.7% of its faces outside the tile.

    Triangles are rasterized properly, by barycentric test. Testing face
    bounding boxes instead is far easier and completely wrong here: on hair,
    where islands are small and numerous, neighbouring bounding boxes overlap
    constantly even when no triangle does. That shortcut reported this
    character's hair at 45% overlap when the real figure was 0.3%.
    """
    import numpy as np

    mesh = obj.data
    if not mesh.uv_layers:
        return 0.0, 0.0, 0.0

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        bm.free()
        return 0.0, 0.0, 0.0

    outside_faces = 0
    coverage = np.zeros((resolution, resolution), dtype=np.int32)
    for face in bm.faces:
        us = [loop[uv_layer].uv.x for loop in face.loops]
        vs = [loop[uv_layer].uv.y for loop in face.loops]
        if min(us) < -0.001 or max(us) > 1.001 or min(vs) < -0.001 or max(vs) > 1.001:
            outside_faces += 1
        points = np.array([[loop[uv_layer].uv.x * resolution,
                            loop[uv_layer].uv.y * resolution] for loop in face.loops])
        if len(points) != 3:
            continue
        x0 = max(0, int(np.floor(points[:, 0].min())))
        x1 = min(resolution - 1, int(np.ceil(points[:, 0].max())))
        y0 = max(0, int(np.floor(points[:, 1].min())))
        y1 = min(resolution - 1, int(np.ceil(points[:, 1].max())))
        if x1 < x0 or y1 < y0:
            continue

        grid_x, grid_y = np.meshgrid(np.arange(x0, x1 + 1) + 0.5,
                                     np.arange(y0, y1 + 1) + 0.5)
        edge0 = points[1] - points[0]
        edge1 = points[2] - points[0]
        determinant = edge0[0] * edge1[1] - edge1[0] * edge0[1]
        if abs(determinant) < 1e-12:
            continue
        offset_x = grid_x - points[0, 0]
        offset_y = grid_y - points[0, 1]
        bary_a = (offset_x * edge1[1] - edge1[0] * offset_y) / determinant
        bary_b = (edge0[0] * offset_y - offset_x * edge0[1]) / determinant
        inside = (bary_a >= 0) & (bary_b >= 0) & (bary_a + bary_b <= 1)
        if inside.any():
            coverage[y0:y1 + 1, x0:x1 + 1][inside] += 1

    face_count = len(bm.faces)
    bm.free()
    outside = outside_faces / face_count if face_count else 0.0
    used = int((coverage > 0).sum())
    if used == 0:
        return 0.0, 0.0, outside
    return (used / (resolution * resolution),
            int((coverage > 1).sum()) / used,
            outside)


def uv_islands(bm, uv_layer):
    """face index -> island id, where an island is faces joined by shared UV corners.

    This is the same definition the packer uses, which is the point: an island is what can be
    moved as a unit, so it is also the unit that self-overlap has to be measured against.
    """
    parent = {face.index: face.index for face in bm.faces}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    for edge in bm.edges:
        if len(edge.link_faces) != 2:
            continue
        first, second = edge.link_faces

        def corner(face, vert):
            for loop in face.loops:
                if loop.vert == vert:
                    return round(loop[uv_layer].uv.x, 6), round(loop[uv_layer].uv.y, 6)
            return None

        if all(corner(first, v) == corner(second, v) for v in edge.verts):
            union(first.index, second.index)
    return {face.index: find(face.index) for face in bm.faces}


def self_overlapping_faces(obj, resolution=1024):
    """Faces that cover a texel already claimed by another face of their OWN island.

    Overlap between two islands is a packing problem and the packer solves it. Overlap inside
    one island is a folded unwrap, and no packer can touch it — moving the island moves the
    fold with it. Measured on this character's Hair.003, every single overlapped texel left
    after packing was of this second kind, which is why raising the pack margin from 0.003 to
    0.02 cut coverage from 16.1% to 1.8% and left overlap exactly where it was.

    Rasterized rather than compared analytically, for the reason given in `uv_overlap`:
    bounding-box tests on hair report overlap that is not there.
    """
    import numpy as np

    mesh = obj.data
    if not mesh.uv_layers:
        return set()

    bm = bmesh.new()
    bm.from_mesh(mesh)
    uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        bm.free()
        return set()

    # Triangulating would renumber faces, so the caller could not act on the result. Fan the
    # loops instead and keep every triangle attributed to the face it came from.
    islands = uv_islands(bm, uv_layer)
    owner = np.full((resolution, resolution), -1, np.int32)
    culprits: set[int] = set()

    for face in bm.faces:
        island = islands[face.index]
        points = [(loop[uv_layer].uv.x * resolution, loop[uv_layer].uv.y * resolution)
                  for loop in face.loops]
        for i in range(1, len(points) - 1):
            tri = np.array([points[0], points[i], points[i + 1]])
            x0 = max(0, int(np.floor(tri[:, 0].min())))
            x1 = min(resolution - 1, int(np.ceil(tri[:, 0].max())))
            y0 = max(0, int(np.floor(tri[:, 1].min())))
            y1 = min(resolution - 1, int(np.ceil(tri[:, 1].max())))
            if x1 < x0 or y1 < y0:
                continue
            grid_x, grid_y = np.meshgrid(np.arange(x0, x1 + 1) + 0.5,
                                         np.arange(y0, y1 + 1) + 0.5)
            edge0 = tri[1] - tri[0]
            edge1 = tri[2] - tri[0]
            determinant = edge0[0] * edge1[1] - edge1[0] * edge0[1]
            if abs(determinant) < 1e-12:
                continue
            offset_x = grid_x - tri[0, 0]
            offset_y = grid_y - tri[0, 1]
            bary_a = (offset_x * edge1[1] - edge1[0] * offset_y) / determinant
            bary_b = (edge0[0] * offset_y - offset_x * edge0[1]) / determinant
            inside = (bary_a >= 0) & (bary_b >= 0) & (bary_a + bary_b <= 1)
            if not inside.any():
                continue
            window = owner[y0:y1 + 1, x0:x1 + 1]
            if (inside & (window == island)).any():
                culprits.add(face.index)
            window[inside & (window == -1)] = island

    bm.free()
    return culprits


def detach_faces(obj, face_indices):
    """Break the given faces out of their islands by moving their UVs somewhere unique.

    Translation only — the face keeps its exact shape and size, it just stops sharing corners
    with its neighbours, which is the whole definition of a separate island. The parking spot
    is outside 0-1 and does not matter: the pack that follows brings everything back in. A
    lone triangle cannot overlap itself, so once a folded face is on its own the fold is gone.
    """
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        bm.free()
        return 0

    bm.faces.ensure_lookup_table()
    moved = 0
    for offset, index in enumerate(sorted(face_indices)):
        if index >= len(bm.faces):
            continue
        face = bm.faces[index]
        shift = Vector((2.0 + offset * 1.5, 0.0))
        for loop in face.loops:
            loop[uv_layer].uv = loop[uv_layer].uv + shift
        moved += 1

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return moved


def _iter_target_meshes(context):
    """Selected mesh objects, plus meshes under a selected armature."""
    seen = set()
    objects = list(context.selected_objects)
    active = context.view_layer.objects.active
    if active is not None and active not in objects:
        objects.append(active)

    for obj in objects:
        if obj is None:
            continue
        if obj.type == 'MESH' and obj.data not in seen:
            seen.add(obj.data)
            yield obj
        elif obj.type == 'ARMATURE':
            for child in obj.children_recursive:
                if child.type == 'MESH' and child.data not in seen:
                    seen.add(child.data)
                    yield child


def _is_concave(edge):
    """True when an edge is a valley rather than a ridge.

    Winding-independent: it compares the step from one face's centre to the
    other against the first face's normal. Stepping outward along the normal
    means the surface folds back on itself, which is a valley.
    """
    face_a, face_b = edge.link_faces[0], edge.link_faces[1]
    between = face_b.calc_center_median() - face_a.calc_center_median()
    return between.dot(face_a.normal) > 0.0


def _loose_parts(bm):
    """Vertex sets for each connected component."""
    bm.verts.ensure_lookup_table()
    seen = set()
    parts = []
    for vert in bm.verts:
        if vert.index in seen:
            continue
        component = []
        stack = [vert]
        while stack:
            current = stack.pop()
            if current.index in seen:
                continue
            seen.add(current.index)
            component.append(current)
            for edge in current.link_edges:
                other = edge.other_vert(current)
                if other.index not in seen:
                    stack.append(other)
        parts.append(component)
    return parts


def _long_axis(verts):
    """The direction a strand runs, and its two extreme vertices.

    Power iteration on the covariance matrix rather than a full SVD - the
    dominant axis is the only one needed, and this keeps the module free of a
    numpy dependency it would otherwise only use here.
    """
    centre = Vector((0.0, 0.0, 0.0))
    for vert in verts:
        centre += vert.co
    centre /= len(verts)

    axis = Vector((1.0, 0.0, 0.0))
    for _ in range(24):
        accumulated = Vector((0.0, 0.0, 0.0))
        for vert in verts:
            offset = vert.co - centre
            accumulated += offset * offset.dot(axis)
        if accumulated.length < 1e-12:
            break
        accumulated.normalize()
        if (accumulated - axis).length < 1e-7:
            axis = accumulated
            break
        axis = accumulated

    lowest = min(verts, key=lambda v: (v.co - centre).dot(axis))
    highest = max(verts, key=lambda v: (v.co - centre).dot(axis))
    return axis, lowest, highest


def _cheapest_path(start, end, axis, concave_bias):
    """Edges on the cheapest path between two verts, biased along the axis.

    Cost is edge length scaled up for running across the strand rather than
    along it, and scaled down for concave edges. The effect is a cut that
    follows the strand lengthwise and sits in a valley, which is where a seam
    on hair is least visible.
    """
    distances = {start: 0.0}
    previous = {}
    queue = [(0.0, start.index, start)]
    visited = set()

    while queue:
        cost, _, vert = heapq.heappop(queue)
        if vert in visited:
            continue
        visited.add(vert)
        if vert is end:
            break

        for edge in vert.link_edges:
            other = edge.other_vert(vert)
            if other in visited:
                continue

            direction = other.co - vert.co
            length = direction.length
            if length < 1e-12:
                continue

            alignment = abs(direction.normalized().dot(axis))
            weight = length * (1.0 + 3.0 * (1.0 - alignment))
            if concave_bias and len(edge.link_faces) == 2 and _is_concave(edge):
                weight *= 0.4

            candidate = cost + weight
            if candidate < distances.get(other, float('inf')):
                distances[other] = candidate
                previous[other] = (vert, edge)
                heapq.heappush(queue, (candidate, other.index, other))

    if end not in previous and end is not start:
        return []

    path = []
    cursor = end
    while cursor in previous:
        prior, edge = previous[cursor]
        path.append(edge)
        cursor = prior
    return path


def mark_smart_seams(bm, settings):
    """Run the three passes. Returns a counts dict for reporting."""
    counts = {'crease': 0, 'boundary': 0, 'strand_cuts': 0, 'cleared': 0}

    if settings['clear_existing']:
        for edge in bm.edges:
            if edge.seam:
                edge.seam = False
                counts['cleared'] += 1

    threshold = settings['angle']

    for edge in bm.edges:
        if edge.is_boundary:
            if settings['mark_boundaries'] and not edge.seam:
                edge.seam = True
                counts['boundary'] += 1
            continue

        if len(edge.link_faces) != 2:
            # A non-manifold edge cannot be reasoned about as a crease, and it
            # already behaves as a cut, so leave it be.
            continue

        if edge.calc_face_angle(0.0) < threshold:
            continue
        if settings['concave_only'] and not _is_concave(edge):
            continue
        if not edge.seam:
            edge.seam = True
            counts['crease'] += 1

    if settings['cut_closed_strands']:
        for verts in _loose_parts(bm):
            if len(verts) < settings['min_strand_verts']:
                continue
            # An open part can already be flattened; only sealed tubes need a
            # cut, and cutting an open one just adds a seam for nothing.
            if any(e.is_boundary for v in verts for e in v.link_edges):
                continue

            axis, low, high = _long_axis(verts)
            if low is high:
                continue
            for edge in _cheapest_path(low, high, axis, settings['concave_only']):
                if not edge.seam:
                    edge.seam = True
            counts['strand_cuts'] += 1

    return counts


def pack_all(context, obj, margin):
    """Select everything and pack it into 0-1. Assumes the object is already active."""
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.select_all(action='SELECT')
    bpy.ops.uv.pack_islands(
        rotate=True, rotate_method='ANY', scale=True,
        shape_method='CONCAVE', margin_method='SCALED', margin=margin)
    bpy.ops.object.mode_set(mode='OBJECT')


def resolve_overlaps(context, obj, margin, max_passes=3, resolution=1024):
    """Second pass: take the faces still overlapping their own island and re-pack them apart.

    Returns (before, after, detached) as overlap fractions and a face count.

    Repeated because detaching changes the layout: the pack that follows can slide an island
    into a spot that reveals a fold the previous rasterization could not see. It converges
    quickly and stops early when a pass finds nothing or stops improving.
    """
    view_layer = context.view_layer
    previous_active = view_layer.objects.active
    previous_selection = list(context.selected_objects)

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    view_layer.objects.active = obj

    _coverage, before, _outside = uv_overlap(obj, resolution)
    detached = 0
    try:
        for _ in range(max_passes):
            culprits = self_overlapping_faces(obj, resolution)
            if not culprits:
                break
            detached += detach_faces(obj, culprits)
            pack_all(context, obj, margin)
    finally:
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        for previous in previous_selection:
            if previous.name in view_layer.objects:
                previous.select_set(True)
        if previous_active is not None:
            view_layer.objects.active = previous_active

    _coverage, after, _outside = uv_overlap(obj, resolution)
    return before, after, detached


def align_islands_upright(obj, flat_below=0.02):
    """Turn every UV island so the mesh's world up runs up the V axis.

    Smash's anisotropic hair shader takes the highlight direction from the UV
    layout, so a strand has to run top-to-bottom in UV space the way it does
    on the model - the root at the top of its island, the tip at the bottom.
    An unwrap or a pack that rotates freely leaves islands at any angle.

    "Up" for an island is the direction world Z increases across its UVs, from
    a least-squares plane z = a*u + b*v + c over its corners. Fitting the whole
    island rather than taking two extreme points keeps a curled strand from
    being judged by its tip alone. Pure rotation about the island's centre:
    mirroring would flip the tangent basis and with it the normal map.

    An island whose height barely changes - a flat cap on top of the head -
    has no meaningful up, so anything with less vertical relief than
    `flat_below` of its own size is left alone rather than spun at random.

    Returns (turned, already_upright, flat).
    """
    import math
    import numpy as np

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        bm.free()
        return 0, 0, 0

    world = {v.index: obj.matrix_world @ v.co for v in bm.verts}
    island_of = uv_islands(bm, uv_layer)
    islands = {}
    for face in bm.faces:
        islands.setdefault(island_of[face.index], []).append(face)

    turned = upright = flat = 0
    for faces in islands.values():
        loops = [loop for face in faces for loop in face.loops]
        uv = np.array([loop[uv_layer].uv[:] for loop in loops], dtype=np.float64)
        pos = np.array([world[loop.vert.index][:] for loop in loops], dtype=np.float64)
        extent = float(np.ptp(pos, axis=0).max())
        if extent <= 0.0 or float(np.ptp(pos[:, 2])) < flat_below * extent:
            flat += 1
            continue
        design = np.c_[uv, np.ones(len(uv))]
        (a, b, _c), *_ = np.linalg.lstsq(design, pos[:, 2], rcond=None)
        if math.hypot(a, b) < 1e-9:
            flat += 1
            continue
        angle = math.pi / 2 - math.atan2(b, a)
        angle = (angle + math.pi) % (2 * math.pi) - math.pi
        if abs(angle) < 1e-4:
            upright += 1
            continue
        c, s = math.cos(angle), math.sin(angle)
        centre = (uv.min(axis=0) + uv.max(axis=0)) / 2
        turned_uv = (uv - centre) @ np.array([[c, s], [-s, c]]) + centre
        for loop, (u, v) in zip(loops, turned_uv):
            loop[uv_layer].uv = (u, v)
        turned += 1

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return turned, upright, flat


def pack_upright(context, obj, margin):
    """Pack into 0-1 without rotating anything. Assumes the object is active."""
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.select_all(action='SELECT')
    bpy.ops.uv.pack_islands(
        rotate=False, scale=True,
        shape_method='CONCAVE', margin_method='SCALED', margin=margin)
    bpy.ops.object.mode_set(mode='OBJECT')


class SUB_OP_uv_align_upright(Operator):
    """Stand every UV island upright: the top of the mesh at the top of the UV"""
    bl_idname = 'sub.uv_align_upright'
    bl_label = 'Align UVs Upright (Hair)'
    bl_description = (
        'Rotate each UV island so the part of the mesh that is higher up sits higher in UV '
        'space - roots at the top, tips at the bottom. Smash\'s anisotropic hair material '
        'reads its highlight direction from the UVs and needs this. Re-packs without '
        'rotation afterwards so nothing overlaps'
    )
    bl_options = {'REGISTER', 'UNDO'}

    pack_after: BoolProperty(
        name='Re-pack (No Rotation)',
        description=(
            'Turning islands in place makes them overlap and leave the 0-1 tile. Pack them '
            'again with rotation disabled, which keeps them upright'
        ),
        default=True,
    )
    pack_margin: FloatProperty(
        name='Pack Margin',
        description='Gap between packed islands. Keep small; the margin is per island',
        default=0.003, min=0.0, max=0.1,
    )
    flat_below: FloatProperty(
        name='Flat Below',
        description=(
            'Leave an island alone when its height changes by less than this share of its '
            'size - a flat piece has no top or bottom to align to'
        ),
        default=0.02, min=0.0, max=0.5, subtype='FACTOR',
    )

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'EDIT_MESH'} and context.selected_objects

    def execute(self, context):
        started_in_edit = context.mode == 'EDIT_MESH'
        if started_in_edit:
            bpy.ops.object.mode_set(mode='OBJECT')

        meshes = [obj for obj in _iter_target_meshes(context) if obj.data.uv_layers]
        if not meshes:
            self.report({'WARNING'}, 'No mesh objects with a UV map selected.')
            return {'CANCELLED'}

        view_layer = context.view_layer
        previous_active = view_layer.objects.active
        previous_selection = list(context.selected_objects)
        totals = [0, 0, 0]
        overlaps = []
        try:
            for obj in meshes:
                counts = align_islands_upright(obj, self.flat_below)
                totals = [t + c for t, c in zip(totals, counts)]
                if self.pack_after:
                    bpy.ops.object.select_all(action='DESELECT')
                    obj.select_set(True)
                    view_layer.objects.active = obj
                    pack_upright(context, obj, self.pack_margin)
                    overlaps.append(uv_overlap(obj, 512)[1])
        finally:
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.object.select_all(action='DESELECT')
            for previous in previous_selection:
                if previous.name in view_layer.objects:
                    previous.select_set(True)
            if previous_active is not None:
                view_layer.objects.active = previous_active

        if started_in_edit:
            bpy.ops.object.mode_set(mode='EDIT')

        turned, upright, flat = totals
        message = (f'{len(meshes)} mesh(es): {turned} island(s) turned upright, '
                   f'{upright} already were')
        if flat:
            message += f', {flat} flat left as-is'
        if overlaps:
            message += f'; overlap after re-pack {max(overlaps):.2%}'
        self.report({'INFO'}, message)
        return {'FINISHED'}


class SUB_OP_uv_resolve_overlaps(Operator):
    """Clear overlapping UVs by moving the offenders into free space"""
    bl_idname = 'sub.uv_resolve_overlaps'
    bl_label = 'Resolve UV Overlaps'
    bl_description = (
        'Second pass for an already-unwrapped mesh. Finds faces that overlap another face of '
        'their own island - a folded unwrap, which no amount of packing can fix - splits them '
        'off and re-packs everything so nothing overlaps'
    )
    bl_options = {'REGISTER', 'UNDO'}

    pack_margin: FloatProperty(
        name='Pack Margin',
        description='Gap between packed islands. Keep small; the margin is per island',
        default=0.003, min=0.0, max=0.1,
    )
    max_passes: IntProperty(
        name='Max Passes',
        description=(
            'Detach-and-repack rounds. Each pack can reveal a fold the previous pass could '
            'not see, so a second round usually finishes what the first started'
        ),
        default=3, min=1, max=8,
    )
    resolution: IntProperty(
        name='Test Resolution',
        description=(
            'Texel grid the overlap test rasterizes to. Higher finds smaller overlaps and '
            'costs more time'
        ),
        default=1024, min=256, max=4096,
    )

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'EDIT_MESH'} and context.selected_objects

    def execute(self, context):
        started_in_edit = context.mode == 'EDIT_MESH'
        if started_in_edit:
            bpy.ops.object.mode_set(mode='OBJECT')

        meshes = [obj for obj in _iter_target_meshes(context) if obj.data.uv_layers]
        if not meshes:
            self.report({'WARNING'}, 'No mesh objects with a UV map selected.')
            return {'CANCELLED'}

        print('\n' + '=' * 66)
        print('Resolve UV Overlaps')
        print('=' * 66)
        print(f'{"mesh":20s} {"overlap":>20s} {"faces detached":>18s}')
        total_detached = 0
        cleared = 0
        for obj in meshes:
            before, after, detached = resolve_overlaps(
                context, obj, self.pack_margin, self.max_passes, self.resolution)
            total_detached += detached
            if after <= 1e-9:
                cleared += 1
            print(f'{obj.name:20s} {before:8.3%} ->{after:9.3%} {detached:18d}')
        print('=' * 66 + '\n')

        if started_in_edit:
            bpy.ops.object.mode_set(mode='EDIT')

        self.report(
            {'INFO'},
            f'{len(meshes)} mesh(es): {total_detached} face(s) moved to free space, '
            f'{cleared} now fully non-overlapping')
        return {'FINISHED'}


class SUB_OP_smart_hair_seams(Operator):
    """Mark seams suited to hair, where Smart UV Project falls down"""
    bl_idname = 'sub.smart_hair_seams'
    bl_label = 'Smart Seams (Hair)'
    bl_description = (
        'Mark UV seams using hair structure rather than face angle alone: '
        'concave creases where strands separate, open boundaries, and a '
        'lengthwise cut through any closed strand. Optionally unwrap and pack '
        'afterwards, which is what actually clears stacked UVs'
    )
    bl_options = {'REGISTER', 'UNDO'}

    # Stored in radians, which is what subtype 'ANGLE' expects and what
    # bmesh's calc_face_angle returns - so the value goes straight through with
    # no conversion anywhere.
    angle: FloatProperty(
        name='Crease Angle',
        description=(
            'Mark an edge when its two faces meet at more than this angle. '
            'Lower cuts hair into more, smaller strips'
        ),
        default=radians(40.0), min=radians(1.0), max=radians(180.0),
        subtype='ANGLE',
    )
    concave_only: BoolProperty(
        name='Valleys Only',
        description=(
            'Only mark concave creases. On hair the valleys are where strands '
            'separate; a convex ridge is the middle of a strand, and a seam '
            'there runs down the visible face of it'
        ),
        default=True,
    )
    mark_boundaries: BoolProperty(
        name='Mark Open Edges',
        description=(
            'Also mark edges that have no second face. They already act as '
            'cuts - marking them keeps that explicit'
        ),
        default=True,
    )
    cut_closed_strands: BoolProperty(
        name='Cut Closed Strands',
        description=(
            'Find loose parts with no open edge - tubes - and cut each one end '
            'to end so it can lie flat. Without this a closed strand cannot be '
            'unwrapped at all'
        ),
        default=True,
    )
    clear_existing: BoolProperty(
        name='Clear Existing Seams',
        description='Remove seams already on the mesh before marking',
        default=True,
    )

    then: EnumProperty(
        name='Then',
        description='What to do once the seams are marked',
        items=(
            ('NOTHING', 'Just Mark Seams',
             'Leave the UVs alone so you can look at the seams first'),
            ('UNWRAP', 'Unwrap',
             'Unwrap along the new seams, without repacking'),
            ('UNWRAP_PACK', 'Unwrap + Pack',
             'Unwrap, even out texel density, and pack into 0-1 with a margin. '
             'This is what clears stacked UVs and makes the mesh bakeable'),
        ),
        default='UNWRAP_PACK',
    )
    unwrap_method: EnumProperty(
        name='Unwrap Method',
        description='How the surface is flattened once the seams are marked',
        items=(
            ('MINIMUM_STRETCH', 'Minimum Stretch',
             'Best for baking. Every face keeps real texel area, so nothing '
             'bakes to nothing'),
            ('ANGLE_BASED', 'Angle Based',
             'Blender\'s usual default. Faster, but on dense hair it squashes '
             'a large share of faces down to almost no texels'),
            ('CONFORMAL', 'Conformal',
             'Preserves angles at the cost of area. Rarely the right pick for '
             'a bake'),
        ),
        default='MINIMUM_STRETCH',
    )
    pack_margin: FloatProperty(
        name='Pack Margin',
        description=(
            'Gap between packed islands, to stop texture bleed. Keep this '
            'small - the margin is applied per island, so on hair with '
            'hundreds of cards a seemingly modest value eats the whole UV '
            'space (0.02 measured 1.9% coverage against 25.2% at 0.002)'
        ),
        default=0.003, min=0.0, max=0.1,
    )
    resolve_overlaps: BoolProperty(
        name='Resolve Remaining Overlaps',
        description=(
            'Second pass after packing. Packing can only separate whole islands, so a folded '
            'island keeps overlapping itself no matter how it is placed - measured on this '
            'character\'s Hair.003, every overlapped texel left after packing was of that '
            'kind. This splits those faces off and re-packs them into free space (1.11% '
            'overlap down to 0.003%, for 1.4% of faces detached and about 2% of coverage)'
        ),
        default=True,
    )
    skip_if_clean: BoolProperty(
        name='Skip Already-Clean Meshes',
        description=(
            'Leave a mesh alone when its UVs barely overlap already. Re-'
            'unwrapping a good layout only loses texel density - measured on '
            'this character, two of three hair meshes were already under 0.5% '
            'overlap and re-unwrapping them cost about a third of their coverage'
        ),
        default=True,
    )
    clean_threshold: FloatProperty(
        name='Clean Below',
        description='Overlap fraction under which a mesh counts as already clean',
        default=0.05, min=0.0, max=1.0, subtype='FACTOR',
    )

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'EDIT_MESH'} and context.selected_objects

    def draw(self, context):
        layout = self.layout
        column = layout.column(align=True)
        column.prop(self, 'angle')
        column.prop(self, 'concave_only')
        column.prop(self, 'mark_boundaries')
        column.prop(self, 'cut_closed_strands')
        column.prop(self, 'clear_existing')

        layout.separator()
        layout.prop(self, 'then')
        row = layout.row()
        row.enabled = self.then != 'NOTHING'
        row.prop(self, 'unwrap_method')
        row = layout.row()
        row.enabled = self.then == 'UNWRAP_PACK'
        row.prop(self, 'pack_margin')
        row = layout.row()
        row.enabled = self.then == 'UNWRAP_PACK'
        row.prop(self, 'resolve_overlaps')

        guard = layout.column(align=True)
        guard.prop(self, 'skip_if_clean')
        sub = guard.row()
        sub.enabled = self.skip_if_clean
        sub.prop(self, 'clean_threshold')

        if self.then == 'NOTHING':
            box = layout.box()
            box.label(text='Seams alone do not change existing UVs.', icon='INFO')
            box.label(text='Unwrap to actually clear stacked islands.')

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=380)

    def execute(self, context):
        settings = {
            'angle': self.angle,
            'concave_only': self.concave_only,
            'mark_boundaries': self.mark_boundaries,
            'cut_closed_strands': self.cut_closed_strands,
            'clear_existing': self.clear_existing,
            'min_strand_verts': 6,
        }

        started_in_edit = context.mode == 'EDIT_MESH'
        if started_in_edit:
            bpy.ops.object.mode_set(mode='OBJECT')

        totals = {'crease': 0, 'boundary': 0, 'strand_cuts': 0, 'cleared': 0}
        candidates = list(_iter_target_meshes(context))
        if not candidates:
            self.report({'WARNING'}, 'No mesh objects selected.')
            return {'CANCELLED'}

        # Measure first, so a mesh whose UVs are already fine can be left
        # alone and so the report can say what actually changed.
        before = {}
        meshes = []
        skipped = []
        for obj in candidates:
            coverage, overlap, outside = uv_overlap(obj)
            before[obj.name] = (coverage, overlap, outside)
            # Clean means BOTH: barely overlapping and entirely inside the 0-1
            # tile. Checking overlap alone let Hair.001 through untouched -
            # 0% overlap, but a seventh of it parked in the next tile where a
            # bake would never reach it.
            clean = (overlap < self.clean_threshold
                     and outside < self.clean_threshold)
            if self.skip_if_clean and obj.data.uv_layers and clean:
                skipped.append(f'{obj.name} ({overlap:.1%})')
            else:
                meshes.append(obj)

        if not meshes:
            self.report(
                {'INFO'},
                'Nothing to do - already below the clean threshold: '
                + ', '.join(skipped))
            return {'CANCELLED'}

        for obj in meshes:
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            bm.normal_update()
            counts = mark_smart_seams(bm, settings)
            for key in totals:
                totals[key] += counts[key]
            bm.to_mesh(obj.data)
            bm.free()
            obj.data.update()

        message = (f'{len(meshes)} mesh(es): {totals["crease"]} crease seam(s), '
                   f'{totals["boundary"]} boundary, '
                   f'{totals["strand_cuts"]} closed strand(s) cut')

        self._fell_back = []
        if self.then != 'NOTHING':
            unwrapped = self._unwrap(context, meshes)
            if unwrapped:
                message += f'; unwrapped {unwrapped} mesh(es)'
            else:
                message += '; unwrap failed - see console'
            if self._fell_back:
                message += (f'; {len(self._fell_back)} fell back to Angle Based '
                            f'(too many islands for Minimum Stretch)')
            # Only after packing: the pass fixes folds the packer cannot reach, and there is
            # nothing for it to pack into if packing was skipped.
            if unwrapped and self.then == 'UNWRAP_PACK' and self.resolve_overlaps:
                detached = 0
                for obj in meshes:
                    _before, _after, moved = resolve_overlaps(
                        context, obj, self.pack_margin)
                    detached += moved
                if detached:
                    message += f'; {detached} folded face(s) moved to free space'

        if skipped:
            message += f'; skipped {len(skipped)} already-clean'

        # Print the before/after per mesh. Overlap is the number that decides
        # whether a bake will come out usable, and it is not something you can
        # judge by looking at a UV editor full of stacked islands.
        print('\n' + '=' * 66)
        print('Smart Seams (Hair)')
        print('=' * 66)
        print(f'{"mesh":20s} {"coverage":>17s} {"overlap":>17s} {"outside 0-1":>17s}')
        for obj in candidates:
            was_coverage, was_overlap, was_outside = before[obj.name]
            if obj in meshes and self.then != 'NOTHING':
                now_coverage, now_overlap, now_outside = uv_overlap(obj)
                print(f'{obj.name:20s} {was_coverage:6.1%} ->{now_coverage:7.1%} '
                      f'{was_overlap:6.1%} ->{now_overlap:7.1%} '
                      f'{was_outside:6.1%} ->{now_outside:7.1%}')
            else:
                tag = '(seams only)' if obj in meshes else '(skipped)'
                print(f'{obj.name:20s} {was_coverage:6.1%} {tag:>9s} '
                      f'{was_overlap:6.1%} {"":>9s} {was_outside:6.1%}')
        print('=' * 66 + '\n')

        if started_in_edit:
            bpy.ops.object.mode_set(mode='EDIT')

        self.report({'INFO'}, message)
        return {'FINISHED'}

    # A packed unwrap covers a serious share of the UV space. Anything near
    # zero means the solver gave up and produced degenerate coordinates
    # rather than a layout.
    COLLAPSE_COVERAGE = 0.02

    def _unwrap_once(self, context, obj, method):
        """One unwrap-and-pack pass on a single object."""
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.select_all(action='SELECT')

        # no_flip keeps the solver from producing mirrored triangles, which
        # read as overlap because two faces claim the same texels.
        try:
            bpy.ops.uv.unwrap(method=method, margin=0.0, no_flip=True)
        except TypeError:
            bpy.ops.uv.unwrap(method=method, margin=0.0)

        if self.then == 'UNWRAP_PACK':
            # Even density first: packing islands of wildly different texel
            # density just preserves the difference.
            bpy.ops.uv.average_islands_scale()
            # CONCAVE packs to the island's real outline instead of its
            # bounding box. On hair, where islands are long and curved, that
            # is the difference between roughly a third of the UV space used
            # and roughly two thirds - measured 32.8% against 60% on this
            # character's Hair.001.
            bpy.ops.uv.pack_islands(
                rotate=True, rotate_method='ANY', scale=True,
                shape_method='CONCAVE',
                margin_method='SCALED', margin=self.pack_margin)

        bpy.ops.object.mode_set(mode='OBJECT')

    def _unwrap(self, context, meshes):
        """Unwrap, and optionally even out and pack, one mesh at a time.

        Per mesh rather than all at once so each gets its own 0-1 space -
        packing several objects together would share the space between them,
        which is wrong when every material has its own texture set.

        Minimum Stretch is the better solver on a normal hair mesh, but it
        collapses outright on a mesh made of thousands of tiny islands: hair
        cards split into roughly 17,000 of them measured 0% coverage, against
        19.5% from Angle Based on the same mesh. Rather than make the caller
        know which of their meshes is which, the result is measured and the
        pass is redone with Angle Based when the first attempt produced
        nothing.
        """
        view_layer = context.view_layer
        previous_active = view_layer.objects.active
        previous_selection = list(context.selected_objects)
        done = 0
        fell_back = []

        for obj in meshes:
            if not obj.data.uv_layers:
                obj.data.uv_layers.new(name='map1')
            try:
                self._unwrap_once(context, obj, self.unwrap_method)

                coverage, _overlap, _outside = uv_overlap(obj, 256)
                if coverage < self.COLLAPSE_COVERAGE and self.unwrap_method != 'ANGLE_BASED':
                    self._unwrap_once(context, obj, 'ANGLE_BASED')
                    fell_back.append(obj.name)

                done += 1
            except Exception as e:
                print(f'[sub_uv] unwrap failed on {obj.name}: {e}')
                if context.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')

        if fell_back:
            print(f'[sub_uv] {self.unwrap_method} collapsed on '
                  f'{", ".join(fell_back)} - redone with Angle Based.')

        bpy.ops.object.select_all(action='DESELECT')
        for obj in previous_selection:
            if obj.name in context.view_layer.objects:
                obj.select_set(True)
        if previous_active is not None:
            view_layer.objects.active = previous_active
        self._fell_back = fell_back
        return done
