"""Run with Blender --background --factory-startup --python this_file.

Optional arguments after --: model folder, material animation, native DLL.
"""
import ctypes as c
import importlib.util
from pathlib import Path
import sys
import time
import statistics
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("viewport_test", ROOT / "source/extras/smash_viewport.py")
vp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vp)

arm = bpy.data.objects.new("Test rig", bpy.data.armatures.new("Test rig"))
mesh = bpy.data.objects.new("Test mesh", bpy.data.meshes.new("Test mesh"))
bpy.context.collection.objects.link(arm)
bpy.context.collection.objects.link(mesh)
mesh.parent = arm
mesh.modifiers.new("Armature", "ARMATURE").object = arm
bpy.context.view_layer.update()
arm.hide_set(True)
assert vp._gpu_model_visible(bpy.context.scene, arm, bpy.context.view_layer, None)
assert not vp._gpu_mesh_hidden(mesh, arm, None, bpy.context.view_layer, None)
mesh.hide_set(True)
assert not vp._gpu_model_visible(bpy.context.scene, arm, bpy.context.view_layer, None)
mesh.hide_set(False)
collection = bpy.data.collections.new("Hidden collection")
bpy.context.scene.collection.children.link(collection)
bpy.context.collection.objects.unlink(mesh)
collection.objects.link(mesh)
collection.hide_viewport = True
bpy.context.view_layer.update()
assert vp._gpu_mesh_hidden(mesh, arm, None, bpy.context.view_layer, None)
collection.hide_viewport = False

uploads = []
def upload(handle, indices, names, subs, matrices, count):
    uploads.append(list(matrices))
    return 0
