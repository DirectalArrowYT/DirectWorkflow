"""The TextureCraft round trip on a real fighter, headless (needs texturecraft-cli).

blender -b --python tests_texturecraft.py -- <model folder with a .numdlb> <empty work folder>

The model folder is copied into the work folder, because importing writes PNGs next to it.
"""
import bpy, sys, os, json, shutil, subprocess, importlib, addon_utils
import numpy as np

MODEL, WORK = sys.argv[sys.argv.index("--") + 1:][:2]

fails = []
def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)

NAME = "smash-ultimate-blender-animation-workflow"
addon_utils.enable(NAME, default_set=False)
tc = importlib.import_module(NAME + ".source.bake_texs.texturecraft")
core = importlib.import_module(NAME + ".source.bake_texs.core")
tc.launch = lambda port: None   # no window in a headless run

model_dir = os.path.join(WORK, "model")
shutil.copytree(MODEL, model_dir)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(WORK, "fighter.blend"))

scene = bpy.context.scene
ssp = scene.sub_scene_properties
ssp.model_import_folder_path = model_dir
for attr, ext in (("numdlb", ".numdlb"), ("numshb", ".numshb"), ("nusktb", ".nusktb"),
                  ("numatb", ".numatb"), ("nuhlpb", ".nuhlpb")):
    setattr(ssp, f"model_import_{attr}_file_name", "model" + ext)
bpy.ops.sub.model_importer(model_path=model_dir)

meshes = [o for o in bpy.data.objects if o.type == 'MESH' and o.material_slots]
check(bool(meshes), f"imported {len(meshes)} meshes")
for obj in bpy.data.objects:
    obj.select_set(False)

# Pick what to paint: the texture with the most meshes on it, and leave one of those meshes
# unselected when there are two, which is the hidden expression mesh case.
by_col = {}
for obj in meshes:
    for slot in obj.material_slots:
        col = tc.slot_image(slot.material, tc.SLOT_COL)
        if col is not None:
            by_col.setdefault(col.name, [])
            if obj not in by_col[col.name]:
                by_col[col.name].append(obj)
col_name, users = max(by_col.items(), key=lambda kv: len(kv[1]))
sent = users[:-1] if len(users) > 1 else users
left_out = users[-1] if len(users) > 1 else None
for obj in sent:
    obj.hide_set(False)
    obj.select_set(True)
print(f"  painting {col_name}: {len(sent)} sent, left out {left_out and left_out.name}")
for name, objs in by_col.items():
    if name != col_name and objs[0] not in users:
        objs[0].hide_set(False)
        objs[0].select_set(True)
        sent.append(objs[0])
        break

props = scene.sub_bake_texs_properties
props.char_tag = "test"
core.apply_settings(props)
materials = [s.material for o in sent for s in o.material_slots]
base = next(m for m in materials if tc.slot_image(m, tc.SLOT_COL)
            and tc.slot_image(m, tc.SLOT_COL).name == col_name)
before = {p: tc.read_image(tc.slot_image(base, p))
          for p in (tc.SLOT_COL, tc.SLOT_PRM, tc.SLOT_NOR)}

print("== send")
sets = tc.collect_sets()
print("  sets:", [(s["name"], len(s["groups"])) for s in sets])
check(len(sets) >= 2, "several texture sets")
sent_names = {o.name for s in sets for o in s["objects"]}
print("  meshes:", sorted(sent_names))
if any(tc.is_open_blink(bpy.data.objects[n]) for n in sent_names):
    check(not [n for n in sent_names if "blink" in n.lower()
               and not tc.is_open_blink(bpy.data.objects[n])], "only Openblink of the blinks")
check(sum(1 for s in sets if tc.slot_image(s["groups"][0][0], tc.SLOT_COL)
          and tc.slot_image(s["groups"][0][0], tc.SLOT_COL).name == col_name) == 1,
      "one set per shared texture")
objects_before = len(bpy.data.objects)
materials_before = len(bpy.data.materials)
check(bpy.ops.sub.texturecraft_send('EXEC_DEFAULT') == {'FINISHED'}, "send ran")
check(tc.has_project(), "project written")
check(len(bpy.data.objects) == objects_before and len(bpy.data.materials) == materials_before,
      "no temporary objects or materials left behind")

