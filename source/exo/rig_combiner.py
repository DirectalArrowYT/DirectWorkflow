"""
Rig Combiner - fit a game-ready Smash rig to a character's source rig, fold the
source's swing and accessory bones into it, and move the source meshes over.

Replaces the Magic Exo Skel Maker for movesets. An exo skeleton keeps vanilla
proportions and hangs the character off helper bones; a new fighter has its
own animations, so the Smash bones themselves can simply move to where the
character's joints are - which is what "Align" does.

THE THREE STEPS
  1. Auto Pair   Every source bone gets an action:
                   MAP    it IS a Smash bone (head -> Head, L_arm -> ShoulderL).
                          Align moves the Smash bone to it; its weights are
                          renamed to the Smash name.
                   MERGE  a bone Smash has no equivalent for that should come
                          across - accessory / swing chains (AC_*). Added to the
                          Smash rig as S_... so the swing tools and export see it.
                   FOLD   its weights go to another bone and the bone itself
                          stays behind - face bones (the VIS face tool owns
                          expressions), twist bones Smash has no helper for.
                   SKIP   weightless sockets and roots (grab, PlayerIconSocket).
                 All Justice rigs share one naming scheme, so the body is a
                 fixed table (AJ_TO_SMASH) and everything else is rules. The
                 list is editable before anything is changed.
  2. Align       Smash bone heads move onto the paired source heads. Source
                 TAILS are ignored - AJ imports give every bone the same stub
                 tail - so direction comes from the Smash rig: a bone that
                 pointed at its child still does, anything else keeps its
                 direction, and roll is re-solved to keep the Smash bone's own
                 Z axis, so vanilla-derived animation keeps its local frames.
                 Unpaired Smash bones (face bones, LegC, Throw) ride along with
                 their parent, and meshes already on the Smash rig (swing
                 collision shapes) are carried with their bones.
  3. Combine     MERGE bones are added, source meshes get their vertex groups
                 renamed / folded and are rebound to the Smash rig. Face meshes
                 are left deforming from the source rig and only re-parented,
                 which is exactly the setup the VIS face bake expects: it plays
                 the facial action on the modifier's rig and binds the baked
                 meshes to the parent.

The source rig is never modified - the face still needs it.
"""

import re
from collections import defaultdict

import bpy
from bpy.types import Operator, Panel, PropertyGroup, UIList, Object
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, IntProperty,
                       PointerProperty, StringProperty)
from mathutils import Vector

from ..blender_compat import ensure_bone_collection, assign_bone_to_collection


# =============================================================================
# PAIRING RULES
# =============================================================================
def _aj_table():
    table = {
        'root': 'Trans', 'jointroot': 'Rot', 'hip': 'Hip', 'waist': 'Waist',
        'chest': 'Bust', 'neck': 'Neck', 'head': 'Head', 'face': 'Face',
    }
    fingers = (('thumbfinger', 5), ('indexfinger', 1), ('middlefinger', 2),
               ('ringfinger', 3), ('littlefinger', 4))
    for side in 'LR':
        s = side.lower()
        table.update({
            f'{s}_collar': f'Clavicle{side}', f'{s}_arm': f'Shoulder{side}',
            f'{s}_elbow': f'Arm{side}', f'{s}_hand': f'Hand{side}',
            f'{s}_weapon': f'Have{side}',
            f'{s}_leg': f'Leg{side}', f'{s}_knee': f'Knee{side}',
            f'{s}_ankle': f'Foot{side}', f'{s}_toe': f'Toe{side}',
            # AJ's palm bone parents the ring and little fingers, which is
            # what Smash's FingerX30 does.
            f'{s}_fingerbase': f'Finger{side}30',
            # AJ twist/corrective bones onto the Smash helpers in the same spot.
            f'{s}_arm_ex': f'H_Shoulder{side}', f'{s}_elbow_ex': f'H_Arm{side}',
            f'{s}_wrist_ex': f'H_Hand{side}', f'{s}_knee_ex': f'H_Knee{side}',
        })
        for finger, digit in fingers:
            for index, letter in enumerate('abc', start=1):
                table[f'{s}_{finger}_{letter}'] = f'Finger{side}{digit}{index}'
    return table


