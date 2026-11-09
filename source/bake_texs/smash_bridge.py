"""Everything bake_texs needs from outside its own folder.

bake_texs runs two ways:

  - inside DirectWorkflow (source/bake_texs), where the Smash importer, the
    material groups and the shader database live next door, and
  - as the standalone "HB Smash PBR" add-on (tools/build_hb_smash_pbr.py),
    where the package is the add-on itself and those modules are copied into
    _vendor/ and the texture tool into bin/.

Only this module knows which one it is; the rest of the package imports from
here. Small helpers are kept here outright so they are the same both ways.
"""
import importlib
import os
from pathlib import Path
from sys import platform

import bpy
import numpy as np

_PARENT = __package__.rpartition('.')[0]


def _parent_module(name):
    """source/<name> when running inside DirectWorkflow, else None."""
    if not _PARENT:
        return None
    try:
        return importlib.import_module(f'{_PARENT}.{name}')
    except ImportError:
        return None


STANDALONE = _parent_module('material_grouping') is None

if STANDALONE:
    from ._vendor import material_grouping
    from ._vendor import shader_info
else:
    material_grouping = _parent_module('material_grouping')
    shader_info = _parent_module('model.material.shader_info')

MATERIAL_MERGE_SUFFIXES = material_grouping.MATERIAL_MERGE_SUFFIXES
MATERIAL_GROUPS = material_grouping.MATERIAL_GROUPS
SIDE_LOAD_SUFFIX = material_grouping.SIDE_LOAD_SUFFIX
group_key_for = material_grouping.group_key_for
side_loaded_name = material_grouping.side_loaded_name


# =============================================================================
# Smash material data (sub_matl_data is registered by the DirectWorkflow
# importer; without it every material reads as plain PBR)
# =============================================================================
# Collections Blender leaves out of the file while they are empty.
_COLLECTIONS = ('bools', 'floats', 'vectors', 'textures', 'samplers',
                'vertex_attributes', 'linked_materials')


class _RawGroup:
    """Attribute access to sub_matl_data saved in a .blend when the DirectWorkflow
    property groups are not registered, so a fighter imported there still bakes
    with its real shader rules here. Reads and writes go to the ID properties."""

    __slots__ = ('_group',)

    def __init__(self, group):
        object.__setattr__(self, '_group', group)

    def __getattr__(self, name):
        try:
            value = self._group[name]
        except (KeyError, TypeError):
            if name in ('shader_label', 'param_id_name', 'name', 'ui_name'):
                return ''
            if name in _COLLECTIONS:
                return _RawCollection(())
            raise AttributeError(name) from None
        return _wrap(value)

    def __setattr__(self, name, value):
        self._group[name] = value


class _RawCollection:
    __slots__ = ('_items',)

    def __init__(self, items):
        self._items = [_RawGroup(item) for item in items]

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def get(self, key, default=None):
        for item in self._items:
            if item.name == key or item.param_id_name == key:
                return item
        return default


def _wrap(value):
    if isinstance(value, bpy.types.ID):
        return value  # e.g. a texture slot's image: an ID, not a nested group
    if hasattr(value, 'keys'):
        return _RawGroup(value)
    if isinstance(value, (list, tuple)) and value and hasattr(value[0], 'keys'):
        return _RawCollection(value)
    if isinstance(value, (list, tuple)) and not value:
        return _RawCollection(())
    return value


def _saved_property(owner, name):
    """An add-on property saved on an ID, read without the add-on registered.

    Blender 5 keeps add-on (RNA) properties in a system store apart from user
    custom properties; owner[name] there is at most a stale pre-5.0 copy, so
    it is only read when the system store has nothing.
    """
    getter = getattr(owner, 'bl_system_properties_get', None)
    if getter is not None:
        try:
            store = getter()
        except (TypeError, RuntimeError):
            store = None
        if store is not None and name in store:
            return store[name]
    try:
        return owner.get(name)
    except TypeError:
        return None


