"""
Merge one rig's bones into another.

Costume slots for the same fighter share animations - c81 plays what c80 was
animated with, and the game simply ignores any bone an animation names that a
given model does not have. That tolerance is what makes alternate slots
practical, and it is also what makes them easy to get subtly wrong: a bone that
exists only on c81 can never be animated, because the animation is authored
against c80's skeleton and there is nothing there to key.

This takes the union of two skeletons so one rig can drive every slot. Bones
present in the source but missing in the target are added; bones already
present are left completely alone, so an existing skeleton is never disturbed
or duplicated.

WHAT IT DELIBERATELY DOES NOT DO
  Existing bones keep their rest position even when the source disagrees. A
  shared bone in a different place is a real conflict and quietly moving it
  would invalidate every animation and every mesh already weighted to it, so
  the mismatch is reported and left for a human. Nothing is reparented either
  - only genuinely new bones get a parent assigned.

  New bones are appended after the existing ones rather than interleaved. Bone
  order is part of the .nusktb, and export's "Order & Values" linkage expects
  the vanilla bones to stay where they were; appending keeps that intact.
"""

import bpy
from bpy.types import Operator
from bpy.props import BoolProperty, PointerProperty, EnumProperty, StringProperty

from ..blender_compat import ensure_bone_collection, assign_bone_to_collection


def _hierarchy_order(armature_data, names):
    """`names` sorted so a bone always follows its parent.

    A new bone cannot be parented until its parent exists, and a slot can
    easily add a whole chain at once - a new accessory with its own children -
    so inserting them in arbitrary order would leave part of the chain
    unparented.
    """
    ordered = []
    placed = set()

    def place(name):
        if name in placed or name not in names:
            return
        bone = armature_data.bones.get(name)
        if bone is not None and bone.parent is not None:
            place(bone.parent.name)
        if name not in placed:
            placed.add(name)
            ordered.append(name)

    for name in sorted(names):
        place(name)
    return ordered


# Bones that are almost always Blender animation controls rather than game
# bones. Not excluded automatically - Shigaraki's own c81 has all of these in
# its exported skeleton, so somebody may well be relying on them - but they are
# worth pointing at, because copying rig controls between costume slots is
# rarely what anyone means by merging skeletons.
CONTROL_BONE_HINTS = ('IK', 'BL_EyeLook', '_eye', 'Pole', 'CTRL', 'MCH-', 'ORG-', 'DEF-')


def looks_like_control_bone(name):
    return any(hint in name for hint in CONTROL_BONE_HINTS)


def _excluded(name, patterns):
    """True when a bone name matches any comma-separated wildcard pattern."""
    import fnmatch
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        if fnmatch.fnmatchcase(name, pattern if '*' in pattern or '?' in pattern
                               else f'*{pattern}*'):
            return True
    return False


def compare_rigs(source_obj, target_obj, position_tolerance=1e-4, exclude=''):
    """What merging would do, without doing any of it."""
    source_bones = {bone.name: bone for bone in source_obj.data.bones}
    target_bones = {bone.name: bone for bone in target_obj.data.bones}

    patterns = exclude.split(',') if exclude else []
    missing = {n for n in set(source_bones) - set(target_bones)
               if not _excluded(n, patterns)}
    extra = set(target_bones) - set(source_bones)
    shared = set(source_bones) & set(target_bones)

    # Rest position is compared in each armature's own space. Comparing world
    # space instead would flag every bone whenever the two objects merely sit
    # at different places in the scene, which says nothing about the skeletons.
    mismatched = []
    for name in sorted(shared):
        source_bone, target_bone = source_bones[name], target_bones[name]
        head_delta = (source_bone.head_local - target_bone.head_local).length
        tail_delta = (source_bone.tail_local - target_bone.tail_local).length
        worst = max(head_delta, tail_delta)
        if worst > position_tolerance:
            mismatched.append((name, worst))
    mismatched.sort(key=lambda item: -item[1])

    reparented = []
    for name in sorted(shared):
        source_parent = source_bones[name].parent
        target_parent = target_bones[name].parent
        source_parent_name = source_parent.name if source_parent else None
        target_parent_name = target_parent.name if target_parent else None
        if source_parent_name != target_parent_name:
            reparented.append((name, target_parent_name, source_parent_name))

    return {
        'missing': missing,
        'extra': extra,
        'shared': shared,
        'mismatched': mismatched,
        'different_parents': reparented,
    }


