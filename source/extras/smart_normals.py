"""
Smart Normals: shading normals that read well under Smash's lighting.

Imported MHA meshes come with "anime" normals made for cel shading, and
Blender's own normals (from the faces) look faceted and dull: every hair card
shades like a flat strip, and creases on clothing smear into their neighbours.
Per mesh, by material name (or forced):

  HAIR      Volume normals. Each point's normal points away from the weighted
            centre of the hair around it, so the whole mass shades as soft,
            rounded clumps instead of flat cards (both sides of a card shade
            like the volume they belong to). Blended with the mesh's own
            smooth normal by Strength so strands keep some definition.
  SOFT      The same with a wider radius and a lighter blend, for faces: soft,
            even shading without losing the nose/brow shapes.
  WEIGHTED  Clothing, body, props: face-area weighted smooth normals (large
            faces decide the shading, not the thin bevel strips around them),
            with edges sharper than Sharp Angle kept crisp.

The result is written as custom split normals, which is what the Smash model
exporter exports. Faces are set to smooth shading.
"""

import math

import bpy
import numpy as np
from bpy.props import EnumProperty, FloatProperty
from bpy.types import Operator
from mathutils import kdtree

MODE_ITEMS = (
    ('AUTO', "Auto", "Hair for hair/brow/lash materials, Soft for face/head, Weighted for the rest; "
                     "eyes are left alone"),
    ('HAIR', "Hair (Volume)", "Soft, rounded volume shading for hair"),
    ('SOFT', "Soft (Face)", "Lighter volume shading with a wide radius, for faces"),
    ('WEIGHTED', "Weighted", "Face-area weighted smooth normals with sharp creases, for clothing and bodies"),
)

_HAIR_KEYS = ('hair', 'brow', 'lash', 'bang', 'fringe', 'ponytail', 'alp')
_SOFT_KEYS = ('face', 'head')
_SKIP_KEYS = ('eye', 'pupil', 'iris', 'mouth', 'teeth', 'tooth', 'tongue')


def guess_mode(obj):
    names = " ".join([obj.name] + [m.name for m in obj.data.materials if m]).lower()
    if any(k in names for k in _SKIP_KEYS) and not any(k in names for k in _HAIR_KEYS):
        return None
    if any(k in names for k in _HAIR_KEYS):
        return 'HAIR'
    if any(k in names for k in _SOFT_KEYS):
        return 'SOFT'
    return 'WEIGHTED'


# ---------------------------------------------------------------------------
# Mesh helpers (Blender 4.0 - 5.x)
# ---------------------------------------------------------------------------

def _set_all_smooth(mesh):
    values = [True] * len(mesh.polygons)
    mesh.polygons.foreach_set('use_smooth', values)


def _clear_sharp_edges(mesh):
    mesh.edges.foreach_set('use_edge_sharp', [False] * len(mesh.edges))


def _mark_sharp_from_angle(mesh, angle):
    if hasattr(mesh, 'set_sharp_from_angle'):
        mesh.set_sharp_from_angle(angle=angle)
    else:  # Blender 4.0
        mesh.use_auto_smooth = True
        mesh.auto_smooth_angle = angle


def _corner_normals(mesh):
    count = len(mesh.loops)
    out = np.empty(count * 3, dtype=np.float64)
    if hasattr(mesh, 'corner_normals'):
        mesh.corner_normals.foreach_get('vector', out)
    else:
        mesh.calc_normals_split()
        mesh.loops.foreach_get('normal', out)
    return out.reshape(-1, 3)


def _vertex_positions(mesh):
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get('co', co)
    return co.reshape(-1, 3)


def _loop_vertices(mesh):
    idx = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get('vertex_index', idx)
    return idx


def _normalize(v):
    length = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(length, 1e-12)


def _clear_custom_normals(mesh):
    """Back to Blender's own normals. A zero vector means "use the automatic normal"."""
    if mesh.has_custom_normals:
        mesh.normals_split_custom_set([(0.0, 0.0, 0.0)] * len(mesh.loops))
        attribute = mesh.attributes.get('custom_normal')
        if attribute is not None:
            mesh.attributes.remove(attribute)


# ---------------------------------------------------------------------------
# Volume normals
# ---------------------------------------------------------------------------

def _weld(positions, tolerance):
    """(unique points, inverse index): positions closer than `tolerance` share one point, so
    normals stay continuous across UV/card splits."""
    keys = np.round(positions / max(tolerance, 1e-12))
    _unique, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    inverse = inverse.reshape(-1)
    sums = np.zeros((len(first), 3))
    np.add.at(sums, inverse, positions)
    counts = np.bincount(inverse, minlength=len(first))[:, None]
    return sums / counts, inverse


def _kdtree(points):
    tree = kdtree.KDTree(len(points))
    for i, p in enumerate(points):
        tree.insert(p, i)
    tree.balance()
    return tree


def _smooth_field(points, field, tree, radius):
    """Gaussian-weighted average of a unit vector field over `radius`."""
    out = np.zeros_like(field)
    for i, p in enumerate(points):
        found = tree.find_range(p, radius)
        if not found:
            out[i] = field[i]
            continue
        idx = np.fromiter((f[1] for f in found), dtype=np.int64, count=len(found))
        dist = np.fromiter((f[2] for f in found), dtype=np.float64, count=len(found))
        w = np.exp(-2.0 * (dist / radius) ** 2)
        out[i] = (field[idx] * w[:, None]).sum(axis=0)
    return _normalize(out)