vp._lib = SimpleNamespace(ssbh_preview_set_mesh_transforms=upload)
vp._primary_smash_armature = lambda scene: arm
arm.location = (10, 20, 30)
mesh.location = (3, 0, 0)
bpy.context.view_layer.update()
assert vp._sync_mesh_transforms(1, bpy.context, bpy.context.evaluated_depsgraph_get())
assert np.allclose(np.array(uploads[-1]).reshape(4, 4).T[:3, 3], (3, 0, 0), atol=1e-5), (uploads[-1], str(arm.matrix_world), str(mesh.matrix_world))
assert not vp._sync_mesh_transforms(1, bpy.context, bpy.context.evaluated_depsgraph_get())
mesh.location.x = 0
bpy.context.view_layer.update()
assert vp._sync_mesh_transforms(1, bpy.context, bpy.context.evaluated_depsgraph_get())
assert np.allclose(np.array(uploads[-1]).reshape(4, 4), np.eye(4), atol=1e-5)
other = bpy.data.objects.new("Other mesh", bpy.data.meshes.new("Other mesh"))
bpy.context.collection.objects.link(other)
other.parent = arm
other.modifiers.new("Armature", "ARMATURE").object = arm
bpy.context.view_layer.update()
assert vp._sync_mesh_transforms(1, bpy.context, bpy.context.evaluated_depsgraph_get())
assert len(uploads[-1]) == 16, "Unchanged mesh was reuploaded"
mesh.location.x = 2
bpy.context.view_layer.update()
assert vp._sync_mesh_transforms(1, bpy.context, bpy.context.evaluated_depsgraph_get())
assert len(uploads[-1]) == 16, "Moving one mesh reuploaded unrelated meshes"
print("PASS: hidden skeleton, hidden mesh/collection, moved mesh, reset transform, cache")

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if args:
    model, anim, dll = args
    lib = vp._bind_lib(c.CDLL(str(Path(dll).resolve())))
    handle = lib.ssbh_preview_create()
    assert handle, lib.ssbh_preview_last_error()
    def ok(result):
        assert result == 0, lib.ssbh_preview_last_error()
    try:
        ok(lib.ssbh_preview_load_folder(handle, model.encode()))
        ok(lib.ssbh_preview_resize(handle, 512, 512, 1))
        ok(lib.ssbh_preview_set_clear_color(handle, 0, 0, 0, 0))
        eye = Vector((0, 10, 35))
        target = Vector((0, 10, 0))
        world = (target-eye).to_track_quat('-Z', 'Y').to_matrix().to_4x4()
        world.translation = eye
        view = world.inverted()
        proj = vp._GL_TO_WGPU_CLIP @ vp._perspective_rh(0.7, 1, 0.1, 1000)
        arr = lambda m: (c.c_float * 16)(*vp._mat4_col_major(m))
        ok(lib.ssbh_preview_set_camera(handle, arr(view), arr(proj), (c.c_float*4)(*eye, 1), 512, 512, 1))
        # Isolate the opaque hair draw call from the other expression meshes.
        # Read identities using the add-on's bundled SSBH Python package.
        sys.path.insert(0, str(ROOT))
        from dependencies import ssbh_data_py as ssbh
        data = ssbh.mesh_data.read_mesh(str(Path(model) / 'model.numshb'))
        names = (c.c_char_p * len(data.objects))(*(o.name.encode() for o in data.objects))
        subs = (c.c_uint * len(data.objects))(*(o.subindex for o in data.objects))
        visible = (c.c_ubyte * len(data.objects))(*(int(o.name == 'Vegito_Hair_VIS_O_OBJShape') for o in data.objects))
        ok(lib.ssbh_preview_set_mesh_visibility(handle, names, subs, visible, len(data.objects)))
        def render():
            pixels = (c.c_ubyte * (512*512*4))()
            w, h = c.c_uint(), c.c_uint()
            ok(lib.ssbh_preview_render_wait(handle, pixels, len(pixels), c.byref(w), c.byref(h)))
            return np.array(pixels).reshape(512, 512, 4)
        before = render()
        ok(lib.ssbh_preview_apply_material_anim(handle, anim.encode(), 0))
        after = render()
        coverage = after[:,:,3] > 200
        assert coverage.sum() > 100, f"Hair coverage lost: {coverage.sum()}"
        assert coverage.sum() < 512 * 512 // 2, "Transparent background lost"
        rgb = after[:,:,:3][coverage].mean(axis=0)
        assert rgb[0] > rgb[2] * 1.3 and rgb[1] > rgb[2] * 1.3, rgb
        assert np.abs(after.astype(float)-before).sum() > 1000
        center = np.argwhere(coverage).mean(axis=0)
        ok(lib.ssbh_preview_set_mesh_transforms(handle, (c.c_uint*1)(0), (c.c_char_p*1)(b'Vegito_Hair_VIS_O_OBJShape'), (c.c_uint*1)(0), arr(Matrix.Translation((3,0,0))), 1))
        moved = render()
        moved_center = np.argwhere(moved[:,:,3] > 200).mean(axis=0)
        assert moved_center[1] > center[1] + 10, (center, moved_center)
        ok(lib.ssbh_preview_set_mesh_transforms(handle, (c.c_uint*1)(0), (c.c_char_p*1)(b'Vegito_Hair_VIS_O_OBJShape'), (c.c_uint*1)(0), arr(Matrix.Identity(4)), 1))
        reset = render()
        assert np.abs(reset.astype(float)-after).mean() < 1
        ok(lib.ssbh_preview_apply_material_anim(handle, None, 0))
        cleared = render()
        assert np.abs(cleared.astype(float)-before).mean() < 1
        ok(lib.ssbh_preview_apply_material_anim(handle, anim.encode(), 0))
        restored = render()
        assert np.abs(restored.astype(float)-after).mean() < 1
        timings = []
        for _ in range(5):
            start = time.perf_counter()
            for frame in range(120):
                ok(lib.ssbh_preview_apply_material_anim(handle, anim.encode(), frame))
            timings.append((time.perf_counter() - start) * 1000 / 120)
            render()  # Drain pending GPU writes between samples.
        print(f"Material update benchmark: {statistics.median(timings):.4f} ms/call (5 x 120 frames)")
        print(f"PASS: native animated yellow hair ({coverage.sum()} covered pixels, RGB {rgb}), translation and reset")
    finally:
        lib.ssbh_preview_destroy(handle)
