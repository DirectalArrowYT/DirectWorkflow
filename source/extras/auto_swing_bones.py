"""Auto swing bones.

Turns an ordinary bone chain (hair, a scarf, a skirt) into a working Smash swing
setup in four steps, each of which can be run on its own:

    1. Detect   - find linear bone chains that look like swing candidates.
    2. Rename   - put them on Smash's S_<Part><Dir><n> convention, with the
                  chain terminated by a weightless _null bone.
    3. Build    - create the sub_swing_data chains and fill in per-bone physics.
    4. Collide  - attach each swing bone to the collision shapes nearest it.

Naming and physics are measured, not invented. Every vanilla swing.prc in the
game dump was parsed to derive them; see swing_profiles.py for the parameter
tables and the note on _null in the build operator for chain length.
"""

import re
from math import radians

import bpy
from bpy.props import (BoolProperty, CollectionProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)
from bpy.types import Operator, Panel, PropertyGroup, UIList
from mathutils import Vector

from .swing_profiles import GENERIC_PROFILES, SWING_PROFILES

# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

# Ordered longest-first so 'armhair' and 'busthair' win over 'hair', and
# 'shirttail' over 'tail'. The value is the CamelCase form used in bone names;
# the swing.prc stores the lowercase hash40 of it either way.
PART_PATTERNS = [
    (re.compile(r'(?i)shirt_?tail'), 'ShirtTail'),
    (re.compile(r'(?i)arm_?hair'),   'ArmHair'),
    (re.compile(r'(?i)bust_?hair'),  'BustHair'),
    (re.compile(r'(?i)hair_?side'),  'HairSide'),
    (re.compile(r'(?i)head_?band'),  'Headband'),
    (re.compile(r'(?i)hair'),        'Hair'),
    (re.compile(r'(?i)mantle'),      'Mantle'),
    (re.compile(r'(?i)skirt'),       'Skirt'),
    # All Justice rips spell it 'slirt' and use Japanese words for a few parts
    # ('eri' = collar, 'sode' = sleeve, 'mant' = mantle). Mapped onto the Smash
    # part they behave like, so each also inherits that part's measured physics
    # profile rather than falling back to the generic one.
    (re.compile(r'(?i)slirt'),       'Skirt'),
    (re.compile(r'(?i)neck_?guard'), 'Collar'),
    (re.compile(r'(?i)eri'),         'Collar'),
    (re.compile(r'(?i)sode'),        'Sleeve'),
    (re.compile(r'(?i)mant'),        'Mantle'),
    (re.compile(r'(?i)scarf'),       'Scarf'),
    (re.compile(r'(?i)sleeve'),      'Sleeve'),
    (re.compile(r'(?i)collar'),      'Collar'),
    (re.compile(r'(?i)ribbon'),      'Ribbon'),
    (re.compile(r'(?i)belt'),        'Belt'),
    (re.compile(r'(?i)cape'),        'Cape'),
    (re.compile(r'(?i)coat'),        'Coat'),
    (re.compile(r'(?i)wing'),        'Wing'),
    (re.compile(r'(?i)tail'),        'Tail'),
    (re.compile(r'(?i)strap'),       'Strap'),
    (re.compile(r'(?i)rope'),        'Rope'),
    (re.compile(r'(?i)chain'),       'Chain'),
    (re.compile(r'(?i)cloth'),       'Cloth'),
    (re.compile(r'(?i)pias'),        'Pias'),
    (re.compile(r'(?i)bust'),        'Bust'),
    (re.compile(r'(?i)hat'),         'Hat'),
    (re.compile(r'(?i)ear'),         'Ear'),
]

# Direction tokens, as they appear between the part and the index. Vanilla is
# not internally consistent about the order of two-letter tokens - some
# fighters ship s_hairlb1, others s_hairbl1 - so whichever the source rig used
# is preserved rather than normalised. The prc is self-describing: it only has
# to agree with the rig shipping beside it.
DIRECTION_TOKENS = {
    'C', 'F', 'B', 'L', 'R', 'T', 'U', 'D',
    'LF', 'LB', 'LT', 'LU', 'LD', 'RF', 'RB', 'RT', 'RU', 'RD',
    'FL', 'FR', 'BL', 'BR', 'TL', 'TR', 'CF', 'CB',
    'BT', 'FT', 'TB', 'TF', 'CL', 'CR', 'LC', 'RC',
}