def merge_rigs(source_obj, target_obj, copy_collections=True, exclude=''):
    """Add the source's missing bones to the target. Returns a result dict."""
    report = compare_rigs(source_obj, target_obj, exclude=exclude)
    missing = report['missing']
    if not missing:
        report['added'] = []
        return report

    source_data = source_obj.data
    order = _hierarchy_order(source_data, missing)

    previous_mode = target_obj.mode
    previous_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = target_obj
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = target_obj.data.edit_bones
    added = []
    orphaned = []
    try:
        for name in order:
            source_bone = source_data.bones[name]
            new_bone = edit_bones.new(name)
            # edit_bones.new() can rename on collision, which would silently
            # create the duplicate this whole operator exists to avoid.
            if new_bone.name != name:
                edit_bones.remove(new_bone)
                continue

            new_bone.head = source_bone.head_local
            new_bone.tail = source_bone.tail_local
            new_bone.roll = _bone_roll(source_bone)
            new_bone.use_deform = source_bone.use_deform
            added.append(name)

        # Parenting is a second pass so it cannot depend on creation order at
        # all - by now every new bone exists, and any parent that was already
        # in the target has been there from the start.
        for name in added:
            source_bone = source_data.bones[name]
            if source_bone.parent is None:
                continue
            parent = edit_bones.get(source_bone.parent.name)
            if parent is None:
                orphaned.append(name)
                continue
            new_bone = edit_bones.get(name)
            new_bone.parent = parent
            # Connecting a bone snaps its head onto the parent's tail, so it is
            # only safe where the source already had them coincident.
            if source_bone.use_connect:
                if (source_bone.head_local - source_bone.parent.tail_local).length < 1e-5:
                    new_bone.use_connect = True
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
        if previous_active is not None:
            bpy.context.view_layer.objects.active = previous_active
        if previous_mode != 'OBJECT' and target_obj.mode != previous_mode:
            try:
                bpy.ops.object.mode_set(mode=previous_mode)
            except Exception:
                pass

    if copy_collections:
        _copy_bone_collections(source_obj, target_obj, added)

    report['added'] = added
    report['orphaned'] = orphaned
    return report


def _bone_roll(bone):
    """A rest bone's roll, which only edit bones expose directly."""
    try:
        return bone.AxisRollFromMatrix(bone.matrix_local.to_3x3())[1]
    except Exception:
        return 0.0


def _copy_bone_collections(source_obj, target_obj, names):
    """Put newly added bones in the same named collections as in the source."""
    source_collections = getattr(source_obj.data, 'collections', None)
    if source_collections is None:
        return

    for name in names:
        source_bone = source_obj.data.bones.get(name)
        target_bone = target_obj.data.bones.get(name)
        if source_bone is None or target_bone is None:
            continue
        for collection in getattr(source_bone, 'collections', ()):
            target_collection = ensure_bone_collection(target_obj.data, collection.name)
            assign_bone_to_collection(target_collection, target_bone)


def _armature_items(self, context):
    items = [(o.name, o.name, '') for o in bpy.data.objects if o.type == 'ARMATURE']
    return items or [('', 'No armatures in this file', '')]