def volume_normals(points, radius, max_samples=6000):
    """Per point: away from the Gaussian-weighted centre of the surface around it (within
    `radius`). On a shell or a clump that centre lies inside, so this points outward."""
    rng = np.random.default_rng(0)
    sample = points if len(points) <= max_samples else points[rng.choice(len(points), max_samples, replace=False)]
    tree = _kdtree(sample)
    result = np.zeros_like(points)
    for i, p in enumerate(points):
        found = tree.find_range(p, radius)
        if len(found) < 3:
            continue
        idx = np.fromiter((f[1] for f in found), dtype=np.int64, count=len(found))
        dist = np.fromiter((f[2] for f in found), dtype=np.float64, count=len(found))
        w = np.exp(-2.0 * (dist / radius) ** 2)
        result[i] = p - (sample[idx] * w[:, None]).sum(axis=0) / w.sum()
    return _normalize(result)


# Share of each part per mode: (global form, local clumps, the mesh's own normal).
_MIX = {
    'HAIR': (0.45, 0.40, 0.15),
    'SOFT': (0.30, 0.20, 0.50),
}


def apply_smart_normals(obj, mode, strength=1.0, radius_factor=0.3, sharp_angle=math.radians(50.0)):
    """Write smart custom normals on a mesh object. Returns the mode used."""
    mesh = obj.data
    if len(mesh.loops) == 0:
        return None
    _clear_custom_normals(mesh)
    _set_all_smooth(mesh)

    if mode == 'WEIGHTED':
        _clear_sharp_edges(mesh)
        _mark_sharp_from_angle(mesh, sharp_angle)
        mesh.update()
        normals = _weighted_corner_normals(obj)
        mesh.normals_split_custom_set([tuple(n) for n in normals])
        return mode

    # HAIR / SOFT
    _clear_sharp_edges(mesh)
    mesh.update()
    own_corner = _normalize(_corner_normals(mesh))
    positions = _vertex_positions(mesh)
    loop_vertex = _loop_vertices(mesh)
    size = float(np.linalg.norm(positions.max(axis=0) - positions.min(axis=0)))
    points, inverse = _weld(positions, size * 1e-4)
    corner_point = inverse[loop_vertex]

    # The mesh's own normal, averaged over welded points. Cards facing opposite ways at the
    # same spot cancel out, which is fine: the volume parts decide there.
    own = np.zeros_like(points)
    np.add.at(own, corner_point, own_corner)
    own = _normalize(own)

    centre = points.mean(axis=0)
    global_form = _normalize(points - centre)
    local = volume_normals(points, max(size * radius_factor, 1e-6))
    g, l, _o = _MIX[mode]
    volume = _normalize(global_form * g + local * l)
    blend = strength * (g + l)  # the rest is the mesh's own normal
    field = _normalize(volume * blend + own * (1.0 - blend))
    field = _smooth_field(points, field, _kdtree(points), size * 0.04)
    normals = field[corner_point]
    mesh.normals_split_custom_set([tuple(n) for n in normals])
    return mode


def _weighted_corner_normals(obj):
    """Face-area-with-angle weighted normals from a temporary Weighted Normal modifier."""
    modifier = obj.modifiers.new('__SUB_smart_normals', 'WEIGHTED_NORMAL')
    try:
        modifier.mode = 'FACE_AREA_WITH_ANGLE'
        modifier.weight = 60
        modifier.keep_sharp = True
        if hasattr(modifier, 'use_face_influence'):
            modifier.use_face_influence = False
        # Evaluate with only this modifier.
        disabled = []
        for other in obj.modifiers:
            if other is not modifier and other.show_viewport:
                other.show_viewport = False
                disabled.append(other)
        try:
            depsgraph = bpy.context.evaluated_depsgraph_get()
            evaluated = obj.evaluated_get(depsgraph)
            eval_mesh = evaluated.to_mesh()
            try:
                normals = _corner_normals(eval_mesh)
            finally:
                evaluated.to_mesh_clear()
        finally:
            for other in disabled:
                other.show_viewport = True
    finally:
        obj.modifiers.remove(modifier)
    return _normalize(normals)


class SUB_OP_smart_normals(Operator):
    """Give the selected meshes shading normals that read well in game"""
    bl_idname = 'sub.smart_normals'
    bl_label = 'Smart Normals'
    bl_description = (
        'Replace dull or anime-style normals: soft volume normals for hair, lighter ones for faces, '
        'and area-weighted smooth normals with crisp creases for everything else. Written as '
        'custom normals, which the model exporter exports'
    )
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(name='Mode', items=MODE_ITEMS, default='AUTO')
    strength: FloatProperty(
        name='Volume Strength', default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        description='Hair/Soft: how much the volume normal replaces the mesh\'s own smooth normal',
    )
    radius: FloatProperty(
        name='Volume Radius', default=0.3, min=0.02, max=1.0, subtype='FACTOR',
        description='Hair/Soft: size of the clumps that shade as one form, relative to the mesh size. '
                    'Larger is softer',
    )
    sharp_angle: FloatProperty(
        name='Sharp Angle', default=math.radians(50.0), min=0.0, max=math.pi, subtype='ANGLE',
        description='Weighted: edges sharper than this stay crisp',
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(o.type == 'MESH' for o in context.selected_objects)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=340)

    def execute(self, context):
        done, skipped = [], []
        for obj in [o for o in context.selected_objects if o.type == 'MESH']:
            mode = guess_mode(obj) if self.mode == 'AUTO' else self.mode
            if mode is None:
                skipped.append(obj.name)
                continue
            used = apply_smart_normals(obj, mode, self.strength, self.radius, self.sharp_angle)
            if used:
                done.append(f'{obj.name} ({used.title()})')
        message = 'Smart normals: ' + (', '.join(done) or 'nothing changed')
        if skipped:
            message += f'. Left alone (eyes/mouth): {", ".join(skipped)}'
        self.report({'INFO'}, message)
        return {'FINISHED'}