# All Justice (lowercase) -> Smash. Checked against Dabi (SK_pl17) and Bakugo
# (SK_pl02): body and face bones are identical, only AC_ accessories differ.
AJ_TO_SMASH = _aj_table()

FACIAL_RE = re.compile(r'(eye|lid|brow|lip|mouth|cheek|nose|jaw|chin|teeth|tongue|'
                       r'faceline|forehead|lash)', re.IGNORECASE)
ACCESSORY_PREFIX = 'AC_'
CHAIN_LETTER_RE = re.compile(r'_[A-Z]$')

ACTION_ITEMS = (
    ('MAP', "Map", "This source bone is the chosen Smash bone. Align moves the Smash "
                   "bone here and its weights take the Smash name"),
    ('MERGE', "Merge", "Add this bone to the Smash rig under the given name (swing and "
                       "accessory bones)"),
    ('FOLD', "Fold", "Send its weights to the given bone and leave the bone behind"),
    ('SKIP', "Skip", "Weightless - nothing to carry over"),
)


class Pair:
    def __init__(self, source, action, target='', reason=''):
        self.source, self.action, self.target, self.reason = source, action, target, reason

    def __repr__(self):
        return f"{self.source:24s} {self.action:5s} {self.target:22s} {self.reason}"


def skinned_meshes(rig):
    """Meshes deformed by `rig` through an Armature modifier."""
    return [o for o in bpy.data.objects if o.type == 'MESH' and any(
        m.type == 'ARMATURE' and m.object == rig for m in o.modifiers)]


def weight_stats(meshes):
    """{group name: (weighted vertex count, weighted world-space centroid)}."""
    count = defaultdict(int)
    total = defaultdict(float)
    centre = defaultdict(lambda: Vector((0.0, 0.0, 0.0)))
    for obj in meshes:
        names = {vg.index: vg.name for vg in obj.vertex_groups}
        mw = obj.matrix_world
        for v in obj.data.vertices:
            co = None
            for g in v.groups:
                if g.weight <= 1e-4 or g.group not in names:
                    continue
                name = names[g.group]
                count[name] += 1
                if g.weight >= 0.25:
                    if co is None:
                        co = mw @ v.co
                    centre[name] += co * g.weight
                    total[name] += g.weight
    return {n: (count[n], (centre[n] / total[n]) if total[n] else None) for n in count}


def _descendants(bone):
    for child in bone.children:
        yield child
        yield from _descendants(child)


def merged_name(source_name, swing_prefix='S_', rigid_prefix='H_'):
    """AC_LB_slirt_A -> S_LB_slirt_A. A lettered chain is swing; a lone
    accessory (AC_L_grenade) is a rigid extra bone."""
    base = source_name[len(ACCESSORY_PREFIX):] if source_name.startswith(ACCESSORY_PREFIX) else source_name
    return (swing_prefix if CHAIN_LETTER_RE.search(source_name) else rigid_prefix) + base