# Bones of the standard fighter skeleton, which a part word can otherwise
# match: 'Bust' is the chest bone on every Smash rig, not a bust accessory,
# and renaming it S_Bust1 would break the skeleton. Helper (H_) bones are
# driven by the nuhlpb and are never swing either.
SMASH_CORE_BONES = {
    'Trans', 'Rot', 'Throw', 'Hip', 'Waist', 'Bust', 'Neck', 'Head', 'Face',
    'LegC', 'ClavicleC', 'ClavicleL', 'ClavicleR', 'Jaw', 'Tongue',
    'Mouth_group', 'FingerL30', 'FingerR30',
}

# An already-converted chain: S_HairLB2, S_BandannaL1, S_SkirtC4_null. The part
# is whatever word is there, so a vanilla name works even when PART_PATTERNS
# has never heard of it - the vocabulary is only needed for rigs that have not
# been renamed yet.
_SMASH_SWING_NAME = re.compile(
    r'(?i)^s_([a-z]+?)({})?(\d+)(_null)?$'.format(
        '|'.join(sorted(DIRECTION_TOKENS, key=len, reverse=True))))

# Trailing .001 / .002 that Blender appends to duplicate names.
_BLENDER_DUP = re.compile(r'\.\d{3}$')
_NULL_SUFFIX = re.compile(r'(?i)_null$')
_LEADING_S = re.compile(r'(?i)^s_')


def _split_tokens(name):
    """Break a bone name into comparable tokens.

    Handles both underscore style (AC_LB_hair_A) and camel/number style
    (S_HairLB1), which show up on modded and vanilla rigs respectively.
    """
    name = _BLENDER_DUP.sub('', name)
    name = _NULL_SUFFIX.sub('', name)
    name = _LEADING_S.sub('', name)
    parts = []
    for chunk in re.split(r'[_\s.-]+', name):
        if not chunk:
            continue
        # Split camelCase and letter/digit boundaries: HairLB1 -> Hair, LB, 1
        parts.extend(p for p in re.findall(
            r'[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+', chunk) if p)
    return parts


def classify_bone(name):
    """Return (part, direction) for a bone name, or (None, '') if not a candidate."""
    base = _BLENDER_DUP.sub('', name)
    if base in SMASH_CORE_BONES or base.startswith('H_'):
        return None, ''

    already_named = _SMASH_SWING_NAME.match(base)
    if already_named:
        part = already_named.group(1)
        # Vanilla ships some chains all-lowercase (s_hairlb1); the part is
        # written CamelCase in the bone name either way.
        return (part if not part.islower() else part.capitalize()),             (already_named.group(2) or '').upper()

    tokens = _split_tokens(name)
    if not tokens:
        return None, ''

    # A part name can straddle several tokens, because camelCase splitting
    # breaks ArmHair into Arm + Hair. Contiguous spans of up to three tokens
    # are tried so the compound parts still beat their own suffixes -
    # 'ArmHair' must win over 'Hair', 'ShirtTail' over 'Tail'. PART_PATTERNS is
    # ordered longest-first, so the first pattern that matches anywhere wins.
    part = None
    span = (0, 0)
    for pattern, canonical in PART_PATTERNS:
        for width in (3, 2, 1):
            for i in range(len(tokens) - width + 1):
                if pattern.fullmatch(''.join(tokens[i:i + width])):
                    part, span = canonical, (i, i + width)
                    break
            if part:
                break
        if part:
            break

    if part is None:
        # The part may be glued to a direction inside one token, e.g. 'hairlb'.
        for tok in tokens:
            for pattern, canonical in PART_PATTERNS:
                m = pattern.match(tok)
                if m and m.start() == 0:
                    rest = tok[m.end():].upper()
                    if rest in DIRECTION_TOKENS:
                        return canonical, rest
        return None, ''

    # Direction is a token outside the part that is nothing but direction
    # letters. The final token is the chain position (a letter or a number),
    # not a direction, so it is never considered.
    candidates = [t for i, t in enumerate(tokens)
                  if not (span[0] <= i < span[1]) and i != len(tokens) - 1]
    for tok in reversed(candidates):
        if tok.upper() in DIRECTION_TOKENS:
            return part, tok.upper()

    # A direction fused onto the tail of the part itself: 'hairlb'.
    fused = ''.join(tokens[span[0]:span[1]])
    for pattern, canonical in PART_PATTERNS:
        m = pattern.match(fused)
        if m and canonical == part:
            rest = fused[m.end():].upper()
            if rest in DIRECTION_TOKENS:
                return part, rest
    return part, ''


