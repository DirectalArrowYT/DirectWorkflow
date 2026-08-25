"""
Material linter - catches the mistakes that only show up in game.

Most of these are things that export happily writes and the game then renders
wrong, so there is nothing to notice until the model is on a Switch. The rules
are ported from SSBH Editor's src/validation.rs, with extra ones that are only
possible now that the shader info dump is available (see shader_info.py): what
a shader actually reads, whether it premultiplies alpha, whether it alpha tests.

Nothing here edits anything. check_material() returns Issue objects and the
caller decides what to do with them.
"""

from . import shader_info

# Severity, worst first.
ERROR = 'ERROR'
WARNING = 'WARNING'
INFO = 'INFO'

_SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}

# Blender image colour space per texture param. Colour maps are sRGB, data
# maps must stay linear or their values are silently gamma-shifted.
# Matches expects_srgb() in SSBH Editor's validation.rs.
NON_COLOR_TEXTURE_PARAMS = {'Texture2', 'Texture4', 'Texture6', 'Texture7', 'Texture16'}

# Cube map slots. These are normally left pointing at "#replace_cubemap" so the
# game substitutes the stage's own reflection probe, so an empty one is the
# expected state rather than something to warn about.
_CUBE_MAP_TEXTURES = {'Texture2', 'Texture7', 'Texture8'}


class Issue:
    def __init__(self, severity, message, material_name='', fix_hint=''):
        self.severity = severity
        self.message = message
        self.material_name = material_name
        self.fix_hint = fix_hint

    def __repr__(self):
        return f'[{self.severity}] {self.material_name}: {self.message}'


def _sort_key(issue):
    return (_SEVERITY_ORDER.get(issue.severity, 9), issue.material_name, issue.message)


def expects_srgb(texture_param_name):
    """True when this texture slot holds colour rather than data."""
    return texture_param_name not in NON_COLOR_TEXTURE_PARAMS


def check_material(material, mesh_objects=()):
    """Every problem found on one material.

    mesh_objects: the mesh objects using this material. Needed for the checks
    that compare what the shader reads against what the geometry actually has;
    pass nothing to skip those.
    """
    issues = []
    sub_matl_data = getattr(material, 'sub_matl_data', None)
    if sub_matl_data is None or not sub_matl_data.shader_label:
        return issues

    name = material.name
    shader_label = sub_matl_data.shader_label

    def add(severity, message, fix_hint=''):
        issues.append(Issue(severity, message, name, fix_hint))

    # --- Shader label ---------------------------------------------------------
    if not shader_info.exists(shader_label):
        add(ERROR,
            f'Shader label "{shader_label}" is not a real shader program.',
            'Pick a preset, or use Find Shader to search for a valid label.')
        # Every check below reads the shader database, so there is nothing
        # further that can be said about this material.
        return issues

    # --- Alpha blending -------------------------------------------------------
    # A premultiplied shader has already multiplied colour by alpha. Asking the
    # blender to multiply by source alpha as well applies it twice, which reads
    # as a transparent surface that is also too dark.
    if shader_info.is_premultiplied(shader_label):
        for blend_state in sub_matl_data.blend_states:
            if blend_state.source_color == 'SourceAlpha':
                add(ERROR,
                    f'Shader premultiplies alpha but Source Color is "SourceAlpha", '
                    f'so alpha gets applied twice.',
                    'Set Source Color to "One".')

    # --- Textures -------------------------------------------------------------
    used_textures = shader_info.used_textures(shader_label)
    for texture in sub_matl_data.textures:
        param = texture.param_id_name

        if texture.image is None:
            if param not in _CUBE_MAP_TEXTURES:
                add(WARNING,
                    f'{param} ({texture.ui_name}) has no image assigned.',
                    'Assign an image, or the game falls back to a default texture.')
            continue

        # Colour space. Baking a normal or PRM map as sRGB is a common and
        # near-invisible mistake in Blender - it looks close enough on screen
        # and is wrong by a gamma curve everywhere.
        colorspace = ''
        try:
            colorspace = texture.image.colorspace_settings.name
        except Exception:
            pass
        if colorspace:
            is_srgb_image = colorspace == 'sRGB'
            if expects_srgb(param) and not is_srgb_image:
                add(WARNING,
                    f'{param} holds colour but its image is "{colorspace}".',
                    'Set the image colour space to sRGB.')
            elif not expects_srgb(param) and is_srgb_image:
                add(ERROR,
                    f'{param} holds data, not colour, but its image is sRGB.',
                    'Set the image colour space to Non-Color.')

        # A texture wired to a slot the shader never samples is wasted work,
        # and usually means the wrong shader was chosen.
        if param not in used_textures:
            add(INFO,
                f'{param} has an image but shader {shader_info.base_label(shader_label)} '
                f'never reads it.',
                'Either switch to a shader that uses it, or clear the slot.')

    assigned = {t.param_id_name for t in sub_matl_data.textures}
    for param in sorted(used_textures - assigned):
        add(WARNING,
            f'Shader reads {param} but the material has no such slot.',
            'Re-apply the preset to rebuild the material\'s parameter list.')

    # --- PRM alpha: specular vs anisotropic rotation --------------------------
    # Documented in Smush-Material-Research/Textures.md. While CustomFloat10 is
    # non-zero the shader reinterprets PRM alpha as a highlight rotation angle,
    # 0.0-1.0 mapped onto 0-180 degrees. A specular map baked into that channel
    # becomes a field of random rotations.
    aniso = sub_matl_data.floats.get('CustomFloat10')
    if aniso is not None and abs(aniso.value) > 1e-6:
        add(INFO,
            f'CustomFloat10 is {aniso.value:.3g}, so PRM alpha is read as '
            f'anisotropic rotation (0-180 degrees), not specular.',
            'Bake PRM alpha from rotation, not specular. The baker does this '
            'automatically.')

    # CustomBoolean1 off means the shader ignores PRM alpha entirely and uses a
    # hardcoded 0.16, so any specular authored there does nothing.
    cb1 = sub_matl_data.bools.get('CustomBoolean1')
    if cb1 is not None and not cb1.value:
        add(INFO,
            'CustomBoolean1 is off, so PRM alpha is ignored and specular is a '
            'flat 0.16.',
            'Turn CustomBoolean1 on to use the PRM alpha channel.')

    # --- Subsurface scattering ------------------------------------------------
    # On an SSS shader the PRM red channel stops being metalness and becomes
    # the SSS mask, so the two settings have to agree.
    has_sss = shader_info.uses_param(shader_label, 'CustomVector30')
    if has_sss:
        cv30 = sub_matl_data.vectors.get('CustomVector30')
        if cv30 is not None and abs(cv30.value[0]) < 1e-6:
            add(WARNING,
                'This is a subsurface shader but CustomVector30.x (the blend '
                'factor) is 0, so the effect is off.',
                'Set CustomVector30.x to around 0.5, as vanilla skin does.')
        if not shader_info.uses_param(shader_label, 'CustomVector11'):
            pass  # Some SSS shaders take the colour from elsewhere.
        elif sub_matl_data.vectors.get('CustomVector11') is None:
            add(WARNING,
                'Subsurface shader is missing CustomVector11, its subsurface colour.',
                'Re-apply the Skin preset.')

    # --- Vertex attributes ----------------------------------------------------
    # A shader that reads colorSet1 from a mesh with no such layer gets
    # garbage, and this is the single most common cause of a model that
    # imports fine and renders black in game.
    required_attributes = shader_info.used_attributes(shader_label)
    for obj in mesh_objects:
        if obj.type != 'MESH':
            continue
        available = set()
        available.update(uv.name for uv in obj.data.uv_layers)
        available.update(attribute.name for attribute in obj.data.color_attributes)
        missing = sorted(a for a in required_attributes if a not in available)
        if missing:
            add(ERROR,
                f'Mesh "{obj.name}" is missing {", ".join(missing)}, which this '
                f'shader reads.',
                'Add the missing UV map or colour attribute, or switch to a '
                'shader that does not need it.')

    # --- Samplers -------------------------------------------------------------
    for sampler in sub_matl_data.samplers:
        if sampler.anisotropic_filtering and (
            sampler.min_filter == 'Nearest' or sampler.mag_filter == 'Nearest'
        ):
            add(WARNING,
                f'{sampler.param_id_name} enables anisotropic filtering with a '
                f'Nearest filter mode, which conflict.',
                'Turn anisotropy off, or use only Linear filter modes.')

    return sorted(issues, key=_sort_key)


