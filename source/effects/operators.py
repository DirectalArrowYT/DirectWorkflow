import json
import math
import os
import queue
import subprocess
import threading
import time

import bpy
from bpy.types import Operator
from bpy.props import BoolProperty, IntProperty
from mathutils import Euler, Matrix

from . import acmd, cards, forge, geometry, layering, lights
from .properties import refresh_list


def _active_fx(context):
    obj = context.object
    return obj if obj is not None and obj.smash_fx.enabled else None


class SUB_OP_smash_fx_add(Operator):
    bl_idname = 'sub.smash_fx_add'
    bl_label = 'Add Effect'
    bl_description = ("Add an empty that plays a Smash effect where it is. With a bone active in Pose Mode, "
                      "it follows that bone the way the game's EFFECT_FOLLOW does")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.sub_smash_fx
        if settings.source == 'CUSTOM':
            eff = settings.custom_eff
            if not eff:
                self.report({'ERROR'}, 'Pick a custom .eff file first')
                return {'CANCELLED'}
        else:
            eff = forge.vanilla_eff(settings.game_root, settings.source)
        if not os.path.isfile(bpy.path.abspath(eff)):
            self.report({'ERROR'}, f'No effect file at {eff}')
            return {'CANCELLED'}

        armature = context.object if context.object and context.object.type == 'ARMATURE' else None
        bone = context.active_pose_bone if armature is not None and context.mode == 'POSE' else None
        if bone is not None:
            bpy.ops.object.mode_set(mode='OBJECT')

        empty = bpy.data.objects.new(f'FX {os.path.basename(eff)}', None)
        empty.empty_display_type = 'ARROWS'
        empty.empty_display_size = 3.0
        context.collection.objects.link(empty)
        if bone is not None:
            con = empty.constraints.new('CHILD_OF')
            con.target = armature
            con.subtarget = bone.name
            con.set_inverse_pending = False
            con.inverse_matrix = Matrix.Identity(4)
            # Imported Smash bones are turned a quarter about their Z from the game's; this
            # puts the empty on the game's bone axes, so its offsets are the script's.
            empty.rotation_euler = (0.0, 0.0, math.radians(90))
        else:
            empty.location = context.scene.cursor.location

        fx = empty.smash_fx
        fx.enabled = True
        fx.start = context.scene.frame_current
        fx.eff = eff
        settings_list = settings.listed
        if fx.effect == '' and len(settings_list):
            fx.effect = settings_list[0].name

        for o in context.selected_objects:
            o.select_set(False)
        empty.select_set(True)
        context.view_layer.objects.active = empty
        return {'FINISHED'}


class SUB_OP_smash_fx_list(Operator):
    bl_idname = 'sub.smash_fx_list'
    bl_label = 'List Effects'
    bl_description = "Read the effect names out of the active effect's file"

    def execute(self, context):
        settings = context.scene.sub_smash_fx
        obj = _active_fx(context)
        if obj is None:
            return {'CANCELLED'}
        settings.listed_eff = ''
        refresh_list(settings, obj.smash_fx.eff)
        if settings.list_error:
            self.report({'ERROR'}, settings.list_error)
            return {'CANCELLED'}
        return {'FINISHED'}


class SUB_OP_smash_fx_fit_length(Operator):
    bl_idname = 'sub.smash_fx_fit_length'
    bl_label = 'Fit to Effect'
    bl_description = "End the effect when its last particle is gone (for a looping effect, after one loop)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = _active_fx(context)
        settings = context.scene.sub_smash_fx
        listed = settings.listed.get(obj.smash_fx.effect) if obj else None
        if listed is None:
            self.report({'ERROR'}, 'List the effects of its file first')
            return {'CANCELLED'}
        fx = obj.smash_fx
        step = forge._step(context.scene, fx)
        fx.use_end = True
        fx.end = fx.start + math.ceil(listed.length / step)
        return {'FINISHED'}


def _frames(scene):
    return list(range(scene.frame_start, scene.frame_end + 1))


def _shot_dir(out):
    return os.path.join(out, 'shot')


def _out_ok(op, context):
    settings = context.scene.sub_smash_fx
    if settings.out.startswith('//') and not context.blend_data.filepath:
        op.report({'ERROR'}, 'Save the .blend first (the output folder is next to it)')
        return None
    return forge.out_dir(settings)