def swing_bone_name(part, direction, index, is_null):
    """S_HairLB2, or S_HairLB4_null for the terminal."""
    return 'S_{}{}{}{}'.format(part, direction, index, '_null' if is_null else '')


# ---------------------------------------------------------------------------
# Physics defaults
# ---------------------------------------------------------------------------

# Per-position vanilla profiles, keyed by (part, bone count). See
# swing_profiles.py for how they were derived and why they are per-position.
_PROFILE_FIELDS = (
    'air_resistance', 'water_resistance', 'min_angle_z', 'max_angle_z',
    'min_angle_y', 'max_angle_y', 'collision_size_tip', 'collision_size_root',
    'friction_rate', 'goal_strength', 'inertial_mass', 'local_gravity',
    'fall_speed_scale', 'wind_affect', 'ground_hit',
)


def _resample(rows, count):
    """Stretch or squash a profile of len(rows) bones onto `count` bones.

    Vanilla only ships certain (part, length) combinations, so a 5-bone hair
    chain has to borrow the 4-bone shape. Sampling along the normalised root-to
    -tip position keeps what matters - the goal strength falloff and the way
    the angle limits open up - instead of repeating one bone's values.
    """
    m = len(rows)
    if m == count:
        return [tuple(r) for r in rows]
    out = []
    for i in range(count):
        t = 0.0 if count == 1 else i / (count - 1)
        pos = t * (m - 1)
        lo = int(pos)
        hi = min(lo + 1, m - 1)
        frac = pos - lo
        row = []
        for a, b in zip(rows[lo], rows[hi]):
            if a is None or b is None:
                row.append(a if a is not None else b)
            else:
                row.append(a + (b - a) * frac)
        out.append(tuple(row))
    return out


def _profile_rows(part, count):
    """The best available vanilla profile for this part at this chain length."""
    lengths = SWING_PROFILES.get(part)
    if lengths:
        if count in lengths:
            return [tuple(r) for r in lengths[count]]
        # Nearest length this part actually ships, preferring longer, since a
        # longer profile carries more of the root-to-tip shape.
        nearest = min(lengths, key=lambda n: (abs(n - count), -n))
        return _resample(lengths[nearest], count)
    if count in GENERIC_PROFILES:
        return [tuple(r) for r in GENERIC_PROFILES[count]]
    nearest = min(GENERIC_PROFILES, key=lambda n: (abs(n - count), -n))
    return _resample(GENERIC_PROFILES[nearest], count)


def parameters_for(part, index, count):
    """Physics for bone `index` of `count`, as a plain dict.

    Values come straight from the vanilla profile for this part and chain
    length. Nothing is synthesised: the falloff in goal strength and the way
    the angle limits widen toward the tip are already in the data, and an
    invented ramp on top of a pooled average is what made chains chatter
    against their own angle limits.
    """
    rows = _profile_rows(part, count)
    row = rows[max(0, min(index, len(rows) - 1))]
    out = dict(zip(_PROFILE_FIELDS, row))
    out['ground_hit'] = bool(round(out.get('ground_hit') or 0))
    for key, fallback in (('inertial_mass', 1.0), ('air_resistance', 5.0),
                          ('goal_strength', 100.0)):
        if out.get(key) is None:
            out[key] = fallback
    return out


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class ProposedChain:
    """One candidate chain, before anything is written to the rig."""

    def __init__(self, bones, part, direction):
        self.bone_names = [b.name for b in bones]
        self.part = part
        self.direction = direction
        self.warnings = []

    @property
    def family(self):
        return '{}{}'.format(self.part, self.direction)

    def needs_new_null(self):
        return not _NULL_SUFFIX.search(self.bone_names[-1])

    def proposed_names(self, needs_new_null):
        """New name for each existing bone, plus one more if a _null is appended."""
        total = len(self.bone_names) + (1 if needs_new_null else 0)
        return [swing_bone_name(self.part, self.direction, i + 1, i == total - 1)
                for i in range(total)]