info = json.loads(subprocess.run(
    [tc.find_program("texturecraft-cli"), "info", tc.project_path()],
    capture_output=True, text=True).stdout)
set_names = {s["name"] for s in sets}
project_sets = [s["name"] for s in info["textureSets"]]
print("  project sets:", project_sets)
check(set(project_sets) == set_names, "the project's sets are the manifest's sets")

print("== get, nothing painted: the textures come back as they went")
check(bpy.ops.sub.texturecraft_get('EXEC_DEFAULT') == {'FINISHED'}, "get ran")
entry = next(s for s in tc.sets_from_manifest()[0] if s["groups"][0][0] == base
             or base in [m for g in s["groups"] for m in g])
folder = os.path.join(tc.project_dir(), "export")
covered = tc.read_png(os.path.join(folder, entry["name"] + "_cov.png"))[..., 0] > 0.5
share = covered.mean()
print(f"  mesh covers {share:.1%} of the texture")
check(0.01 < share < 1.0, "coverage is a real mask")

def after(param):
    return tc.read_image(tc.slot_image(base, param))

def close(a, b, mask=None, tol=2.5 / 255):
    a, b = tc.resized(a, covered.shape[1], covered.shape[0]), \
        tc.resized(b, covered.shape[1], covered.shape[0])
    d = np.abs(a - b).max(axis=2)
    return (d[mask] if mask is not None else d)

# The export's padding replaces texels beside the islands, so exactness is checked on the islands
# alone: the same export with no padding covers only what the UVs reach.
bare = os.path.join(WORK, "bare")
tc.run_batch({"open": tc.project_path(), "steps": [{"id": "file.exportTextures", "params": {
    "dir": bare, "preset": tc.SMASH_PRESET, "ts": "all", "padding": "none"}}]})
interior = tc.read_png(os.path.join(bare, entry["name"] + "_cov.png"))[..., 0] > 0.5
print(f"  islands alone cover {interior.mean():.1%}")
check(0.01 < interior.mean() < share, "the bare export has no padding")

col2, prm2, nor2 = after(tc.SLOT_COL), after(tc.SLOT_PRM), after(tc.SLOT_NOR)
check(tc.slot_image(base, tc.SLOT_COL).name == entry["stem"] + "_col", "slot took the new image")
for label, a, b in (("COL", before[tc.SLOT_COL], col2), ("PRM", before[tc.SLOT_PRM], prm2)):
    if a is None:
        continue
    d = close(a, b)
    print(f"  {label}: {(d > 3 / 255).mean():.3%} of texels moved, worst {d.max() * 255:.0f}/255")
    check((d > 3 / 255).mean() < 0.02, f"{label} survived the round trip")
    d = close(a, b, interior)
    print(f"  {label} on the islands: {(d > 1.5 / 255).mean():.4%} off, "
          f"worst {d.max() * 255:.0f}/255")
    check((d > 1.5 / 255).mean() < 0.001, f"{label} is exact on the islands")
if before[tc.SLOT_NOR] is not None:
    # A NOR smaller than its set gets resampled by both sides, each their own way.
    print("  NOR size", before[tc.SLOT_NOR].shape[:2], "set size", covered.shape)
    for i, label in ((2, "b"), (3, "a")):
        one = np.abs(tc.resized(before[tc.SLOT_NOR], *covered.shape[::-1])[..., i] - nor2[..., i])
        off = (one[interior] > 8 / 255).mean()
        print(f"  NOR.{label}: worst {one[interior].max() * 255:.0f}/255, {off:.3%} off")
        check(off < 0.03, f"NOR.{label} survived the round trip")
    d = close(before[tc.SLOT_NOR][..., :2].repeat(2, axis=2),
              nor2[..., :2].repeat(2, axis=2), covered)
    print(f"  NOR rg: {(d > 4 / 255).mean():.3%} of covered texels moved")
    check((d > 4 / 255).mean() < 0.05, "NOR RG survived")

print("== paint, then get with the app closed")
steps = [{"id": "textureSet.select", "params": {"ts": project_sets.index(entry["name"])}},
         {"id": "layer.add.fill", "params": {"name": "paint", "color": [1.0, 0.0, 0.0],
                                             "roughness": 0.25, "metallic": 1.0}}]