class SUB_OP_smash_fx_bake(Operator):
    bl_idname = 'sub.smash_fx_bake'
    bl_label = 'Bake Effects'
    bl_description = ("Draw every effect over the scene's frame range with EffectForge, then lay the frames over "
                      "the camera view and the render")

    _process = None
    _timer = None
    _lines = None
    _frames = None
    _out = None
    _cameras = None
    _log = None
    _names = None

    def _start(self, context):
        out = _out_ok(self, context)
        if out is None:
            return False
        scene = context.scene
        settings = scene.sub_smash_fx
        exe = forge.forge_path(settings)
        if not os.path.isfile(exe):
            self.report({'ERROR'}, f'EffectForge is not at {exe} (Smash FX > Settings)')
            return False
        if not forge.effect_objects(scene):
            self.report({'ERROR'}, 'No effects to bake: add one with Add Effect')
            return False
        frames = _frames(scene)
        shots = None
        if settings.use_shot:
            shots = [os.path.join(_shot_dir(out), f'shot_{f:04}.exr') for f in frames]
            shots = [p if os.path.isfile(p) else None for p in shots]
            if not any(shots):
                self.report({'ERROR'}, 'No shot to draw against: Render Shot Depth first, or turn Against the Shot off')
                return False
        os.makedirs(out, exist_ok=True)
        cameras = set()
        keep = scene.frame_current
        for f in frames:
            scene.frame_set(f)
            if scene.camera:
                cameras.add(scene.camera)
        scene.frame_set(keep)
        try:
            data = forge.build_scene(context, frames, out, shots, preview=settings.show_in_camera,
                                     lights=settings.lights, cards=settings.cards and not settings.geometry,
                                     geometry=settings.geometry)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return False
        scene_file = os.path.join(out, 'scene.json')
        with open(scene_file, 'w') as f:
            json.dump(data, f)

        self._frames, self._out, self._cameras = frames, out, cameras
        self._names = [e['object'] for e in data['effects']]
        # Lights and cards from an earlier bake would be stale.
        for stale in (os.path.join(out, 'lights.json'), os.path.join(out, 'geo', 'layers.json')):
            if os.path.isfile(stale):
                os.remove(stale)
        for folder, prefixes in (('cards', ('card_',)), ('geo', ('geo_', 'atlas_'))):
            old = os.path.join(out, folder)
            for name in os.listdir(old) if os.path.isdir(old) else ():
                if name.startswith(prefixes) and name.endswith(('.exr', '.bin')):
                    os.remove(os.path.join(old, name))
        self._log = []
        self._process = subprocess.Popen([exe, 'render-scene', scene_file], stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True)
        self._lines = queue.Queue()

        def read(stream, lines):
            for line in stream:
                lines.put(line.rstrip())
            lines.put(None)

        threading.Thread(target=read, args=(self._process.stdout, self._lines), daemon=True).start()
        return True

    def _drain(self, context):
        """Read what EffectForge has said; True once it is done."""
        done = False
        while True:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                break
            if line is None:
                done = True
                break
            self._log.append(line)
            if line.startswith('frame ') and '/' in line and context.workspace:
                context.workspace.status_text_set(f'Baking Smash effects: {line}')
        return done

    def _finish(self, context):
        code = self._process.wait()
        if context.workspace:
            context.workspace.status_text_set(None)
        if code != 0:
            tail = [l for l in self._log if l][-3:]
            self.report({'ERROR'}, 'EffectForge failed: ' + ' | '.join(tail))
            return {'CANCELLED'}
        settings = context.scene.sub_smash_fx
        made = stood = shaped = 0
        if settings.lights:
            made = lights.apply(context.scene, self._out, self._names, geometry=settings.geometry)
        else:
            lights.clear(context.scene)
        if settings.geometry:
            # The meshes are the effects in the shot: nothing laid over it, no cards beside them.
            shaped = geometry.apply(context.scene, self._out, self._names, light_by_emission=not settings.lights)
            cards.clear(context.scene)
            layering.remove(context.scene, self._cameras)
        else:
            geometry.clear(context.scene)
            if settings.cards:
                stood = cards.apply(context.scene, self._out, self._names)
            else:
                cards.clear(context.scene)
            if settings.composite:
                layering.composite(context.scene, self._out, self._frames)
            if settings.show_in_camera:
                layering.show_in_cameras(self._cameras, self._out, self._frames)
        done = self._log[-1] if self._log else 'Baked'
        if made:
            done += f', {made} effect light{"" if made == 1 else "s"}'
        if stood:
            done += f', {stood} card{"" if stood == 1 else "s"}'
        if shaped:
            done += f', {shaped} effect mesh{"" if shaped == 1 else "es"}'
        self.report({'INFO'}, done)
        return {'FINISHED'}

    def execute(self, context):
        # Run to the end in one go (background mode, scripts).
        if not self._start(context):
            return {'CANCELLED'}
        while not self._drain(context):
            time.sleep(0.05)
        return self._finish(context)

    def invoke(self, context, event):
        if not self._start(context):
            return {'CANCELLED'}
        self._timer = context.window_manager.event_timer_add(0.2, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self._process.kill()
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        if not self._drain(context):
            return {'RUNNING_MODAL'}
        context.window_manager.event_timer_remove(self._timer)
        return self._finish(context)


class SUB_OP_smash_fx_render_shot(Operator):
    bl_idname = 'sub.smash_fx_render_shot'
    bl_label = 'Render Shot Depth'
    bl_description = ("Render the frame range once, quickly, to a multilayer EXR with depth, so baked effects can "
                      "be hidden behind what is in front of them (Against the Shot). Render settings are put back after")

    fast: BoolProperty(name='Fast', default=True, description='One sample: enough for depth')

    def execute(self, context):
        out = _out_ok(self, context)
        if out is None:
            return {'CANCELLED'}
        scene = context.scene
        render = scene.render
        settings = render.image_settings
        keep = {
            'filepath': render.filepath,
            'file_format': settings.file_format,
            'color_depth': settings.color_depth,
            'use_compositing': render.use_compositing,
        }
        if hasattr(settings, 'media_type'):
            keep['media_type'] = settings.media_type
        passes = {vl.name: vl.use_pass_z for vl in scene.view_layers}
        samples = {}
        if self.fast:
            if hasattr(scene, 'eevee'):
                samples['eevee'] = scene.eevee.taa_render_samples
                scene.eevee.taa_render_samples = 1
            if hasattr(scene, 'cycles'):
                samples['cycles'] = scene.cycles.samples
                scene.cycles.samples = 1
        try:
            os.makedirs(_shot_dir(out), exist_ok=True)
            render.filepath = os.path.join(_shot_dir(out), 'shot_')
            if hasattr(settings, 'media_type'):
                settings.media_type = 'MULTI_LAYER_IMAGE'
            settings.file_format = 'OPEN_EXR_MULTILAYER'
            settings.color_depth = '32'
            render.use_compositing = False
            for vl in scene.view_layers:
                vl.use_pass_z = True
            bpy.ops.render.render(animation=True)
        finally:
            for vl in scene.view_layers:
                vl.use_pass_z = passes[vl.name]
            if 'eevee' in samples:
                scene.eevee.taa_render_samples = samples['eevee']
            if 'cycles' in samples:
                scene.cycles.samples = samples['cycles']
            if 'media_type' in keep:
                settings.media_type = keep['media_type']
            settings.file_format = keep['file_format']
            settings.color_depth = keep['color_depth']
            render.use_compositing = keep['use_compositing']
            render.filepath = keep['filepath']
        scene.sub_smash_fx.use_shot = True
        self.report({'INFO'}, f'Shot rendered to {_shot_dir(out)}')
        return {'FINISHED'}


classes = (
    SUB_OP_smash_fx_add,
    SUB_OP_smash_fx_list,
    SUB_OP_smash_fx_fit_length,
    SUB_OP_smash_fx_bake,
    SUB_OP_smash_fx_render_shot,
)


_LISTS = {}


def _effects_in(settings, eff):
    """The effect names of an .eff, upper case, listed once per session."""
    eff = bpy.path.abspath(eff)
    if eff not in _LISTS:
        try:
            _LISTS[eff] = {e['name'].upper(): e for e in forge.list_effects(forge.forge_path(settings), eff)}
        except Exception as e:
            print(f'Smash FX: could not list {eff}: {e}')
            _LISTS[eff] = {}
    return _LISTS[eff]


def _effect_files(settings):
    """Where a script's effects are looked for, in order: the fighter's own file, then common and item."""
    files = []
    if settings.source == 'CUSTOM':
        if settings.custom_eff:
            files.append(settings.custom_eff)
    elif settings.source not in ('common', 'item'):
        files.append(forge.vanilla_eff(settings.game_root, settings.source))
    files += [forge.vanilla_eff(settings.game_root, 'common'), forge.vanilla_eff(settings.game_root, 'item')]
    # Stored as given (// paths stay relative to the blend), so the shot can move with its files.
    return [f for f in files if os.path.isfile(bpy.path.abspath(f))]


def _pose_bone(armature, name):
    want = 'trans' if name.lower() == 'top' else name.lower()
    return next((b for b in armature.pose.bones if b.name.lower() == want), None)


def _local(effect):
    """Where the script puts the effect, in the bone's Blender axes (the game's turned a quarter about Z)."""
    rotation = Euler([math.radians(a) for a in effect['rotation']], 'XYZ').to_matrix().to_4x4()
    return (Matrix.Rotation(math.radians(90), 4, 'Z') @ Matrix.Translation(effect['offset'])
            @ rotation @ Matrix.Scale(effect['size'], 4))


class SUB_OP_smash_fx_import_acmd(Operator):
    bl_idname = 'sub.smash_fx_import_acmd'
    bl_label = 'Import Move Effects'
    bl_description = ("Place the effects of a move's effect script (ACMD) on the active armature: each effect on its "
                      "bone and frame, following it as the script says, with its rate, colour and stop")
    bl_options = {'REGISTER', 'UNDO'}

    start: IntProperty(name='Motion Start', default=1, description='The frame the move\'s animation starts on')
    cut: IntProperty(name='Cut At', default=0, min=0,
                     description="The motion frame the move is cut at (the next move starts); 0 plays it all. "
                                 "Effects the script makes after it are left out")

    def execute(self, context):
        settings = context.scene.sub_smash_fx
        armature = context.object
        if armature is None or armature.type != 'ARMATURE':
            self.report({'ERROR'}, 'Select the armature the move plays on')
            return {'CANCELLED'}
        path = bpy.path.abspath(settings.acmd_file)
        try:
            text = open(path, encoding='utf-8', errors='replace').read()
        except OSError as e:
            self.report({'ERROR'}, f'Cannot read the script: {e}')
            return {'CANCELLED'}
        body = acmd.function_body(text, settings.acmd_script)
        if body is None:
            self.report({'ERROR'}, f'{settings.acmd_script} is not in {os.path.basename(path)}')
            return {'CANCELLED'}
        files = _effect_files(settings)
        effects = [e for e in acmd.read(body) if not self.cut or e['frame'] < self.cut]

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        scene = context.scene
        keep = scene.frame_current
        collection = bpy.data.collections.new(f'FX {settings.acmd_script}')
        scene.collection.children.link(collection)
        missing, made = [], 0
        for e in effects:
            name = e['name'].upper()
            eff = next((f for f in files if name in _effects_in(settings, f)), None)
            bone = _pose_bone(armature, e['bone'])
            if eff is None or bone is None:
                missing.append(name if eff is None else f'{name} (bone {e["bone"]})')
                continue
            frame = self.start + int(round(e['frame']))
            empty = bpy.data.objects.new(f'{settings.acmd_script} {int(e["frame"])} {name}', None)
            empty.empty_display_type = 'ARROWS'
            empty.empty_display_size = 2.0
            collection.objects.link(empty)
            local = _local(e)
            if e['follow']:
                con = empty.constraints.new('CHILD_OF')
                con.target = armature
                con.subtarget = bone.name
                con.inverse_matrix = Matrix.Identity(4)
                con.set_inverse_pending = False
                if e['follow'] == 'pos':
                    con.use_rotation_x = con.use_rotation_y = con.use_rotation_z = False
                    scene.frame_set(frame)
                    turn = (armature.matrix_world @ bone.matrix).to_quaternion().to_matrix().to_4x4()
                    local = turn @ local
                empty.matrix_basis = local
            else:
                scene.frame_set(frame)
                world = armature.matrix_world @ bone.matrix @ local
                if e['grounded']:
                    ground = _pose_bone(armature, 'top')
                    world.translation.z = (armature.matrix_world @ ground.matrix).translation.z
                empty.matrix_world = world
            fx = empty.smash_fx
            fx.enabled = True
            fx.eff = eff
            fx.effect = name
            fx.start = frame
            fx.rate = e['rate']
            fx.colour = e['colour']
            if e['stop'] is not None:
                fx.use_stop = True
                fx.stop = self.start + int(round(e['stop']))
            made += 1
        scene.frame_set(keep)
        fps = scene.render.fps / scene.render.fps_base
        if abs(fps - 60) > 0.01:
            self.report({'WARNING'}, f'The scene runs at {fps:g} fps; Smash animations and effects are 60')
        if missing:
            self.report({'WARNING'}, f'{made} effects placed; not found: {", ".join(missing)}')
        else:
            self.report({'INFO'}, f'{made} effects placed')
        return {'FINISHED'}


classes += (SUB_OP_smash_fx_import_acmd,)