def linear_run(bone):
    """Walk down from `bone` while each step has exactly one child."""
    run = [bone]
    cur = bone
    while len(cur.children) == 1:
        cur = cur.children[0]
        run.append(cur)
    return run


def detect_chains(armature_data, only_selected=False, min_length=2, info=None):
    """Find swing candidates on `armature_data`.

    A candidate is the root of a linear run whose name identifies a swing part
    and whose parent is not part of the same run. A branching bone (a scarf hub
    with four children, say) is not a chain itself; each branch below it is
    considered separately.
    """
    bones = list(armature_data.bones)
    selected = {b.name for b in bones if b.select} if only_selected else None
    classified = {b.name: classify_bone(b.name) for b in bones}

    found = []
    claimed = set()
    too_short = []
    for bone in bones:
        part, direction = classified.get(bone.name, (None, ''))
        if part is None or bone.name in claimed:
            continue
        if selected is not None and bone.name not in selected:
            continue
        # Only start at the top of a run: a same-family parent with this bone
        # as its only child means we are mid-chain.
        if bone.parent is not None:
            p_part, p_dir = classified.get(bone.parent.name, (None, ''))
            if p_part == part and p_dir == direction and len(bone.parent.children) == 1:
                continue

        run = linear_run(bone)
        if len(run) < min_length:
            too_short.append(bone.name)
            continue
        chain = ProposedChain(run, part, direction)
        if len(run[-1].children) > 1:
            chain.warnings.append('ends on a bone with several children')
        if any(_BLENDER_DUP.search(b.name) for b in run):
            chain.warnings.append('contains a Blender duplicate name (.001)')
        found.append(chain)
        claimed.update(b.name for b in run)

    # Disambiguate families that collide - two separate Hair chains with no
    # direction token, say - by numbering them.
    by_family = {}
    for chain in found:
        by_family.setdefault(chain.family, []).append(chain)
    for family, chains in by_family.items():
        if len(chains) > 1:
            for n, chain in enumerate(chains, start=1):
                chain.direction = '{}{}'.format(chain.direction, n)
                chain.warnings.append(
                    'family "{}" was claimed by {} chains; numbered to keep them apart'
                    .format(family, len(chains)))

    # Why candidates were passed over. Without this, a rig whose bones are all
    # named in a vocabulary this does not know - or whose chains are single
    # bones - reports "nothing detected" and gives no way to tell which.
    if info is not None:
        info['too_short'] = too_short
        info['unrecognised'] = [b.name for b in bones
                                if _LEADING_S.match(b.name)
                                and classified.get(b.name, (None, ''))[0] is None]
    return found


def detection_hint(info, min_length):
    """One line naming what detection skipped, or '' when it skipped nothing."""
    def sample(names):
        return ', '.join(names[:4]) + ('...' if len(names) > 4 else '')

    bits = []
    too_short = info.get('too_short') or []
    if too_short:
        bits.append('{} run(s) shorter than Min Chain Length {} ({})'.format(
            len(too_short), min_length, sample(too_short)))
    unrecognised = info.get('unrecognised') or []
    if unrecognised:
        bits.append('{} S_ bone(s) with an unrecognised part word ({})'.format(
            len(unrecognised), sample(unrecognised)))
    return '; ' + '; '.join(bits) if bits else ''


# ---------------------------------------------------------------------------
# Collisions
# ---------------------------------------------------------------------------

def collision_centers(armature_data):
    """(type, index, name, centre in armature space) for every collision shape.

    Matches how create_meshes builds the preview geometry: the offset is in
    bone space and bone.matrix_local takes it to armature space.
    """
    ssd = armature_data.sub_swing_data
    out = []

    def bone_point(bone_name, offset):
        bone = armature_data.bones.get(bone_name)
        return None if bone is None else bone.matrix_local @ Vector(offset)

    for i, c in enumerate(ssd.spheres):
        p = bone_point(c.bone, c.offset)
        if p is not None:
            out.append(('SPHERE', i, c.name, p))
    for i, c in enumerate(ssd.ovals):
        a = bone_point(c.start_bone_name, c.start_offset)
        b = bone_point(c.end_bone_name, c.end_offset)
        if a is not None and b is not None:
            out.append(('OVAL', i, c.name, (a + b) * 0.5))
    for i, c in enumerate(ssd.ellipsoids):
        p = bone_point(c.bone_name, c.offset)
        if p is not None:
            out.append(('ELLIPSOID', i, c.name, p))
    for i, c in enumerate(ssd.capsules):
        a = bone_point(c.start_bone_name, c.start_offset)
        b = bone_point(c.end_bone_name, c.end_offset)
        if a is not None and b is not None:
            out.append(('CAPSULE', i, c.name, (a + b) * 0.5))
    return out