def check_materials(materials_to_meshes):
    """Run every check over several materials at once.

    materials_to_meshes: dict of material -> list of mesh objects using it.
    Also catches problems that only exist between materials, like duplicate
    names, which export cannot represent.
    """
    issues = []
    seen_names = {}

    for material, mesh_objects in materials_to_meshes.items():
        issues.extend(check_material(material, mesh_objects))
        # Material labels become matl entry names, which must be unique.
        key = material.name.split('.')[0]
        seen_names.setdefault(key, []).append(material.name)

    for key, names in seen_names.items():
        if len(names) > 1:
            issues.append(Issue(
                ERROR,
                f'{len(names)} materials share the exported name "{key}": '
                f'{", ".join(sorted(names))}.',
                key,
                'Give each material a distinct name. Blender\'s .001 suffixes '
                'are stripped on export, so these would collide.',
            ))

    return sorted(issues, key=_sort_key)


def collect_materials(objects):
    """Map every material on these objects to the meshes that use it."""
    result = {}
    for obj in objects:
        if obj.type != 'MESH':
            continue
        for slot in obj.material_slots:
            if slot.material is None:
                continue
            result.setdefault(slot.material, []).append(obj)
    return result


def counts(issues):
    """(errors, warnings, infos)"""
    return (
        sum(1 for i in issues if i.severity == ERROR),
        sum(1 for i in issues if i.severity == WARNING),
        sum(1 for i in issues if i.severity == INFO),
    )


# Results of the last run, so the panel can show them without re-checking on
# every redraw. Transient UI state - deliberately not a scene property, since
# there is no reason for it to survive a file save.
last_results = {
    'issues': [],
    'material_count': 0,
    'has_run': False,
}


def store_results(issues, material_count):
    last_results['issues'] = issues
    last_results['material_count'] = material_count
    last_results['has_run'] = True


def clear_results():
    last_results['issues'] = []
    last_results['material_count'] = 0
    last_results['has_run'] = False
