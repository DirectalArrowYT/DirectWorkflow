"""Bones whose rotation an exported animation must leave at rest.

Smash turns and mirrors a fighter through its root and centre bones: Rot carries the facing turn
(a flip fighter facing left plays its animations mirrored), and LegC is never animated by any
vanilla fighter. A retarget bake can still leave rotation on them, typically a source rig's root
or pelvis turn, and then everything parented to them is thrown off with it: the Throw bone under
Rot (where a grabbed opponent is held) swings with the body instead of following its own keys.

`lock_bones` moves that rotation down onto the bones' children, so the pose looks exactly the
same, and leaves the locked bones at their rest rotation (their translation and scale stay
keyed). Children in `KEEP_OWN_KEYS` keep their keys as they are: Throw is keyed for an unturned
Rot, so with Rot back at rest it lands where it was keyed.

Used by the model animation exporter before every save, and by tools/fix_locked_bones.py on
existing .nuanmb files.
"""

import math

from mathutils import Matrix, Quaternion, Vector

# Bones that stay at their rest rotation, parents first.
LOCKED_BONES = ("Rot", "LegC")
# Children of a locked bone that keep their own keys instead of taking its rotation.
KEEP_OWN_KEYS = frozenset({"Throw"})
# Below this, in degrees, a locked bone already counts as at rest.
TOLERANCE_DEG = 0.05


def _matrix(transform) -> Matrix:
    t, (x, y, z, w), s = transform.translation, transform.rotation, transform.scale
    return Matrix.LocRotScale(Vector(t), Quaternion((w, x, y, z)), Vector(s))


def _set(transform, m: Matrix, prev_rotation=None):
    t, q, s = m.decompose()
    rotation = [q.x, q.y, q.z, q.w]
    if prev_rotation is not None and sum(a * b for a, b in zip(prev_rotation, rotation)) < 0:
        rotation = [-c for c in rotation]
    transform.translation = [t.x, t.y, t.z]
    transform.rotation = rotation
    transform.scale = [s.x, s.y, s.z]


def _rest_transform(make_transform, rest: Matrix):
    t, q, s = rest.decompose()
    return make_transform([s.x, s.y, s.z], [q.x, q.y, q.z, q.w], [t.x, t.y, t.z])


def _angle_deg(a, b) -> float:
    dot = min(1.0, abs(sum(x * y for x, y in zip(a, b))))
    return math.degrees(2.0 * math.acos(dot))


def lock_bones(anim, parents, rest, make_transform, locked=LOCKED_BONES, keep=KEEP_OWN_KEYS, create_missing=True):
    """Put `locked` bones back at their rest rotation in an ssbh AnimData, keeping the pose.

    parents: {bone: parent bone or None}. rest: {bone: Matrix}, each bone's rest transform relative
    to its parent in Smash's space (needed for children that have no track). make_transform: the
    ssbh_data_py Transform constructor. create_missing: give a child with no track one (from its
    rest) so it can take the rotation; off for the exporter, whose missing bones are left out on
    purpose. Returns {locked bone: [children that took its rotation]} for the bones that changed.
    """
    group = next((g for g in anim.groups if g.group_type.name == "Transform"), None)
    if group is None:
        return {}
    nodes = {n.name: n for n in group.nodes}
    children = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)
    changed = {}
    for bone in locked:
        node = nodes.get(bone)
        if node is None or bone not in rest or not node.tracks:
            continue
        track = node.tracks[0]
        _, rest_q, _ = rest[bone].decompose()
        rest_rotation = [rest_q.x, rest_q.y, rest_q.z, rest_q.w]
        if all(_angle_deg(v.rotation, rest_rotation) < TOLERANCE_DEG for v in track.values):
            continue
        frames = len(track.values)
        # BL_ bones are Blender-only helpers the exporter never writes.
        takers = [c for c in children.get(bone, []) if c not in keep and not c.startswith("BL_")]
        # Every child that takes the rotation needs a key per frame; one without a track gets its
        # rest, a constant one is spread over the frames.
        for child in takers:
            child_node = nodes.get(child)
            if child_node is None:
                if not create_missing or child not in rest:
                    continue
                child_node = type(node)(child, [type(track)("Transform", values=[_rest_transform(make_transform, rest[child])])])
                group.nodes.append(child_node)
                nodes[child] = child_node
            values = child_node.tracks[0].values
            frames = max(frames, len(values))
        values = track.values
        if len(values) < frames:
            values = values + [values[-1]] * (frames - len(values))
        old = [_matrix(v) for v in values]
        new = []
        for v in values:
            t, _, s = _matrix(v).decompose()
            new.append(Matrix.LocRotScale(t, rest_q, s))
        took = []
        for child in takers:
            child_node = nodes.get(child)
            if child_node is None:
                continue
            child_track = child_node.tracks[0]
            child_values = list(child_track.values)
            if len(child_values) < frames:
                child_values += [child_values[-1]] * (frames - len(child_values))
            fresh = []
            prev = None
            for i in range(frames):
                m = new[i].inverted() @ old[i] @ _matrix(child_values[i])
                out = make_transform([1.0, 1.0, 1.0], [0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.0])
                _set(out, m, prev)
                prev = out.rotation
                fresh.append(out)
            child_track.values = fresh
            took.append(child)
        fixed = []
        for i in range(frames):
            out = make_transform([1.0, 1.0, 1.0], [0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.0])
            _set(out, new[i], fixed[-1].rotation if fixed else None)
            fixed.append(out)
        track.values = fixed
        changed[bone] = took
    # Vanilla animations keep their nodes sorted by name; some behave wrongly in game otherwise.
    group.nodes.sort(key=lambda n: n.name)
    return changed