def nearest_collisions(bone, centers, limit, radius_scale, scale=None):
    """The `limit` collision shapes closest to `bone`.

    Distance runs from the bone's midpoint. The cutoff is a multiple of
    `scale`, which callers set to the whole chain's reach rather than the
    bone's own length: tip bones are often stubs a tenth the length of the ones
    above them, and scaling per bone left them with no collisions at all, which
    is backwards - vanilla gives tips more, not fewer. Planes are never
    auto-assigned, being unbounded, so proximity says nothing about them.
    """
    mid = (Vector(bone.head_local) + Vector(bone.tail_local)) * 0.5
    cutoff = max(scale if scale is not None else bone.length, 1e-4) * radius_scale
    scored = []
    for kind, index, name, centre in centers:
        d = (centre - mid).length
        if d <= cutoff:
            scored.append((d, kind, index, name))
    scored.sort(key=lambda r: r[0])
    return scored[:limit]


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class SUB_PG_auto_swing_proposal(PropertyGroup):
    bone_names: StringProperty(default='')      # newline separated
    new_names: StringProperty(default='')       # newline separated
    part: StringProperty(default='')
    direction: StringProperty(default='')
    warnings: StringProperty(default='')
    include: BoolProperty(name='Include', default=True)
    append_null: BoolProperty(default=False)


class SUB_PG_auto_swing(PropertyGroup):
    proposals: CollectionProperty(type=SUB_PG_auto_swing_proposal)
    active_index: IntProperty(default=0, options={'HIDDEN'})
    only_selected: BoolProperty(
        name='Only Selected Bones',
        description='Restrict detection to chains whose root bone is selected',
        default=False)
    min_length: IntProperty(
        name='Min Chain Length', default=2, min=1, max=16,
        description='Ignore runs shorter than this many bones')
    assign_collisions: BoolProperty(
        name='Assign Collisions', default=True,
        description='Attach each swing bone to the collision shapes nearest it')
    max_collisions: IntProperty(
        name='Max Per Bone', default=4, min=0, max=16,
        description='Vanilla chains carry 1 to 5 collisions per bone')
    collision_radius_scale: FloatProperty(
        name='Search Radius', default=6.0, min=0.1, max=50.0,
        description='Cutoff distance as a multiple of the bone length')
    overwrite_chains: BoolProperty(
        name='Replace Existing Chains', default=False,
        description='Clear sub_swing_data chains before building. Off by '
                    'default so an existing setup is added to, not lost')
    move_blockers: BoolProperty(
        name='Move Blocking Bones Aside', default=True,
        description='A leftover bone already holding a target name is renamed '
                    'to <name>_orphan. Without this Blender silently appends '
                    '.001 to the real chain instead, which breaks the export')


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

def _armature(context):
    ob = context.object
    return ob if (ob is not None and ob.type == 'ARMATURE') else None


def _store(proposals_prop, chains):
    proposals_prop.clear()
    for chain in chains:
        p = proposals_prop.add()
        p.bone_names = '\n'.join(chain.bone_names)
        p.part = chain.part
        p.direction = chain.direction
        p.append_null = chain.needs_new_null()
        p.new_names = '\n'.join(chain.proposed_names(p.append_null))
        p.warnings = '; '.join(chain.warnings)


class SUB_OP_auto_swing_detect(Operator):
    bl_idname = 'sub.auto_swing_detect'
    bl_label = 'Detect Swing Chains'
    bl_description = ('Scan the armature for linear bone chains that look like '
                      'swing candidates. Nothing is modified')
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _armature(context) is not None

    def execute(self, context):
        ob = _armature(context)
        settings = context.scene.sub_auto_swing
        info = {}
        chains = detect_chains(ob.data,
                               only_selected=settings.only_selected,
                               min_length=settings.min_length,
                               info=info)
        _store(settings.proposals, chains)
        hint = detection_hint(info, settings.min_length)
        if not chains:
            self.report({'WARNING'}, 'No swing chains detected' + hint)
        else:
            self.report({'INFO'}, 'Detected {} chain(s)'.format(len(chains)) + hint)
        return {'FINISHED'}


