import bpy
from bpy.types import PropertyGroup
from bpy.props import StringProperty
from bpy.props import PointerProperty
from bpy.props import BoolProperty
from bpy.props import EnumProperty
from bpy.props import CollectionProperty
from bpy.props import IntProperty

from . import preset_handler


_RESERVED_CUSTOM_ATTRS = frozenset({
    'name', 'entries', 'entries_index', 'ui_version',
    'add_bone', 'remove_bone', 'get_bones', 'has_settings',
    'migrate_legacy_bones', 'sync_all_dynamic_props', '_bump_ui',
    '_clean_identifier', '_ensure_dynamic_prop',
})


def _clean_custom_identifier(identifier):
    return identifier.lower().replace(" ", "_").replace("-", "_").replace(".", "_")


def _custom_bone_entry_update(item, context):
    ob = context.active_object
    if not ob or ob.type != 'ARMATURE':
        return
    custom = ob.data.expykit_retarget.custom
    custom.sync_all_dynamic_props()
    custom._bump_ui()
    if context.screen:
        for area in context.screen.areas:
            area.tag_redraw()


class RetargetBase(PropertyGroup):
    def has_settings(self):
        for k, v in self.items():
            if k == 'name':
                continue
            if v:
                return True
        return False


class RetargetSpine(RetargetBase):
    head: StringProperty(name="head")
    neck: StringProperty(name="neck")
    spine2: StringProperty(name="spine2")
    spine1: StringProperty(name="spine1")
    spine: StringProperty(name="spine")
    hips: StringProperty(name="hips")


class RetargetArm(RetargetBase):
    shoulder: StringProperty(name="shoulder")
    arm: StringProperty(name="arm")
    arm_twist: StringProperty(name="arm_twist")
    arm_twist_02: StringProperty(name="arm_twist_02")
    forearm: StringProperty(name="forearm")
    forearm_twist: StringProperty(name="forearm_twist")
    forearm_twist_02: StringProperty(name="forearm_twist_02")
    hand: StringProperty(name="hand")

    name: StringProperty(default='arm')


class RetargetLeg(RetargetBase):
    upleg: StringProperty(name="upleg")
    upleg_twist: StringProperty(name="upleg_twist")
    upleg_twist_02: StringProperty(name="upleg_twist_02")
    leg: StringProperty(name="leg")
    leg_twist: StringProperty(name="leg_twist")
    leg_twist_02: StringProperty(name="leg_twist_02")
    foot: StringProperty(name="foot")
    toe: StringProperty(name="toe")

    name: StringProperty(default='leg')


class RetargetFinger(RetargetBase):
    meta: StringProperty(name="meta")
    a: StringProperty(name="A")
    b: StringProperty(name="B")
    c: StringProperty(name="C")


class RetargetCustomBone(PropertyGroup):
    identifier: StringProperty(
        name="Identifier",
        description="Unique identifier used to match this bone when binding rigs",
        default="",
        update=_custom_bone_entry_update,
    )
    bone: StringProperty(
        name="Bone",
        description="Bone name on this armature",
        default="",
        update=_custom_bone_entry_update,
    )

    def has_settings(self):
        return bool(self.identifier and self.bone)


class RetargetCustom(RetargetBase):
    name: StringProperty(default='')
    entries: CollectionProperty(type=RetargetCustomBone)
    entries_index: IntProperty(default=0)
    ui_version: IntProperty(default=0)

    def _bump_ui(self):
        self.ui_version += 1

    def _clean_identifier(self, identifier):
        return _clean_custom_identifier(identifier)

    def _ensure_dynamic_prop(self, identifier, bone_name):
        """Keep legacy dynamic properties in sync for preset save/load."""
        if not identifier:
            return
        if not hasattr(self.__class__, identifier):
            prop = StringProperty(name=identifier, default=bone_name)
            setattr(self.__class__, identifier, prop)
        setattr(self, identifier, bone_name)

    def sync_all_dynamic_props(self):
        """Rebuild legacy dynamic properties from the collection entries."""
        self.migrate_legacy_bones()
        active = {}

        for item in self.entries:
            if not item.identifier:
                continue
            identifier = self._clean_identifier(item.identifier)
            if item.identifier != identifier:
                item.identifier = identifier
            active[identifier] = item.bone
            self._ensure_dynamic_prop(identifier, item.bone)

        for prop_name in dir(self):
            if prop_name in _RESERVED_CUSTOM_ATTRS or prop_name.startswith('__') or prop_name.startswith('bl_'):
                continue
            value = getattr(self, prop_name, None)
            if isinstance(value, str) and prop_name not in active:
                setattr(self, prop_name, "")

    def migrate_legacy_bones(self):
        """Move old dynamically-added custom bones into the collection."""
        if self.entries:
            return

        legacy = []
        for prop_name in dir(self):
            if prop_name.startswith('__') or prop_name in _RESERVED_CUSTOM_ATTRS:
                continue
            if prop_name.startswith('bl_'):
                continue
            value = getattr(self, prop_name, None)
            if isinstance(value, str) and value:
                legacy.append((prop_name, value))

        for identifier, bone_name in legacy:
            item = self.entries.add()
            item.identifier = identifier
            item.bone = bone_name

        if legacy:
            self._bump_ui()

    def add_bone(self, identifier, bone_name):
        """Add a custom bone entry with the given identifier."""
        identifier = self._clean_identifier(identifier)
        if not identifier:
            return False

        self.migrate_legacy_bones()

        for item in self.entries:
            if item.identifier == identifier:
                item.bone = bone_name
                self._ensure_dynamic_prop(identifier, bone_name)
                self._bump_ui()
                return True

        item = self.entries.add()
        item.identifier = identifier
        item.bone = bone_name
        self._ensure_dynamic_prop(identifier, bone_name)
        self._bump_ui()
        return True

    def remove_bone(self, identifier):
        """Remove a custom bone entry with the given identifier."""
        self.migrate_legacy_bones()
        for index, item in enumerate(self.entries):
            if item.identifier == identifier:
                self.entries.remove(index)
                if hasattr(self, identifier):
                    setattr(self, identifier, "")
                self._bump_ui()
                return True
        return False

    def get_bones(self):
        """Get all custom bone entries as (identifier, bone_name) pairs."""
        self.migrate_legacy_bones()
        return [(item.identifier, item.bone) for item in self.entries if item.identifier and item.bone]

    def has_settings(self):
        """Check if any custom bones are defined."""
        return bool(self.get_bones()) or bool(self.name)


