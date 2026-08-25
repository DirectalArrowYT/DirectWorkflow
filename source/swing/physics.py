"""
Swing bones as real Blender rigid body physics.

Smash's swing system is a runtime simulation - you author parameters, ship the
model, and find out afterwards whether a given animation makes the hair behave.
This module builds an equivalent simulation out of Blender rigid bodies from
the same swing data, so a chain can be watched, tuned and baked to keyframes
inside Blender instead. Baked chains become ordinary pose animation, which also
means a specific animation can be hand-corrected where the runtime sim would
otherwise be left to chance.

It is a re-creation, not the same solver. Smash's swing and Bullet are
different simulations, so this is a good-faith approximation whose value is
being previewable and directable - not bit-accuracy with the game.

HOW THE RIG IS BUILT
  Each chain becomes a line of "joint proxies" - one small object per bone
  head, plus one for the tip of the last bone:

      proxy0 ---- proxy1 ---- proxy2 ---- proxy3
      (anchor)     bone0        bone1       bone2 tip

  proxy0 is kinematic and follows the bone the chain hangs off, so the whole
  chain rides along with the character. The rest are active rigid bodies joined
  by Generic Spring constraints carrying the swing bone's angle limits and
  stiffness.

  The pose bones are then given a Damped Track constraint aimed at the next
  proxy down the chain. Tracking a direction rather than copying a transform is
  deliberate: bones can only rotate, so bone lengths and the armature hierarchy
  stay intact no matter how far the proxies drift, and the chain cannot come
  apart.

COLLISIONS
  The collision shapes already exist as meshes skinned to their bones, so they
  already move with the animation. They are given passive kinematic rigid
  bodies reading the deformed mesh, which makes them collide where they are
  drawn.

  Per-bone collision lists are honoured through Blender's collision
  collections: every shape gets one of the 20 bits, and a swing bone's proxy
  enables exactly the bits of the shapes that bone is allowed to hit. A useful
  side effect is that proxies never share a bit with each other, so a chain
  cannot collide with itself and jam.
"""

import bpy
from mathutils import Vector

from .sub_swing_data import SUB_PG_sub_swing_data
from ..blender_compat import set_pose_bone_select, is_pose_bone_selected

# Objects this module creates are tagged so cleanup can find them again without
# relying on names, which the user is free to change.
PHYSICS_ID_PROPERTY = 'sub_swing_physics'
PROXY_TAG = 'proxy'
JOINT_TAG = 'joint'
ANCHOR_TAG = 'anchor'

PHYSICS_COLLECTION_SUFFIX = ' Swing Physics'

# Bit 19 is kept for the ground plane so it cannot be taken by a collision
# shape, leaving 0-18 for shapes.
GROUND_COLLISION_BIT = 19
MAX_SHAPE_BITS = 19


def _physics_collection(context, arma_obj):
    """The collection holding this armature's generated physics objects."""
    name = arma_obj.name + PHYSICS_COLLECTION_SUFFIX
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        context.scene.collection.children.link(collection)
    elif name not in {c.name for c in context.scene.collection.children_recursive}:
        context.scene.collection.children.link(collection)
    return collection


def ensure_rigid_body_world(context):
    """The scene's rigid body world, created if the scene has none.

    Both collections have to exist before anything can be linked into them,
    and a fresh scene has neither.
    """
    scene = context.scene
    if scene.rigidbody_world is None:
        bpy.ops.rigidbody.world_add()

    world = scene.rigidbody_world
    if world.collection is None:
        world.collection = bpy.data.collections.new('RigidBodyWorld')
    if world.constraints is None:
        world.constraints = bpy.data.collections.new('RigidBodyConstraints')
    return world


def _add_rigid_body(context, obj, kind):
    """Give an object rigid body settings by putting it in the world.

    Linking into the rigid body world's collection is what creates
    obj.rigid_body - there is no direct way to add one to an object that is not
    the active object, and driving the operator's active object for every proxy
    in a chain would be far slower and would disturb the selection.
    """
    world = ensure_rigid_body_world(context)
    if obj.name not in world.collection.objects:
        world.collection.objects.link(obj)
    obj.rigid_body.type = kind
    return obj.rigid_body