class SUB_OP_auto_swing_rename(Operator):
    bl_idname = 'sub.auto_swing_rename'
    bl_label = 'Rename to S_ Convention'
    bl_description = ('Rename detected chains to S_<Part><Dir><n>, appending a '
                      'weightless _null terminal where one is missing')
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (_armature(context) is not None
                and len(context.scene.sub_auto_swing.proposals) > 0)

    def execute(self, context):
        ob = _armature(context)
        settings = context.scene.sub_auto_swing
        renamed = appended = 0
        previous_mode = ob.mode

        plans = []
        for p in settings.proposals:
            if p.include:
                plans.append((p.bone_names.split('\n'),
                              p.new_names.split('\n'),
                              p.append_null))

        # A bone that is not in any plan but already holds one of the names we
        # are about to assign will block it, and Blender resolves the clash by
        # appending .001 to *our* bone - so the chain silently ends up rooted
        # at S_MantleL1.001 and the export is wrong. These are almost always
        # leftovers from an earlier attempt, so move them out of the way and
        # say which ones were moved.
        planned_bones = {old for olds, _n, _a in plans for old in olds}
        targets = {new for _o, news, _a in plans for new in news}
        blockers = [b.name for b in ob.data.bones
                    if b.name in targets and b.name not in planned_bones]

        bpy.ops.object.mode_set(mode='EDIT')
        try:
            ebones = ob.data.edit_bones
            for name in blockers:
                eb = ebones.get(name)
                if eb is not None and settings.move_blockers:
                    eb.name = '{}_orphan'.format(name)

            # Names are assigned in two passes through a placeholder. Renaming
            # directly can collide with a bone that has not been renamed yet -
            # S_Hair2 becoming S_Hair1 while the real S_Hair1 still exists -
            # and Blender resolves that by silently appending .001.
            staged = []
            for i, (olds, news, _append) in enumerate(plans):
                for j, old in enumerate(olds):
                    eb = ebones.get(old)
                    if eb is None:
                        continue
                    placeholder = '__autoswing_{}_{}'.format(i, j)
                    eb.name = placeholder
                    staged.append((placeholder, news[j]))
            for placeholder, final in staged:
                eb = ebones.get(placeholder)
                if eb is not None:
                    eb.name = final
                    renamed += 1

            for olds, news, append_null in plans:
                if not append_null:
                    continue
                parent = ebones.get(news[len(olds) - 1])
                if parent is None:
                    continue
                # The terminal exists only to give the last simulated bone a
                # direction to point along, so it continues the parent at its
                # tip. It carries no weights, exactly like vanilla _null bones.
                nb = ebones.new(news[-1])
                nb.head = parent.tail
                nb.tail = parent.tail + (parent.tail - parent.head)
                nb.roll = parent.roll
                nb.parent = parent
                nb.use_connect = True
                nb.use_deform = False
                appended += 1
        finally:
            bpy.ops.object.mode_set(mode=previous_mode)

        # Re-detect so the stored proposals match the rig's new names.
        _store(settings.proposals,
               detect_chains(ob.data, only_selected=False,
                             min_length=settings.min_length))
        msg = 'Renamed {} bone(s), appended {} _null terminal(s)'.format(renamed, appended)
        if blockers:
            if settings.move_blockers:
                msg += '; moved {} blocking bone(s) aside: {}'.format(
                    len(blockers), ', '.join(sorted(blockers)))
                self.report({'WARNING'}, msg)
            else:
                self.report({'ERROR'},
                            'These bones already hold names this rename needs, so '
                            'Blender would append .001 to the real chain: {}. '
                            'Delete them, or enable "Move Blocking Bones Aside".'
                            .format(', '.join(sorted(blockers))))
            return {'FINISHED'}
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def _reindex(arma):
    """Point each bone back at its chain, the way the swing.prc importer does."""
    ssd = arma.sub_swing_data
    for chain_index, chain in enumerate(ssd.swing_bone_chains):
        bone = arma.bones.get(chain.start_bone_name)
        for bone_index, swing_bone in enumerate(chain.swing_bones):
            if bone is None:
                break
            swing_bone.name = bone.name
            bone.sub_swing_blender_bone_data.swing_bone_chain_index = chain_index
            bone.sub_swing_blender_bone_data.swing_bone_index = bone_index
            bone = bone.children[0] if len(bone.children) >= 1 else None