def auto_pair(source_rig, smash_rig, swing_prefix='S_'):
    """Decide an action for every source bone. Returns [Pair], in source bone order."""
    smash_names = {b.name for b in smash_rig.data.bones}
    smash_lower = {n.lower(): n for n in smash_names}
    stats = weight_stats(skinned_meshes(source_rig))
    bones = source_rig.data.bones
    pairs = {}

    def weighted(bone):
        return stats.get(bone.name, (0, None))[0] > 0

    # --- the fixed table, plus exact name matches for non-AJ rigs ------------------
    for bone in bones:
        target = AJ_TO_SMASH.get(bone.name.lower()) or smash_lower.get(bone.name.lower())
        if target in smash_names:
            pairs[bone.name] = Pair(bone.name, 'MAP', target, 'All Justice table'
                                    if bone.name.lower() in AJ_TO_SMASH else 'same name')

    # --- face: everything under the bone mapped to Face -------------------------------
    face_roots = [b for b in bones if pairs.get(b.name) and pairs[b.name].target == 'Face']
    facial = set()
    for root in face_roots:
        facial.update(d.name for d in _descendants(root)
                      if not d.name.startswith(ACCESSORY_PREFIX))
    head_root = next((b for b in bones if pairs.get(b.name) and pairs[b.name].target == 'Head'), None)
    if head_root is not None:
        facial.update(d.name for d in _descendants(head_root)
                      if FACIAL_RE.search(d.name) and not d.name.startswith(ACCESSORY_PREFIX))
    try:
        from ..extras.vis_mesh_bake import REMAP_SUGGESTIONS
    except Exception:
        REMAP_SUGGESTIONS = {}
    for name in facial:
        if name in pairs:
            continue
        suggestion = REMAP_SUGGESTIONS.get(name.lower())
        if suggestion in smash_names:
            pairs[name] = Pair(name, 'FOLD', suggestion, 'face bone (VIS face tool pairing)')
        else:
            pairs[name] = Pair(name, 'FOLD', '', 'face bone - left to the VIS face tool')

    # --- accessories -> merged swing / rigid bones -------------------------------------
    # Weightless ones too: AJ ends a chain on an unweighted tip (AC_L_eri_B),
    # and that tip is what gives the last weighted bone its length.
    for bone in bones:
        if bone.name in pairs or not bone.name.startswith(ACCESSORY_PREFIX):
            continue
        if bone.name.endswith('_EX') and bones.get(bone.name[:-3]) is not None:
            continue  # handled with the other _EX bones below
        name = merged_name(bone.name, swing_prefix)
        kind = 'swing chain' if name.startswith(swing_prefix) else 'rigid accessory'
        pairs[bone.name] = Pair(bone.name, 'MERGE', name, kind)

    # --- twist bones Smash has no helper for -> their base bone ------------------------
    for bone in bones:
        if bone.name in pairs or not bone.name.endswith('_EX'):
            continue
        base = bones.get(bone.name[:-3])
        if base is not None:
            pairs[bone.name] = Pair(bone.name, 'FOLD', '', f'twist bone -> {base.name}')

    # --- leftovers: weighted ones find a home, weightless ones are dropped -------------
    taken = {p.target for p in pairs.values() if p.action == 'MAP'}
    height = max((b.head_local.z for b in bones), default=1.0) or 1.0
    for bone in bones:
        if bone.name in pairs:
            continue
        if not weighted(bone):
            pairs[bone.name] = Pair(bone.name, 'SKIP', '', 'no weights')
            continue
        head = smash_rig.matrix_world.inverted() @ (source_rig.matrix_world @ bone.head_local)
        best, best_d = None, height * 0.02
        for sb in smash_rig.data.bones:
            if sb.name in taken or not sb.use_deform:
                continue
            d = (sb.head_local - head).length
            if d < best_d:
                best, best_d = sb.name, d
        if best:
            pairs[bone.name] = Pair(bone.name, 'MAP', best, 'nearest by position - check')
            taken.add(best)
        else:
            pairs[bone.name] = Pair(bone.name, 'FOLD', '', 'no Smash match - weights to parent')

    ordered = [pairs[b.name] for b in bones]
    resolve_fold_targets(ordered, source_rig, smash_rig)
    return ordered


def resolve_fold_targets(pairs, source_rig, smash_rig):
    """Fill every empty FOLD target with the nearest kept ancestor's final name."""
    by_name = {p.source: p for p in pairs}
    root = next((b.name for b in smash_rig.data.bones if b.parent is None), '')
    for p in pairs:
        if p.action != 'FOLD' or p.target:
            continue
        bone = source_rig.data.bones.get(p.source)
        if p.source.endswith('_EX'):
            base = by_name.get(p.source[:-3])
            if base is not None and base.action in ('MAP', 'MERGE'):
                p.target = base.target
                continue
        p.target = kept_ancestor(bone, by_name) or root