def matl_data(material):
    """A material's Smash material data (DirectWorkflow's sub_matl_data), or None."""
    if material is None:
        return None
    data = getattr(material, 'sub_matl_data', None)
    if data is not None:
        return data
    raw = _saved_property(material, 'sub_matl_data')
    return _RawGroup(raw) if raw is not None else None


def has_sub_matl_data(material):
    data = matl_data(material)
    return data is not None and data.shader_label != ''


def get_side_loaded_twin(material):
    """The existing side-loaded twin of a material, or None."""
    return bpy.data.materials.get(side_loaded_name(material.name))


def prefer_side_loaded(context=None):
    """Whether export reads side-loaded twins (a DirectWorkflow scene setting)."""
    scene = (context or bpy.context).scene
    props = getattr(scene, 'sub_scene_properties', None)
    if props is None:
        # Saved by DirectWorkflow but not registered here: read the raw value.
        raw = _saved_property(scene, 'sub_scene_properties')
        if raw is None:
            return False
        props = _RawGroup(raw)
        try:
            return bool(props.export_prefer_sideloaded_materials)
        except AttributeError:
            return False
    return bool(getattr(props, 'export_prefer_sideloaded_materials', False))


def resolve_material_data_source(material, prefer_twin):
    """Which material's sub_matl_data export reads for `material`."""
    if not prefer_twin:
        return material
    twin = get_side_loaded_twin(material)
    if twin is not None and has_sub_matl_data(twin):
        return twin
    return material


# =============================================================================
# Texture tool
# =============================================================================
def _bundled_tex_dir():
    if STANDALONE:
        return Path(__file__).parent / 'bin'
    return Path(__file__).parent.parent.parent / 'dependencies' / 'ultimate_tex'


def get_ultimate_tex_path() -> Path:
    base = _bundled_tex_dir()
    if platform.startswith('win'):
        sub = Path('win') / 'ultimate_tex_cli.exe'
    elif platform.startswith('lin'):
        sub = Path('linux') / 'ultimate_tex_cli'
    elif platform.startswith('dar'):
        import platform as _platform
        arch = 'arm64' if _platform.processor() == 'arm' else 'x86'
        sub = Path('macos') / arch / 'ultimate_tex_cli'
    else:
        raise RuntimeError(f'Unknown platform `{platform}`')
    path = base / sub
    if path.is_file() and os.name != 'nt' and not os.access(path, os.X_OK):
        try:
            path.chmod(path.stat().st_mode | 0o111)
        except OSError:
            pass
    return path


# =============================================================================
# Pixels
# =============================================================================
def linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)


def resize(arr, width, height):
    """Box filter down (or nearest up) to (height, width)."""
    h, w = arr.shape[:2]
    if (w, h) == (width, height):
        return arr
    if w % width == 0 and h % height == 0:
        fy, fx = h // height, w // width
        shape = (height, fy, width, fx) + arr.shape[2:]
        return arr.reshape(shape).mean(axis=(1, 3)).astype(np.float32)
    ys = np.minimum((np.arange(height) + 0.5) * h / height, h - 1).astype(np.int64)
    xs = np.minimum((np.arange(width) + 0.5) * w / width, w - 1).astype(np.int64)
    return arr[ys][:, xs]


def encode_png(path, rgba8):
    import struct
    import zlib
    h, w = rgba8.shape[:2]
    rows = np.empty((h, w * 4 + 1), dtype=np.uint8)
    rows[:, 0] = 0
    rows[:, 1:] = rgba8.reshape(h, w * 4)

    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF))

    with open(path, 'wb') as handle:
        handle.write(b'\x89PNG\r\n\x1a\n')
        handle.write(chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)))
        handle.write(chunk(b'IDAT', zlib.compress(rows.tobytes(), 1)))
        handle.write(chunk(b'IEND', b''))


def master_node(material):
    """The HB Master Shader node of a material, or None."""
    if material is None or not getattr(material, 'use_nodes', False):
        return None
    tree = getattr(material, 'node_tree', None)
    if tree is None:
        return None
    from .master_shader import is_master_group
    for node in tree.nodes:
        if getattr(node, 'type', '') == 'GROUP' and is_master_group(getattr(node, 'node_tree', None)):
            return node
    return None