class SUB_OP_auto_swing_build(Operator):
    bl_idname = 'sub.auto_swing_build'
    bl_label = 'Build Swing Chains'
    bl_description = ('Create sub_swing_data chains from the S_ bones on this '
                      'armature and fill in per-bone physics')
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _armature(context) is not None

    def execute(self, context):
        ob = _armature(context)
        arma = ob.data
        settings = context.scene.sub_auto_swing
        ssd = arma.sub_swing_data

        if settings.overwrite_chains:
            ssd.swing_bone_chains.clear()
        existing = {c.name for c in ssd.swing_bone_chains}

        chains = detect_chains(arma, only_selected=settings.only_selected,
                               min_length=settings.min_length)
        centers = collision_centers(arma) if settings.assign_collisions else []

        built = skipped = 0
        for chain in chains:
            bones = [arma.bones.get(n) for n in chain.bone_names]
            if any(b is None for b in bones):
                skipped += 1
                continue
            # The _null terminal is not simulated: across all 508 vanilla
            # chains, end_bonename never once ends in _null. K bones on the rig
            # therefore give K-1 swing bones.
            sim = bones[:-1] if _NULL_SUFFIX.search(bones[-1].name) else bones
            if not sim:
                skipped += 1
                continue

            name = chain.family.lower()
            if name in existing:
                skipped += 1
                continue

            new_chain = ssd.swing_bone_chains.add()
            new_chain.name = name
            new_chain.start_bone_name = sim[0].name
            new_chain.end_bone_name = sim[-1].name
            new_chain.is_skirt = chain.part in {'Skirt', 'Mantle', 'Coat', 'Cape'}
            new_chain.rotate_order = 1
            new_chain.curve_rotate_x = False

            # One reach for the whole chain, so stubby tip bones are not
            # starved of collisions. See nearest_collisions.
            chain_scale = max((b.length for b in sim), default=0.0)

            for i, bone in enumerate(sim):
                sb = new_chain.swing_bones.add()
                sb.name = bone.name
                params = parameters_for(chain.part, i, len(sim))
                sb.air_resistance = params['air_resistance']
                sb.water_resistance = params['water_resistance']
                sb.angle_z[0] = radians(params['min_angle_z'])
                sb.angle_z[1] = radians(params['max_angle_z'])
                sb.angle_y[0] = radians(params['min_angle_y'])
                sb.angle_y[1] = radians(params['max_angle_y'])
                sb.collision_size[0] = params['collision_size_root']
                sb.collision_size[1] = params['collision_size_tip']
                sb.friction_rate = params['friction_rate']
                sb.goal_strength = params['goal_strength']
                sb.inertial_mass = params['inertial_mass']
                sb.local_gravity = params['local_gravity']
                sb.fall_speed_scale = params['fall_speed_scale']
                sb.ground_hit = params['ground_hit']
                sb.wind_affect = params['wind_affect']

                for _d, kind, index, _n in nearest_collisions(
                        bone, centers, settings.max_collisions,
                        settings.collision_radius_scale, chain_scale):
                    col = sb.collisions.add()
                    col.collision_type = kind
                    col.collision_index = index

            existing.add(name)
            built += 1

        _reindex(arma)
        msg = 'Built {} chain(s)'.format(built)
        if skipped:
            msg += ', skipped {} (already present or unresolved)'.format(skipped)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class SUB_OP_auto_swing_assign_collisions(Operator):
    bl_idname = 'sub.auto_swing_assign_collisions'
    bl_label = 'Reassign Collisions'
    bl_description = ('Replace the collision list on every existing swing bone '
                      'with the shapes nearest it')
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = _armature(context)
        return ob is not None and len(ob.data.sub_swing_data.swing_bone_chains) > 0

    def execute(self, context):
        ob = _armature(context)
        arma = ob.data
        settings = context.scene.sub_auto_swing
        centers = collision_centers(arma)
        if not centers:
            self.report({'WARNING'}, 'No collision shapes on this armature')
            return {'CANCELLED'}

        touched = 0
        for chain in arma.sub_swing_data.swing_bone_chains:
            chain_bones = [arma.bones.get(sb.name) for sb in chain.swing_bones]
            chain_scale = max((b.length for b in chain_bones if b is not None),
                              default=0.0)
            for swing_bone in chain.swing_bones:
                bone = arma.bones.get(swing_bone.name)
                if bone is None:
                    continue
                swing_bone.collisions.clear()
                for _d, kind, index, _n in nearest_collisions(
                        bone, centers, settings.max_collisions,
                        settings.collision_radius_scale, chain_scale):
                    col = swing_bone.collisions.add()
                    col.collision_type = kind
                    col.collision_index = index
                touched += 1
        self.report({'INFO'}, 'Reassigned collisions on {} swing bone(s)'.format(touched))
        return {'FINISHED'}