def _add_rigid_body_constraint(context, obj):
    world = ensure_rigid_body_world(context)
    if obj.name not in world.constraints.objects:
        world.constraints.objects.link(obj)
    return obj.rigid_body_constraint


def _tag(obj, role, armature_name):
    obj[PHYSICS_ID_PROPERTY] = role
    obj[PHYSICS_ID_PROPERTY + '_arma'] = armature_name


def _is_generated(obj, armature_name=None):
    if PHYSICS_ID_PROPERTY not in obj:
        return False
    if armature_name is None:
        return True
    return obj.get(PHYSICS_ID_PROPERTY + '_arma') == armature_name


def _make_proxy_object(name, size):
    """A small cube used as a rigid body joint proxy.

    A mesh rather than an empty because Blender rigid bodies need geometry to
    derive a collision shape from, even when - as here - the proxy's own shape
    never actually matters: proxies are filtered out of colliding with each
    other, and only their positions are read.
    """
    mesh = bpy.data.meshes.new(name)
    half = max(size, 1e-4) * 0.5
    verts = [(x * half, y * half, z * half)
             for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
             (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return bpy.data.objects.new(name, mesh)


def _collision_shapes(sub_swing_data):
    """Every collision shape, as (type, index, property group, object)."""
    groups = (
        ('SPHERE', sub_swing_data.spheres),
        ('OVAL', sub_swing_data.ovals),
        ('ELLIPSOID', sub_swing_data.ellipsoids),
        ('CAPSULE', sub_swing_data.capsules),
        ('PLANE', sub_swing_data.planes),
    )
    for shape_type, collection in groups:
        for index, shape in enumerate(collection):
            yield shape_type, index, shape, getattr(shape, 'blender_object', None)


def _shape_bit_map(sub_swing_data, operator=None):
    """{(type, index): collision collection bit} for every shape.

    Blender offers 20 collision collections and a character can carry more
    shapes than that, in which case bits are reused. Sharing a bit only ever
    makes a bone collide with something it was not listed against, which is far
    less disruptive than the alternative of dropping collisions entirely.
    """
    bit_map = {}
    overflow = False
    for order, (shape_type, index, _shape, _obj) in enumerate(_collision_shapes(sub_swing_data)):
        if order >= MAX_SHAPE_BITS:
            overflow = True
        bit_map[(shape_type, index)] = order % MAX_SHAPE_BITS
    if overflow and operator is not None:
        operator.report(
            {'WARNING'},
            f'More than {MAX_SHAPE_BITS} collision shapes - Blender only has 20 '
            f'collision collections, so some shapes now share one. Bones may '
            f'collide with shapes they are not assigned to.')
    return bit_map


def _set_collision_bits(rigid_body, bits):
    collections = [False] * 20
    for bit in bits:
        collections[bit] = True
    rigid_body.collision_collections = collections


def _chain_bones(arma_obj, chain):
    """The pose bones of a chain, in order, skipping any that no longer exist."""
    bones = []
    for swing_bone in chain.swing_bones:
        pose_bone = arma_obj.pose.bones.get(swing_bone.name)
        if pose_bone is not None:
            bones.append((swing_bone, pose_bone))
    return bones


def build_physics(operator, context, arma_obj, settings):
    """Create the whole rigid body rig for an armature's swing data."""
    sub_swing_data: SUB_PG_sub_swing_data = arma_obj.data.sub_swing_data

    remove_physics(context, arma_obj)

    world = ensure_rigid_body_world(context)
    world.substeps_per_frame = settings.swing_physics_substeps
    world.solver_iterations = settings.swing_physics_solver_iterations
    # The cache has to span the animation or the sim stops partway through.
    if world.point_cache is not None:
        world.point_cache.frame_start = context.scene.frame_start
        world.point_cache.frame_end = context.scene.frame_end

    collection = _physics_collection(context, arma_obj)
    bit_map = _shape_bit_map(sub_swing_data, operator)

    # --- Collision shapes ----------------------------------------------------
    collider_count = 0
    for shape_type, index, _shape, shape_obj in _collision_shapes(sub_swing_data):
        if shape_obj is None:
            continue
        rigid_body = _add_rigid_body(context, shape_obj, 'PASSIVE')
        rigid_body.kinematic = True          # driven by the armature, not the sim
        rigid_body.collision_shape = 'MESH'
        # DEFORM reads the armature-deformed mesh, so the collider sits where
        # the shape is actually drawn on the posed character rather than where
        # its rest mesh happens to be.
        rigid_body.mesh_source = 'DEFORM'
        rigid_body.friction = settings.swing_physics_friction
        rigid_body.restitution = 0.0
        _set_collision_bits(rigid_body, [bit_map[(shape_type, index)]])
        collider_count += 1

    # --- Chains --------------------------------------------------------------
    chain_count = 0
    bone_count = 0
    for chain in sub_swing_data.swing_bone_chains:
        bones = _chain_bones(arma_obj, chain)
        if len(bones) == 0:
            continue

        proxies = _build_chain_proxies(
            context, arma_obj, chain, bones, collection, bit_map, settings)
        if proxies is None:
            continue

        _constrain_bones_to_proxies(arma_obj, bones, proxies)
        chain_count += 1
        bone_count += len(bones)

    operator.report(
        {'INFO'},
        f'Swing physics built: {chain_count} chain(s), {bone_count} bone(s), '
        f'{collider_count} collider(s).')
    return chain_count


def _build_chain_proxies(context, arma_obj, chain, bones, collection, bit_map, settings):
    """One proxy per joint plus the constraints between them."""
    matrix_world = arma_obj.matrix_world
    armature_name = arma_obj.name

    # Joint positions in world space: every bone head, then the final tail.
    joint_positions = [matrix_world @ pose_bone.head for _sb, pose_bone in bones]
    joint_positions.append(matrix_world @ bones[-1][1].tail)

    # Proxy size is cosmetic, but scaling it to the chain keeps it visible on a
    # small chain without swamping a large one.
    chain_length = sum(
        (joint_positions[i + 1] - joint_positions[i]).length
        for i in range(len(joint_positions) - 1))
    proxy_size = max(chain_length / max(len(joint_positions), 1) * 0.25, 1e-3)

    proxies = []
    for joint_index, position in enumerate(joint_positions):
        name = f'{arma_obj.name}_{chain.name}_{JOINT_TAG}{joint_index}'
        proxy = _make_proxy_object(name, proxy_size)
        proxy.location = position
        collection.objects.link(proxy)
        proxy.hide_render = True
        proxy.display_type = 'WIRE'
        _tag(proxy, PROXY_TAG, armature_name)
        proxies.append(proxy)

    # The anchor rides the bone the chain hangs from, so the chain follows the
    # character's own animation. Kinematic: it drives the sim, never the other
    # way around.
    anchor = proxies[0]
    _tag(anchor, ANCHOR_TAG, armature_name)
    anchor_rigid_body = _add_rigid_body(context, anchor, 'PASSIVE')
    anchor_rigid_body.kinematic = True
    _set_collision_bits(anchor_rigid_body, [])

    first_pose_bone = bones[0][1]
    parent_bone = first_pose_bone.parent
    child_of = anchor.constraints.new('CHILD_OF')
    child_of.target = arma_obj
    if parent_bone is not None:
        child_of.subtarget = parent_bone.name
    else:
        child_of.subtarget = first_pose_bone.name
    child_of.set_inverse_pending = True

    # Active bodies for the rest of the chain.
    for joint_index in range(1, len(proxies)):
        swing_bone = bones[min(joint_index - 1, len(bones) - 1)][0]
        rigid_body = _add_rigid_body(context, proxies[joint_index], 'ACTIVE')
        rigid_body.mass = max(swing_bone.inertial_mass, 1e-3)
        rigid_body.collision_shape = 'SPHERE'
        rigid_body.friction = settings.swing_physics_friction
        rigid_body.restitution = 0.0

        # Smash's air resistance is a per-step velocity retention factor, so
        # the closer to 1 the less it slows down; Blender damping is the
        # opposite sense. The scale knob exists because the two simulations do
        # not otherwise agree on how strong "the same" damping feels.
        damping = min(max(1.0 - swing_bone.air_resistance, 0.0), 1.0)
        damping = min(damping * settings.swing_physics_damping_scale, 1.0)
        rigid_body.linear_damping = damping
        rigid_body.angular_damping = min(damping + 0.1, 1.0)

        # Only the shapes this bone is listed against.
        bits = []
        for collision in swing_bone.collisions:
            bit = bit_map.get((collision.collision_type, collision.collision_index))
            if bit is not None:
                bits.append(bit)
        if swing_bone.ground_hit and settings.swing_physics_ground:
            bits.append(GROUND_COLLISION_BIT)
        _set_collision_bits(rigid_body, bits)

    # Joints. Constraint i sits at proxy i and joins proxy i-1 to proxy i.
    for joint_index in range(1, len(proxies)):
        swing_bone = bones[min(joint_index - 1, len(bones) - 1)][0]
        name = f'{arma_obj.name}_{chain.name}_link{joint_index}'
        joint = bpy.data.objects.new(name, None)
        joint.empty_display_type = 'PLAIN_AXES'
        joint.empty_display_size = proxy_size
        joint.location = proxies[joint_index].location
        collection.objects.link(joint)
        _tag(joint, JOINT_TAG, armature_name)

        constraint = _add_rigid_body_constraint(context, joint)
        constraint.type = 'GENERIC_SPRING'
        constraint.object1 = proxies[joint_index - 1]
        constraint.object2 = proxies[joint_index]
        constraint.disable_collisions = True

        # Locked in translation - a joint is a pivot, not a slider - so the
        # chain keeps its bone lengths.
        for axis in 'xyz':
            setattr(constraint, f'use_limit_lin_{axis}', True)
            setattr(constraint, f'limit_lin_{axis}_lower', 0.0)
            setattr(constraint, f'limit_lin_{axis}_upper', 0.0)

        # Smash's Y and Z angle limits map onto Blender's X and Z: the two
        # packages disagree about which axis a bone points down, which is the
        # same swap sub_swing_data.py notes on the angle properties themselves.
        constraint.use_limit_ang_x = True
        constraint.limit_ang_x_lower = swing_bone.angle_y[0]
        constraint.limit_ang_x_upper = swing_bone.angle_y[1]
        constraint.use_limit_ang_z = True
        constraint.limit_ang_z_lower = swing_bone.angle_z[0]
        constraint.limit_ang_z_upper = swing_bone.angle_z[1]
        # Twist about the bone's own length is not something swing models.
        constraint.use_limit_ang_y = True
        constraint.limit_ang_y_lower = 0.0
        constraint.limit_ang_y_upper = 0.0

        # Goal strength is the pull back towards the rest pose, which is what a
        # rotational spring does here.
        stiffness = max(swing_bone.goal_strength, 0.0) * settings.swing_physics_stiffness_scale
        if stiffness > 0.0:
            for axis in 'xz':
                setattr(constraint, f'use_spring_ang_{axis}', True)
                setattr(constraint, f'spring_stiffness_ang_{axis}', stiffness)
                setattr(constraint, f'spring_damping_ang_{axis}',
                        max(swing_bone.friction_rate, 0.0))

    return proxies


def _constrain_bones_to_proxies(arma_obj, bones, proxies):
    """Aim each swing bone at the next joint proxy."""
    for bone_index, (_swing_bone, pose_bone) in enumerate(bones):
        for existing in list(pose_bone.constraints):
            if existing.name.startswith('SubSwingPhysics'):
                pose_bone.constraints.remove(existing)

        constraint = pose_bone.constraints.new('DAMPED_TRACK')
        constraint.name = 'SubSwingPhysics Track'
        constraint.target = proxies[bone_index + 1]
        # Blender bones point down their own +Y.
        constraint.track_axis = 'TRACK_Y'


def remove_physics(context, arma_obj):
    """Delete everything build_physics() made for this armature."""
    armature_name = arma_obj.name
    scene = context.scene

    for pose_bone in arma_obj.pose.bones:
        for constraint in list(pose_bone.constraints):
            if constraint.name.startswith('SubSwingPhysics'):
                pose_bone.constraints.remove(constraint)

    world = scene.rigidbody_world
    sub_swing_data: SUB_PG_sub_swing_data = arma_obj.data.sub_swing_data

    # Collision shapes are the user's own objects, so only the rigid body
    # settings this module added come off - never the object itself.
    for _shape_type, _index, _shape, shape_obj in _collision_shapes(sub_swing_data):
        if shape_obj is None or shape_obj.rigid_body is None:
            continue
        # Unlinking from the world collection is not enough on its own: the
        # object keeps its rigid_body settings, so the shape would still behave
        # as a collider in any other rigid body simulation in the file. Only
        # the operator actually clears them.
        try:
            with context.temp_override(object=shape_obj,
                                       active_object=shape_obj,
                                       selected_objects=[shape_obj]):
                bpy.ops.rigidbody.object_remove()
        except Exception:
            if world is not None and world.collection is not None:
                if shape_obj.name in world.collection.objects:
                    world.collection.objects.unlink(shape_obj)

    doomed = [obj for obj in bpy.data.objects if _is_generated(obj, armature_name)]

    # Every joint constraint points at two proxy objects. Deleting a proxy
    # while a constraint still references it leaves the rigid body world
    # holding a freed pointer, and Blender then dies on the next frame change -
    # long after the teardown that caused it, so it presents as an unrelated
    # crash while scrubbing the timeline. Clearing the references first, and
    # freeing the simulation cache that also remembers these bodies, is what
    # makes the deletion below safe.
    for obj in doomed:
        constraint = getattr(obj, 'rigid_body_constraint', None)
        if constraint is not None:
            constraint.object1 = None
            constraint.object2 = None

    if doomed and world is not None:
        try:
            with context.temp_override(scene=scene):
                bpy.ops.ptcache.free_bake_all()
        except Exception:
            pass

    for obj in doomed:
        if world is not None:
            if world.collection is not None and obj.name in world.collection.objects:
                world.collection.objects.unlink(obj)
            if world.constraints is not None and obj.name in world.constraints.objects:
                world.constraints.objects.unlink(obj)
        bpy.data.objects.remove(obj, do_unlink=True)

    collection_name = armature_name + PHYSICS_COLLECTION_SUFFIX
    collection = bpy.data.collections.get(collection_name)
    if collection is not None and len(collection.objects) == 0:
        bpy.data.collections.remove(collection)

    return len(doomed)


def has_physics(arma_obj):
    return any(_is_generated(obj, arma_obj.name) for obj in bpy.data.objects)


def bake_physics(operator, context, arma_obj, frame_start, frame_end):
    """Bake the simulated chains down to ordinary pose keyframes.

    The rig is torn down afterwards: once the motion is keyframed the
    simulation has nothing left to contribute, and leaving it running would
    fight the keys it just produced.
    """
    sub_swing_data: SUB_PG_sub_swing_data = arma_obj.data.sub_swing_data

    swing_bone_names = set()
    for chain in sub_swing_data.swing_bone_chains:
        for swing_bone in chain.swing_bones:
            if swing_bone.name in arma_obj.pose.bones:
                swing_bone_names.add(swing_bone.name)

    if not swing_bone_names:
        operator.report({'WARNING'}, 'No swing bones to bake.')
        return False

    previous_mode = arma_obj.mode
    previous_active = context.view_layer.objects.active
    context.view_layer.objects.active = arma_obj
    bpy.ops.object.mode_set(mode='POSE')

    # Selection moved from Bone.select to PoseBone.select in Blender 5, so it
    # goes through the compat helpers rather than either attribute directly.
    previously_selected = {pose_bone.name for pose_bone in arma_obj.pose.bones
                           if is_pose_bone_selected(pose_bone)}
    for pose_bone in arma_obj.pose.bones:
        set_pose_bone_select(pose_bone, pose_bone.name in swing_bone_names)

    try:
        bpy.ops.nla.bake(
            frame_start=frame_start,
            frame_end=frame_end,
            only_selected=True,
            visual_keying=True,
            clear_constraints=True,
            clear_parents=False,
            use_current_action=True,
            bake_types={'POSE'},
        )
    except Exception as e:
        operator.report({'ERROR'}, f'Bake failed: {e}')
        return False
    finally:
        for pose_bone in arma_obj.pose.bones:
            set_pose_bone_select(pose_bone, pose_bone.name in previously_selected)
        bpy.ops.object.mode_set(mode=previous_mode)
        if previous_active is not None:
            context.view_layer.objects.active = previous_active

    dropped = _drop_static_channels(arma_obj, swing_bone_names)
    removed = remove_physics(context, arma_obj)
    operator.report(
        {'INFO'},
        f'Baked {len(swing_bone_names)} swing bone(s) over frames '
        f'{frame_start}-{frame_end}. Physics rig removed ({removed} object(s)).')
    return True


def _owned_fcurves(action):
    """(owner collection, fcurve) for every fcurve in an action.

    Blender 4.4 moved fcurve storage out of action.fcurves and into
    layers/strips/channelbags, leaving action.fcurves as a compatibility view
    onto the first slot. The owner has to be carried around with the curve
    because removing one through the view rather than through the collection
    that actually holds it leaves the channelbag pointing at freed memory -
    which does not raise, it crashes Blender the next time anything reads the
    action.

    Channelbags are therefore preferred, and action.fcurves is used only for
    genuinely legacy actions that have no layers at all.
    """
    owned = []
    for layer in getattr(action, 'layers', []):
        for strip in getattr(layer, 'strips', []):
            for channelbag in getattr(strip, 'channelbags', []):
                for fcurve in channelbag.fcurves:
                    owned.append((channelbag.fcurves, fcurve))
    if owned:
        return owned
    return [(action.fcurves, fcurve) for fcurve in getattr(action, 'fcurves', [])]


def _drop_static_channels(arma_obj, bone_names):
    """Remove baked location/scale curves that never actually move.

    The bake writes every channel, but a swing chain is driven by Damped Track
    and therefore only ever rotates. The location and scale curves it produces
    are flat, and on a connected bone the location one cannot do anything at
    all - so they are noise in the action and in any later export. Curves that
    do vary are left alone, on the grounds that something must have put motion
    there deliberately.
    """
    if arma_obj.animation_data is None or arma_obj.animation_data.action is None:
        return 0

    action = arma_obj.animation_data.action
    doomed = []
    for owner, fcurve in _owned_fcurves(action):
        path = fcurve.data_path
        if not path.startswith('pose.bones['):
            continue
        if not (path.endswith('.location') or path.endswith('.scale')):
            continue
        if not any(f'"{name}"' in path for name in bone_names):
            continue

        values = [keyframe.co[1] for keyframe in fcurve.keyframe_points]
        if not values:
            continue
        if max(values) - min(values) > 1e-5:
            continue

        doomed.append((owner, fcurve))

    # Collected first, removed after: mutating a collection while walking it
    # is what invalidates the rest of the iteration.
    dropped = 0
    for owner, fcurve in doomed:
        try:
            owner.remove(fcurve)
            dropped += 1
        except Exception:
            pass
    return dropped