skin = tc.is_skin(entry)
print("  skin shader:", skin)
for channel, value in ((tc.SSS_CHANNEL, 1.0), (tc.CAVITY_CHANNEL, [0.0, 0.0, 0.0]),
                       (tc.TRANSITION_CHANNEL, [1.0, 1.0, 1.0]), ("sheenRoughness", 0.5),
                       ("coatRoughness", 0.25), ("anisotropyLevel", 1.0),
                       ("anisotropyAngle", 0.0)):
    steps.append({"id": "layer.fill.set", "params": {"channel": channel, "value": value}})
tc.run_batch({"open": tc.project_path(), "steps": steps, "save": tc.project_path()})
check(bpy.ops.sub.texturecraft_get('EXEC_DEFAULT') == {'FINISHED'}, "get ran")
col3, prm3 = after(tc.SLOT_COL), after(tc.SLOT_PRM)
check(col3[covered][:, 0].min() > 0.98 and col3[covered][:, 1].max() < 0.02,
      "paint reached COL on every covered texel")
check(abs(np.median(prm3[covered][:, 1]) - 0.25) < 0.01
      and prm3[covered][:, 0].min() > 0.98, "paint reached PRM rough and metal")
outside = ~covered
d = np.abs(col3[outside] - tc.resized(col2, *covered.shape[::-1])[outside]).max()
check(d < 1.5 / 255, "texels the mesh doesn't reach kept their old pixels exactly")
nor3 = after(tc.SLOT_NOR)
check(nor3[covered][:, 3].max() < 0.02, "cavity paint reached NOR.a")
check(nor3[covered][:, 2].min() > 0.98, "transition paint reached NOR.b")
toon = tc.read_png(os.path.join(core.BAKE_DIR, entry["stem"] + "_toon.png"))
m = np.median(toon[covered], axis=0)
print("  toon map median:", m)
check(abs(m[0] - 0.5) < 0.01 and abs(m[1] - 0.25) < 0.01 and m[2] > 0.98 and m[3] < 0.02,
      "the toon masks came back in their channels, alpha included")

print("== shader wiring")
mat = bpy.data.materials.new("plain")
mat.use_nodes = True
principled = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
notes = []
image = tc.slot_image(base, tc.SLOT_COL)
tc.show_on_shader(mat, {"_col": image, "_prm": tc.slot_image(base, tc.SLOT_PRM),
                        "_nor": tc.slot_image(base, tc.SLOT_NOR)}, notes)
check(principled.inputs["Base Color"].is_linked, "Principled Base Color wired")
check(principled.inputs["Metallic"].links[0].from_socket.name == "Red", "Metallic is PRM.r")
check(principled.inputs["Roughness"].links[0].from_socket.name == "Green", "Roughness is PRM.g")
check(principled.inputs["Normal"].links[0].from_node.type == 'NORMAL_MAP', "Normal wired")
check(tc.upstream_image_nodes(principled.inputs["Base Color"])[0].image == image, "COL image")
twice = len(mat.node_tree.nodes)
tc.show_on_shader(mat, {"_col": image, "_prm": tc.slot_image(base, tc.SLOT_PRM),
                        "_nor": tc.slot_image(base, tc.SLOT_NOR)}, notes)
check(len(mat.node_tree.nodes) == twice, "running it again adds nothing")

print("== hb master shader")
ms = importlib.import_module(NAME + ".source.bake_texs.master_shader")
ms.build_master_group()
hb_mat = bpy.data.materials.new("hb")
hb_mat.use_nodes = True
nt = hb_mat.node_tree
nt.nodes.clear()
hb = nt.nodes.new("ShaderNodeGroup")
hb.node_tree = bpy.data.node_groups[ms.MASTER_NAME]
out = nt.nodes.new("ShaderNodeOutputMaterial")
nt.links.new(hb.outputs[0], out.inputs["Surface"])
images = {"_col": image, "_prm": tc.slot_image(base, tc.SLOT_PRM),
          "_nor": tc.slot_image(base, tc.SLOT_NOR)}