class SUB_OP_auto_swing_run_all(Operator):
    bl_idname = 'sub.auto_swing_run_all'
    bl_label = 'Auto Swing Bones'
    bl_description = 'Detect, rename, build chains and assign collisions in one pass'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _armature(context) is not None

    def execute(self, context):
        bpy.ops.sub.auto_swing_detect()
        if not context.scene.sub_auto_swing.proposals:
            self.report({'WARNING'}, 'No swing chains detected')
            return {'CANCELLED'}
        bpy.ops.sub.auto_swing_rename()
        bpy.ops.sub.auto_swing_build()
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class SUB_UL_auto_swing_proposals(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'include', text='')
        row.label(text='{}{}'.format(item.part, item.direction))
        row.label(text='{} bones'.format(len(item.bone_names.split('\n'))))
        if item.warnings:
            row.label(text='', icon='ERROR')


class SUB_PT_auto_swing_bones(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Ultimate'
    bl_label = 'Auto Swing Bones'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return _armature(context) is not None

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sub_auto_swing

        col = layout.column(align=True)
        col.prop(settings, 'only_selected')
        col.prop(settings, 'min_length')
        layout.operator('sub.auto_swing_detect', icon='VIEWZOOM')

        if settings.proposals:
            layout.template_list('SUB_UL_auto_swing_proposals', '', settings,
                                 'proposals', settings, 'active_index', rows=4)
            if 0 <= settings.active_index < len(settings.proposals):
                item = settings.proposals[settings.active_index]
                box = layout.box()
                olds = item.bone_names.split('\n')
                for i, new in enumerate(item.new_names.split('\n')):
                    old = olds[i] if i < len(olds) else '(new bone)'
                    box.label(text='{}  ->  {}'.format(old, new))
                if item.append_null:
                    box.label(text='A _null terminal will be added', icon='INFO')
                if item.warnings:
                    warn = box.box()
                    warn.alert = True
                    warn.label(text=item.warnings, icon='ERROR')
            layout.operator('sub.auto_swing_rename', icon='SORTALPHA')

        layout.separator()
        col = layout.column(align=True)
        col.prop(settings, 'move_blockers')
        col.prop(settings, 'overwrite_chains')
        col.prop(settings, 'assign_collisions')
        sub = col.column(align=True)
        sub.enabled = settings.assign_collisions
        sub.prop(settings, 'max_collisions')
        sub.prop(settings, 'collision_radius_scale')
        layout.operator('sub.auto_swing_build', icon='PHYSICS')
        layout.operator('sub.auto_swing_assign_collisions', icon='MOD_PHYSICS')

        layout.separator()
        layout.operator('sub.auto_swing_run_all', icon='AUTO')
        info = layout.box()
        info.label(text='Physics come from vanilla chains of the', icon='INFO')
        info.label(text='same part and length, per bone position.')
        info.label(text='Tune them in the Swing panel afterwards.')


classes = (
    SUB_PG_auto_swing_proposal,
    SUB_PG_auto_swing,
    SUB_OP_auto_swing_detect,
    SUB_OP_auto_swing_rename,
    SUB_OP_auto_swing_build,
    SUB_OP_auto_swing_assign_collisions,
    SUB_OP_auto_swing_run_all,
    SUB_UL_auto_swing_proposals,
    SUB_PT_auto_swing_bones,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass
    bpy.types.Scene.sub_auto_swing = PointerProperty(type=SUB_PG_auto_swing)


def unregister():
    try:
        del bpy.types.Scene.sub_auto_swing
    except AttributeError:
        pass
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