def kept_ancestor(bone, by_name):
    """Final name of the nearest ancestor that exists on the combined rig."""
    parent = bone.parent if bone is not None else None
    while parent is not None:
        p = by_name.get(parent.name)
        if p is not None and p.action in ('MAP', 'MERGE') and p.target:
            return p.target
        parent = parent.parent
    return None


def final_names(pairs, source_rig, smash_rig):
    """source bone -> the vertex group name it ends up as on the combined rig."""
    by_name = {p.source: p for p in pairs}
    root = next((b.name for b in smash_rig.data.bones if b.parent is None), '')
    out = {}
    for p in pairs:
        if p.action in ('MAP', 'MERGE', 'FOLD') and p.target:
            out[p.source] = p.target
        else:
            out[p.source] = kept_ancestor(source_rig.data.bones.get(p.source), by_name) or root
    return out


def facial_meshes(meshes, pairs, threshold=0.5):
    """Meshes where most vertices mostly follow face bones - the VIS tool's input."""
    facial = {p.source for p in pairs if p.reason.startswith('face bone')}
    out = []
    for obj in meshes:
        names = {vg.index: vg.name for vg in obj.vertex_groups}
        hits = total = 0
        for v in obj.data.vertices:
            best = max(v.groups, key=lambda g: g.weight, default=None)
            if best is None or best.weight <= 0 or best.group not in names:
                continue
            total += 1
            hits += names[best.group] in facial
        if total and hits / total >= threshold:
            out.append(obj)
    return out


# =============================================================================
# EDIT-MODE HELPERS
# =============================================================================
class _EditMode:
    """Put `rig` in edit mode for the block, then restore the previous state."""
    def __init__(self, context, rig):
        self.context, self.rig = context, rig

    def __enter__(self):
        ctx = self.context
        self.prev_active = ctx.view_layer.objects.active
        self.prev_mode = ctx.object.mode if ctx.object else 'OBJECT'
        if ctx.object and ctx.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        self.was_hidden = self.rig.hide_get()
        self.rig.hide_set(False)
        ctx.view_layer.objects.active = self.rig
        bpy.ops.object.mode_set(mode='EDIT')
        return self.rig.data.edit_bones

    def __exit__(self, *exc):
        bpy.ops.object.mode_set(mode='OBJECT')
        self.rig.hide_set(self.was_hidden)
        if self.prev_active is not None:
            self.context.view_layer.objects.active = self.prev_active
        return False


def _top_down(bones):
    out = []

    def walk(bone):
        out.append(bone)
        for child in bone.children:
            walk(child)
    for bone in bones:
        if bone.parent is None:
            walk(bone)
    return out