toon_image = bpy.data.images.load(os.path.join(core.BAKE_DIR, entry["stem"] + "_toon.png"))
images["_toon"] = toon_image
tc.show_on_shader(hb_mat, images, notes, {"Cavity": 1.0, "Rim Mask": None, "Hair Ring": 0.0})
check(not hb.inputs["Cavity"].is_linked, "an unpainted mask keeps its slider")
check(not hb.inputs["Hair Ring"].is_linked, "Hair Ring at its own value stays a slider")
check(hb.inputs["Rim Mask"].links[0].from_socket.name == "Red", "Rim Mask is toon red")
check(hb.inputs["Shadow Mask"].links[0].from_socket.name == "Alpha", "Shadow Mask is toon alpha")
check(hb.inputs["Transition Blend"].links[0].from_socket.name == "Blue", "Transition is NOR.b")
check(hb.inputs["PRM AO"].links[0].from_socket.name == "Blue", "PRM AO is PRM.b")

print("== a shader update gives existing nodes the new sockets' defaults")
old = bpy.data.materials.new("old_hb")
old.use_nodes = True
old_group = ms.build_master_group()
old_node = old.node_tree.nodes.new("ShaderNodeGroup")
old_node.node_tree = old_group
old_node.inputs["Specular"].default_value = 0.33
for name in ("Cavity", "PRM AO", "Rim Mask"):
    old_group.interface.remove(next(i for i in old_group.interface.items_tree
                                    if i.item_type == 'SOCKET' and i.name == name))
ms.build_master_group()
for name in ("Cavity", "PRM AO", "Rim Mask"):
    check(abs(old_node.inputs[name].default_value - 1.0) < 1e-6, f"{name} comes in at 1, not 0")
check(abs(old_node.inputs["Specular"].default_value - 0.33) < 1e-6, "Update keeps existing values")
bpy.data.materials.remove(old)

print("== update mesh keeps the paint")
op = bpy.ops.sub.texturecraft_send('EXEC_DEFAULT', mode='UPDATE')
check(op == {'FINISHED'}, "update ran")
check(bpy.ops.sub.texturecraft_get('EXEC_DEFAULT') == {'FINISHED'}, "get ran")
col4 = after(tc.SLOT_COL)
check(col4[covered][:, 0].min() > 0.98, "paint survived Update Mesh")

print("== a project from before the mask channels")
steps = []
for index in range(len(project_sets)):
    steps.append({"id": "textureSet.select", "params": {"ts": index}})
    steps += [{"id": "textureSet.channel.remove", "params": {"channel": c}}
              for c in tc.MASK_CHANNELS]
tc.run_batch({"open": tc.project_path(), "steps": steps, "save": tc.project_path()})
check(bpy.ops.sub.texturecraft_get('EXEC_DEFAULT') == {'FINISHED'}, "get ran without them")
check(not os.path.isfile(os.path.join(tc.project_dir(), "export", entry["name"] + "_gam.png")),
      "no gam map without the channels")
check(np.abs(after(tc.SLOT_NOR)[..., 3][covered] - nor3[..., 3][covered]).max() < 2 / 255,
      "NOR.a kept the texture's cavity")
op = bpy.ops.sub.texturecraft_send('EXEC_DEFAULT', mode='UPDATE')
check(op == {'FINISHED'}, "update added them")
check(bpy.ops.sub.texturecraft_get('EXEC_DEFAULT') == {'FINISHED'}, "get ran")
check(os.path.isfile(os.path.join(tc.project_dir(), "export", entry["name"] + "_gam.png")),
      "the masks export again")
nor5 = after(tc.SLOT_NOR)
check(np.abs(nor5[..., 3][interior] - nor3[..., 3][interior]).max() < 2 / 255,
      "the new layer was seeded from the texture")

print("== start over keeps a backup")
op = bpy.ops.sub.texturecraft_send('EXEC_DEFAULT', mode='NEW')
check(op == {'FINISHED'} and os.path.isfile(tc.project_path() + ".bak"), "old project backed up")

bpy.ops.wm.save_mainfile()

print(f"\nFAILS: {len(fails)}")
for f in fails:
    print("  -", f)
sys.stdout.flush()
os._exit(1 if fails else 0)