class SUB_OP_merge_rigs(Operator):
    """Add another rig's missing bones to this one, without duplicating any"""
    bl_idname = 'sub.merge_rigs'
    bl_label = 'Merge Rigs'
    bl_description = (
        'Add every bone the source rig has that the target lacks, so one '
        'skeleton can drive several costume slots. Bones already present are '
        'left untouched - nothing is duplicated, moved or reparented'
    )
    bl_options = {'REGISTER', 'UNDO'}

    source: EnumProperty(
        name='Source (take bones from)',
        description='The rig whose extra bones get copied. This rig is not modified',
        items=_armature_items,
    )
    target: EnumProperty(
        name='Target (add bones to)',
        description='The rig that receives the missing bones. This is the one that changes',
        items=_armature_items,
    )
    copy_collections: BoolProperty(
        name='Copy Bone Collections',
        description='Put new bones in the same named bone collections they were in on the source',
        default=True,
    )
    exclude: StringProperty(
        name='Skip Bones Matching',
        description=(
            'Comma-separated names or wildcards to leave behind, e.g. '
            '"IK,*_eye,BL_EyeLook". A bare word matches anywhere in the name. '
            'Use this to keep Blender rig controls out of the game skeleton'
        ),
        default='',
    )
    dry_run: BoolProperty(
        name='Preview Only',
        description='Report what would be added without changing anything',
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return sum(1 for o in bpy.data.objects if o.type == 'ARMATURE') >= 2

    def invoke(self, context, event):
        # Follow Blender's join convention where the active object is the one
        # that receives: selected other armature is the source, active is the
        # target.
        armatures = [o for o in context.selected_objects if o.type == 'ARMATURE']
        active = context.view_layer.objects.active
        if active is not None and active.type == 'ARMATURE':
            self.target = active.name
            others = [o for o in armatures if o is not active]
            if others:
                self.source = others[0].name
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'source')
        layout.prop(self, 'target')

        source_obj = bpy.data.objects.get(self.source)
        target_obj = bpy.data.objects.get(self.target)

        if source_obj is None or target_obj is None:
            return
        if source_obj is target_obj:
            layout.label(text='Source and target are the same rig.', icon='ERROR')
            return

        report = compare_rigs(source_obj, target_obj, exclude=self.exclude)
        box = layout.box()
        box.label(text=f'{len(report["missing"])} bone(s) would be added to '
                       f'{target_obj.name}', icon='BONE_DATA')
        box.label(text=f'{len(report["shared"])} shared, left untouched')
        if report['extra']:
            box.label(text=f'{len(report["extra"])} bone(s) only in the target, kept')

        preview = sorted(report['missing'])[:6]
        for name in preview:
            box.label(text=f'   + {name}')
        if len(report['missing']) > len(preview):
            box.label(text=f'   ...and {len(report["missing"]) - len(preview)} more')

        if report['mismatched']:
            warn = layout.box()
            warn.label(text=f'{len(report["mismatched"])} shared bone(s) sit in '
                            f'different places', icon='ERROR')
            warn.label(text='These are NOT moved - existing weights and')
            warn.label(text='animation would break. See the console.')

        if report['different_parents']:
            warn = layout.box()
            warn.label(text=f'{len(report["different_parents"])} shared bone(s) have '
                            f'a different parent', icon='ERROR')
            warn.label(text='These are NOT reparented. See the console.')

        controls = sorted(n for n in report['missing'] if looks_like_control_bone(n))
        if controls:
            hint = layout.box()
            hint.label(text=f'{len(controls)} of these look like rig controls, '
                            f'not game bones:', icon='INFO')
            hint.label(text='   ' + ', '.join(controls[:6])
                            + (' ...' if len(controls) > 6 else ''))
            hint.label(text='Add them to "Skip Bones Matching" to leave them out.')

        layout.prop(self, 'exclude')
        layout.prop(self, 'copy_collections')
        layout.prop(self, 'dry_run')

    def execute(self, context):
        source_obj = bpy.data.objects.get(self.source)
        target_obj = bpy.data.objects.get(self.target)

        if source_obj is None or target_obj is None:
            self.report({'ERROR'}, 'Pick two armatures.')
            return {'CANCELLED'}
        if source_obj is target_obj:
            self.report({'ERROR'}, 'Source and target are the same rig.')
            return {'CANCELLED'}

        if self.dry_run:
            report = compare_rigs(source_obj, target_obj, exclude=self.exclude)
            report['added'] = []
        else:
            report = merge_rigs(source_obj, target_obj, self.copy_collections, self.exclude)

        self._print(source_obj, target_obj, report)

        added = len(report.get('added', []))
        if self.dry_run:
            message = (f'Preview: {len(report["missing"])} bone(s) would be added to '
                       f'{target_obj.name}, {len(report["shared"])} already shared')
        elif added:
            message = f'Added {added} bone(s) to {target_obj.name}'
        else:
            message = f'{target_obj.name} already has every bone {source_obj.name} has'

        problems = len(report['mismatched']) + len(report['different_parents'])
        if problems:
            message += f'; {problems} conflict(s) left alone - see the console'
        if report.get('orphaned'):
            message += f'; {len(report["orphaned"])} could not be parented'

        self.report({'WARNING' if problems else 'INFO'}, message)
        return {'FINISHED'}

    def _print(self, source_obj, target_obj, report):
        print('\n' + '=' * 70)
        print(f'Merge Rigs: {source_obj.name} -> {target_obj.name}'
              + ('  (preview only)' if self.dry_run else ''))
        print('=' * 70)
        print(f'shared bones          {len(report["shared"])}')
        print(f'only in source        {len(report["missing"])}'
              + ('  (added)' if report.get('added') else ''))
        print(f'only in target        {len(report["extra"])}  (kept)')

        if report['missing']:
            print('\nbones from the source:')
            for name in sorted(report['missing']):
                print(f'  + {name}')

        if report['mismatched']:
            print(f'\n{len(report["mismatched"])} shared bone(s) at a different rest '
                  f'position - NOT moved:')
            for name, delta in report['mismatched'][:20]:
                print(f'  ! {name}  differs by {delta:.5f}')
            if len(report['mismatched']) > 20:
                print(f'  ...and {len(report["mismatched"]) - 20} more')

        if report['different_parents']:
            print(f'\n{len(report["different_parents"])} shared bone(s) with a different '
                  f'parent - NOT reparented:')
            for name, target_parent, source_parent in report['different_parents'][:20]:
                print(f'  ! {name}: target={target_parent} source={source_parent}')

        if report.get('orphaned'):
            print(f'\ncould not be parented: {", ".join(report["orphaned"])}')
        print('=' * 70 + '\n')