# =============================================================================
# STEP 2 - ALIGN
# =============================================================================
def align_smash_rig(context, smash_rig, source_rig, pairs, point_at_children=True,
                    carry_meshes=True):
    """Move every mapped Smash bone onto its source bone. Returns a report dict."""
    targets = {p.target: p.source for p in pairs if p.action == 'MAP' and p.target}
    to_smash = smash_rig.matrix_world.inverted() @ source_rig.matrix_world
    src_head = {b.name: to_smash @ b.head_local for b in source_rig.data.bones}
    old_rest = {b.name: b.matrix_local.copy() for b in smash_rig.data.bones}
    carried = [o for o in skinned_meshes(smash_rig)] if carry_meshes else []
    bone_parented = [o for o in bpy.data.objects
                     if carry_meshes and o.parent == smash_rig and o.parent_type == 'BONE']

    moved, missing = [], []
    with _EditMode(context, smash_rig) as ebs:
        order = _top_down(ebs)
        orig = {eb.name: (eb.head.copy(), eb.tail.copy(), eb.z_axis.copy(), eb.use_connect)
                for eb in ebs}
        for eb in ebs:
            eb.use_connect = False

        new_head = {}
        for eb in order:
            head0 = orig[eb.name][0]
            src = targets.get(eb.name)
            if src is not None and src in src_head:
                new_head[eb.name] = src_head[src].copy()
                moved.append(eb.name)
            elif eb.parent is not None:
                new_head[eb.name] = head0 + (new_head[eb.parent.name] - orig[eb.parent.name][0])
            else:
                new_head[eb.name] = head0.copy()
            if src is not None and src not in src_head:
                missing.append(src)

        for eb in order:
            head0, tail0, z0, _ = orig[eb.name]
            vec = tail0 - head0
            head = new_head[eb.name]
            tail = None
            if point_at_children:
                # Only children the bone was already pointing at: Hand and Head
                # point nowhere in particular and must keep their direction.
                reach = max(0.02, 0.05 * vec.length)
                for child in eb.children:
                    if child.name in targets and (orig[child.name][0] - tail0).length <= reach:
                        tail = new_head[child.name]
                        break
            if tail is None or (tail - head).length < 1e-4:
                tail = head + vec
            eb.head, eb.tail = head, tail
            eb.align_roll(z0)

        for eb in ebs:
            if orig[eb.name][3] and eb.parent is not None and (eb.head - eb.parent.tail).length < 1e-5:
                eb.use_connect = True

    new_rest = {b.name: b.matrix_local.copy() for b in smash_rig.data.bones}
    delta = {n: new_rest[n] @ old_rest[n].inverted() for n in new_rest}
    for obj in carried:
        _carry_mesh(obj, smash_rig, delta)
    for obj in bone_parented:
        d = delta.get(obj.parent_bone)
        if d is not None:
            obj.matrix_world = smash_rig.matrix_world @ d @ smash_rig.matrix_world.inverted() @ obj.matrix_world

    try:
        from ..model.import_model import refresh_helper_bone_constraints
        refresh_helper_bone_constraints(smash_rig)
    except Exception:
        pass
    return {'moved': moved, 'missing': missing, 'carried': [o.name for o in carried + bone_parented]}


def _carry_mesh(obj, rig, delta):
    """Move a mesh skinned to `rig` by how its bones' rest poses changed.

    Changing a rest pose leaves skinned geometry where it was, so the swing
    collision shapes would stay at the old joints. Linear blend of each
    bone's rest delta, in armature space."""
    to_arm = rig.matrix_world.inverted() @ obj.matrix_world
    from_arm = to_arm.inverted()
    names = {vg.index: vg.name for vg in obj.vertex_groups}
    for v in obj.data.vertices:
        acc = Vector((0.0, 0.0, 0.0))
        total = 0.0
        p = to_arm @ v.co
        for g in v.groups:
            d = delta.get(names.get(g.group))
            if d is None or g.weight <= 0:
                continue
            acc += (d @ p) * g.weight
            total += g.weight
        if total > 0:
            v.co = from_arm @ (acc / total)
    obj.data.update()