class RetargetFingers(PropertyGroup):
    thumb: PointerProperty(type=RetargetFinger)
    index: PointerProperty(type=RetargetFinger)
    middle: PointerProperty(type=RetargetFinger)
    ring: PointerProperty(type=RetargetFinger)
    pinky: PointerProperty(type=RetargetFinger)

    name: StringProperty(default='fingers')

    def has_settings(self):
        for setting in (self.thumb, self.index, self.middle, self.ring, self.pinky):
            if setting.has_settings():
                return True

        return False


class RetargetFaceSimple(PropertyGroup):
    jaw: StringProperty(name="jaw")
    left_eye: StringProperty(name="left_eye")
    right_eye: StringProperty(name="right_eye")

    left_upLid: StringProperty(name="left_upLid")
    right_upLid: StringProperty(name="right_upLid")

    super_copy: BoolProperty(default=True)


class RetargetSettings(PropertyGroup):
    face: PointerProperty(type=RetargetFaceSimple)
    spine: PointerProperty(type=RetargetSpine)

    left_arm: PointerProperty(type=RetargetArm)
    left_arm_ik: PointerProperty(type=RetargetArm)
    left_fingers: PointerProperty(type=RetargetFingers)

    right_arm: PointerProperty(type=RetargetArm)
    right_arm_ik: PointerProperty(type=RetargetArm)
    right_fingers: PointerProperty(type=RetargetFingers)

    left_leg: PointerProperty(type=RetargetLeg)
    left_leg_ik: PointerProperty(type=RetargetLeg)
    right_leg: PointerProperty(type=RetargetLeg)
    right_leg_ik: PointerProperty(type=RetargetLeg)

    custom: PointerProperty(type=RetargetCustom)

    root: StringProperty(name="root")
    throw: StringProperty(name="throw")

    active_preset: StringProperty(
        name="Active Preset",
        description="Filename of the last applied retarget preset for this armature",
        default="",
    )

    def has_settings(self):
        for setting in (self.spine, self.left_arm, self.left_arm_ik, self.left_fingers,
                        self.right_arm, self.right_arm_ik, self.right_fingers,
                        self.left_leg, self.left_leg_ik, self.right_leg, self.right_leg_ik,
                        self.custom):
            if setting.has_settings():
                return True

        if self.root or self.throw:
            return True

        return False

    deform_preset: EnumProperty(items=preset_handler.iterate_presets, name="Deformation Bones")


def register_classes():
    bpy.utils.register_class(RetargetSpine)
    bpy.utils.register_class(RetargetArm)
    bpy.utils.register_class(RetargetLeg)
    bpy.utils.register_class(RetargetFinger)
    bpy.utils.register_class(RetargetFingers)
    bpy.utils.register_class(RetargetFaceSimple)
    bpy.utils.register_class(RetargetCustomBone)
    bpy.utils.register_class(RetargetCustom)

    bpy.utils.register_class(RetargetSettings)
    bpy.types.Armature.expykit_retarget = PointerProperty(type=RetargetSettings)
    bpy.types.Armature.expykit_twist_on = BoolProperty(default=False)


def unregister_classes():
    del bpy.types.Armature.expykit_retarget
    del bpy.types.Armature.expykit_twist_on

    bpy.utils.unregister_class(RetargetSettings)

    bpy.utils.unregister_class(RetargetFaceSimple)
    bpy.utils.unregister_class(RetargetFingers)
    bpy.utils.unregister_class(RetargetFinger)
    bpy.utils.unregister_class(RetargetSpine)
    bpy.utils.unregister_class(RetargetCustomBone)
    bpy.utils.unregister_class(RetargetCustom)

    bpy.utils.unregister_class(RetargetArm)
    bpy.utils.unregister_class(RetargetLeg)