# =============================================================================
# STEP 3 - COMBINE
# =============================================================================
def combine_rigs(context, smash_rig, source_rig, pairs, meshes, face_meshes=()):
    """Add MERGE bones, rename weights, rebind meshes. Returns a report dict."""
    merges = [p for p in pairs if p.action == 'MERGE' and p.target]
    by_name = {p.source: p for p in pairs}
    to_smash = smash_rig.matrix_world.inverted() @ source_rig.matrix_world
    stats = weight_stats(skinned_meshes(source_rig))
    src_bones = source_rig.data.bones
    added, collisions = [], []

    with _EditMode(context, smash_rig) as ebs:
        for p in sorted(merges, key=lambda p: _depth(src_bones.get(p.source))):
            if p.target in ebs:
                collisions.append(p.target)
                continue
            sb = src_bones.get(p.source)
            eb = ebs.new(p.target)
            eb.head = to_smash @ sb.head_local
            eb.tail = eb.head + Vector((0.0, 0.0, 0.1))
            eb.use_deform = True
            parent_name = kept_ancestor(sb, by_name)
            if parent_name and parent_name in ebs:
                eb.parent = ebs[parent_name]
            added.append(p.target)

        # Tails once every head exists - a chain points down itself.
        merged_src = {p.target: src_bones.get(p.source) for p in merges if p.target in added}
        for name in added:
            eb = ebs[name]
            sb = merged_src[name]
            kids = sorted((ebs[by_name[c.name].target] for c in sb.children
                           if by_name.get(c.name) and by_name[c.name].action == 'MERGE'
                           and by_name[c.name].target in added), key=lambda e: e.name)
            parent_merged = eb.parent is not None and eb.parent.name in added
            if kids:
                eb.tail = kids[0].head.copy()
            else:
                centroid = stats.get(sb.name, (0, None))[1]
                direction = None
                length = (eb.parent.head - eb.head).length if parent_merged else 0.0
                if centroid is not None:
                    centroid = smash_rig.matrix_world.inverted() @ centroid
                    if (centroid - eb.head).length > 1e-3:
                        direction = (centroid - eb.head)
                        if not parent_merged:
                            length = direction.length * 2.0
                if direction is None and eb.parent is not None:
                    direction = eb.head - eb.parent.head
                if direction is None or direction.length < 1e-6:
                    direction = Vector((0.0, 0.0, 1.0))
                length = max(length, 0.05)
                eb.tail = eb.head + direction.normalized() * length
            if eb.parent is not None:
                eb.align_roll(eb.parent.z_axis)

    for name in added:
        bone = smash_rig.data.bones[name]
        collection = 'Swing Bones' if name.startswith('S_') else 'Helper Bones'
        assign_bone_to_collection(ensure_bone_collection(smash_rig.data, collection), bone)

    rename = final_names(pairs, source_rig, smash_rig)
    rebound = []
    for obj in meshes:
        remap_vertex_groups(obj, rename)
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE' and mod.object == source_rig:
                mod.object = smash_rig
        _reparent(obj, smash_rig)
        rebound.append(obj.name)
    for obj in face_meshes:
        # Keep deforming from the source rig; the VIS bake binds its output
        # to the parent, which should be the game rig.
        _reparent(obj, smash_rig)

    unknown = sorted({vg.name for obj in meshes for vg in obj.vertex_groups}
                     - {b.name for b in smash_rig.data.bones})
    return {'added': added, 'collisions': collisions, 'rebound': rebound,
            'face_meshes': [o.name for o in face_meshes], 'unknown_groups': unknown}


def _depth(bone):
    d = 0
    while bone is not None and bone.parent is not None:
        bone = bone.parent
        d += 1
    return d


def _reparent(obj, rig):
    world = obj.matrix_world.copy()
    obj.parent = rig
    obj.parent_type = 'OBJECT'
    obj.matrix_world = world


def remap_vertex_groups(obj, rename):
    """Rename and merge vertex groups by `rename` (old -> new). Weights landing
    on the same new group add up, capped at 1."""
    groups = obj.vertex_groups
    index_to_new = {vg.index: rename[vg.name] for vg in groups if vg.name in rename}
    if not index_to_new:
        return 0
    acc = defaultdict(dict)
    for v in obj.data.vertices:
        for g in v.groups:
            new = index_to_new.get(g.group)
            if new is not None and g.weight > 0:
                bucket = acc[new]
                bucket[v.index] = bucket.get(v.index, 0.0) + g.weight
    for vg in [groups[i] for i in index_to_new]:
        groups.remove(vg)
    for new, weights in acc.items():
        vg = groups.get(new) or groups.new(name=new)
        by_value = defaultdict(list)
        for index, w in weights.items():
            by_value[round(min(w, 1.0), 6)].append(index)
        for w, indices in by_value.items():
            vg.add(indices, w, 'ADD')
    return len(index_to_new)


# =============================================================================
# UI DATA
# =============================================================================
class SUB_PG_rig_pair(PropertyGroup):
    # name = the source bone, so the list's own filter searches it
    action: EnumProperty(name='Action', items=ACTION_ITEMS, default='SKIP')
    target: StringProperty(name='Target', description='Smash bone (Map), new bone name '
                                                      '(Merge) or bone that receives the weights (Fold)')
    reason: StringProperty(name='Why')


class SUB_PG_rig_mesh(PropertyGroup):
    obj: PointerProperty(type=Object)
    move: BoolProperty(name='Move', default=True,
                       description='Rename weights and bind to the Smash rig. Off: only re-parent, '
                                   'keep deforming from the source rig (face meshes)')
    note: StringProperty()


class SUB_PG_rig_combiner(PropertyGroup):
    pairs: CollectionProperty(type=SUB_PG_rig_pair)
    pairs_index: IntProperty()
    meshes: CollectionProperty(type=SUB_PG_rig_mesh)
    meshes_index: IntProperty()
    swing_prefix: StringProperty(name='Swing Prefix', default='S_',
                                 description='Smash swing bones must start with S_')
    point_at_children: BoolProperty(
        name='Point Bones at Children', default=True,
        description='A Smash bone that pointed at its child keeps pointing at it after '
                    'the move. Roll keeps the original Z axis either way')
    carry_meshes: BoolProperty(
        name='Carry Smash Rig Meshes', default=True,
        description='Move meshes already on the Smash rig (swing collision shapes) with their bones')


def _combiner(context):
    return context.scene.sub_scene_properties.rig_combiner


def _rigs(context):
    ssp = context.scene.sub_scene_properties
    return ssp.smash_armature, ssp.other_armature


def pairs_from_ui(settings):
    return [Pair(item.name, item.action, item.target, item.reason) for item in settings.pairs]


# =============================================================================
# OPERATORS
# =============================================================================
class SUB_OP_rig_combiner_pair(Operator):
    """Pair every source bone with a Smash bone, a new swing bone, or a bone to fold into"""
    bl_idname = 'sub.rig_combiner_pair'
    bl_label = 'Auto Pair Bones'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        smash, source = _rigs(context)
        return smash is not None and source is not None and smash != source

    def execute(self, context):
        smash, source = _rigs(context)
        settings = _combiner(context)
        pairs = auto_pair(source, smash, settings.swing_prefix)
        settings.pairs.clear()
        for p in pairs:
            item = settings.pairs.add()
            item.name, item.action, item.target, item.reason = p.source, p.action, p.target, p.reason

        meshes = skinned_meshes(source)
        faces = set(facial_meshes(meshes, pairs))
        settings.meshes.clear()
        for obj in meshes:
            item = settings.meshes.add()
            item.obj = obj
            item.move = obj not in faces
            item.note = 'face - stays on source rig for the VIS bake' if obj in faces else ''

        counts = defaultdict(int)
        for p in pairs:
            counts[p.action] += 1
        check = sum(1 for p in pairs if 'check' in p.reason)
        self.report({'WARNING' if check else 'INFO'},
                    f"{counts['MAP']} mapped, {counts['MERGE']} merged, {counts['FOLD']} folded, "
                    f"{counts['SKIP']} skipped; {len(faces)} face mesh(es) left on the source rig"
                    + (f"; {check} position guess(es) to check" if check else ""))
        return {'FINISHED'}


class SUB_OP_rig_combiner_align(Operator):
    """Move the Smash rig's bones onto the paired source bones"""
    bl_idname = 'sub.rig_combiner_align'
    bl_label = 'Align Smash Rig to Source'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        smash, source = _rigs(context)
        return smash is not None and source is not None and len(_combiner(context).pairs) > 0

    def execute(self, context):
        smash, source = _rigs(context)
        settings = _combiner(context)
        report = align_smash_rig(context, smash, source, pairs_from_ui(settings),
                                 settings.point_at_children, settings.carry_meshes)
        msg = f"Moved {len(report['moved'])} Smash bone(s)"
        if report['carried']:
            msg += f", carried {len(report['carried'])} mesh(es)"
        if report['missing']:
            msg += f"; not in source rig: {', '.join(report['missing'])}"
        self.report({'WARNING' if report['missing'] else 'INFO'}, msg)
        return {'FINISHED'}


class SUB_OP_rig_combiner_combine(Operator):
    """Add merged bones to the Smash rig and move the source meshes onto it"""
    bl_idname = 'sub.rig_combiner_combine'
    bl_label = 'Merge Bones & Rebind Meshes'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        smash, source = _rigs(context)
        return smash is not None and source is not None and len(_combiner(context).pairs) > 0

    def execute(self, context):
        smash, source = _rigs(context)
        settings = _combiner(context)
        move = [m.obj for m in settings.meshes if m.obj is not None and m.move]
        stay = [m.obj for m in settings.meshes if m.obj is not None and not m.move]
        report = combine_rigs(context, smash, source, pairs_from_ui(settings), move, stay)
        msg = (f"Added {len(report['added'])} bone(s), rebound {len(report['rebound'])} mesh(es)"
               + (f", {len(report['face_meshes'])} face mesh(es) re-parented only"
                  if report['face_meshes'] else ""))
        problems = []
        if report['collisions']:
            problems.append(f"already existed: {', '.join(report['collisions'])}")
        if report['unknown_groups']:
            problems.append(f"groups with no bone: {', '.join(report['unknown_groups'][:8])}")
        self.report({'WARNING' if problems else 'INFO'}, msg + ('; ' + '; '.join(problems) if problems else ''))
        return {'FINISHED'}


# =============================================================================
# UI
# =============================================================================
class SUB_UL_rig_pairs(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.split(factor=0.38, align=True)
        row.label(text=item.name, icon='BONE_DATA')
        sub = row.split(factor=0.34, align=True)
        sub.prop(item, 'action', text='')
        smash, _ = _rigs(context)
        if item.action == 'MAP' and smash is not None:
            sub.prop_search(item, 'target', smash.data, 'bones', text='')
        elif item.action == 'SKIP':
            sub.label(text=item.reason)
        else:
            sub.prop(item, 'target', text='')


class SUB_UL_rig_meshes(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'move', text='')
        row.label(text=item.obj.name if item.obj else '(deleted)', icon='MESH_DATA')
        if item.note:
            row.label(text=item.note)


class SUB_PT_rig_combiner(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Rig Combiner'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        ssp = context.scene.sub_scene_properties
        settings = ssp.rig_combiner

        col = layout.column(align=True)
        col.prop(ssp, 'smash_armature', text='Smash Rig', icon='ARMATURE_DATA')
        col.prop(ssp, 'other_armature', text='Source Rig', icon='ARMATURE_DATA')
        layout.prop(settings, 'swing_prefix')

        layout.operator('sub.rig_combiner_pair', icon='LINKED', text='1. Auto Pair Bones')
        if not settings.pairs:
            layout.label(text='Pairing is editable before anything changes', icon='INFO')
            return

        counts = defaultdict(int)
        for item in settings.pairs:
            counts[item.action] += 1
        layout.label(text=f"Map {counts['MAP']}   Merge {counts['MERGE']}   "
                          f"Fold {counts['FOLD']}   Skip {counts['SKIP']}")
        layout.template_list('SUB_UL_rig_pairs', '', settings, 'pairs', settings, 'pairs_index',
                             rows=8)
        layout.label(text='Source meshes (unticked = face, stays on source rig):')
        layout.template_list('SUB_UL_rig_meshes', '', settings, 'meshes', settings, 'meshes_index',
                             rows=4)

        box = layout.box()
        box.prop(settings, 'point_at_children')
        box.prop(settings, 'carry_meshes')
        box.operator('sub.rig_combiner_align', icon='BONE_DATA', text='2. Align Smash Rig to Source')
        layout.operator('sub.rig_combiner_combine', icon='AUTOMERGE_ON', text='3. Merge Bones & Rebind Meshes')
