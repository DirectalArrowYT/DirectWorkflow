"""Scale correctness, on copies of the real Shigaraki files."""
import sys, os, shutil, importlib

OUT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "scale_test.txt"),
           "w", encoding="utf-8")
def w(*a):
    print(*a, file=OUT)
    OUT.flush()
    print(*a)

addon = next(m for n, m in sys.modules.items()
             if n.endswith("smash-ultimate-blender-animation-workflow"))
FS = importlib.import_module(addon.__name__ + ".source.extras.fighter_scale")
ssbh = importlib.import_module(addon.__name__ + ".dependencies.ssbh_data_py")
pyprc = importlib.import_module(addon.__name__ + ".dependencies.pyprc")

MOD = r"Z:/Documents/Hype Bros Studios/Shigaraki/(Moveset) Shigaraki/fighter/eflame"
TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scaletmp")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)

def cp(src, name):
    d = os.path.join(TMP, name)
    shutil.copy(src, d)
    return d

fails = []
def check(cond, label):
    if not cond:
        fails.append(label)

try:
    F = 0.8
    def close(a, b, tol=1e-4):
        return abs(a - b) <= tol * max(1.0, abs(b))

    # ------------------------------------------------------------------ ANIM
    src = MOD + "/motion/body/c80/a02dash.nuanmb"
    before = ssbh.anim_data.read_anim(src)

    def tg(a):
        return [g for g in a.groups if g.group_type.name == 'Transform'][0]

    def node(a, n):
        return [x for x in tg(a).nodes if x.name == n][0]

    b_trans = [list(v.translation) for v in node(before, 'Trans').tracks[0].values]
    b_rot = [list(v.rotation) for v in node(before, 'Trans').tracks[0].values]
    b_hip = [list(v.translation) for v in node(before, 'Hip').tracks[0].values]
    b_scale = [list(v.scale) for v in node(before, 'Hip').tracks[0].values]

    def material_snapshot(a):
        # Snapshot every Material and Visibility value, so the test proves the
        # scale left all of them alone rather than just one lucky track.
        out = []
        for g in a.groups:
            if g.group_type.name in ('Material', 'Visibility'):
                for nd in g.nodes:
                    for t in nd.tracks:
                        for v in t.values:
                            out.append((nd.name, t.name,
                                        tuple(v) if hasattr(v, '__iter__') else v))
        return out

    mat_before = material_snapshot(before)

    p = cp(src, "a.nuanmb")
    nodes, keys = FS.scale_anim_file(p, F, scale_root=True, root_bones=('Trans',))
    after = ssbh.anim_data.read_anim(p)
    a_trans = [list(v.translation) for v in node(after, 'Trans').tracks[0].values]
    a_rot = [list(v.rotation) for v in node(after, 'Trans').tracks[0].values]
    a_hip = [list(v.translation) for v in node(after, 'Hip').tracks[0].values]
    a_scale = [list(v.scale) for v in node(after, 'Hip').tracks[0].values]

    w("anim: nodes=%d keys=%d" % (nodes, keys))
    w("  Trans last  %s  ->  %s" % (b_trans[-1], a_trans[-1]))
    check(all(close(a, b * F) for A, B in zip(a_trans, b_trans) for a, b in zip(A, B)),
          "Trans translation not scaled by 0.8")
    check(all(close(a, b * F) for A, B in zip(a_hip, b_hip) for a, b in zip(A, B)),
          "Hip translation not scaled by 0.8")
    check(all(close(a, b, 1e-5) for A, B in zip(a_rot, b_rot) for a, b in zip(A, B)),
          "rotation changed (must not)")
    check(mat_before == material_snapshot(after),
          "Material/Visibility tracks changed (must not)")
    check(b_scale == a_scale, "Transform.scale changed (must not)")

    # root-motion opt-out
    p2 = cp(src, "b.nuanmb")
    FS.scale_anim_file(p2, F, scale_root=False, root_bones=('Trans',))
    after2 = ssbh.anim_data.read_anim(p2)
    a2_trans = [list(v.translation) for v in node(after2, 'Trans').tracks[0].values]
    a2_hip = [list(v.translation) for v in node(after2, 'Hip').tracks[0].values]
    check(all(close(a, b, 1e-6) for A, B in zip(a2_trans, b_trans) for a, b in zip(A, B)),
          "scale_root=False still scaled Trans")
    check(all(close(a, b * F) for A, B in zip(a2_hip, b_hip) for a, b in zip(A, B)),
          "scale_root=False failed to scale Hip")
    w("  opt-out: Trans last %s (unchanged), Hip still scaled" % (a2_trans[-1],))

    # identity round trip must not drift
    p3 = cp(src, "c.nuanmb")
    FS.scale_anim_file(p3, 1.0)
    after3 = ssbh.anim_data.read_anim(p3)
    a3 = [list(v.translation) for v in node(after3, 'Trans').tracks[0].values]
    check(all(close(a, b, 1e-6) for A, B in zip(a3, b_trans) for a, b in zip(A, B)),
          "factor 1.0 round trip drifted")
    w("  identity round-trip bytes %d -> %d" % (os.path.getsize(src), os.path.getsize(p3)))

    # ------------------------------------------------------------------ SKEL
    src = MOD + "/model/body/c80/model.nusktb"
    bs = ssbh.skel_data.read_skel(src)
    b_map = {b.name: [list(r) for r in b.transform] for b in bs.bones}
    p = cp(src, "model.nusktb")
    n = FS.scale_skel_file(p, F)
    as_ = ssbh.skel_data.read_skel(p)
    a_map = {b.name: [list(r) for r in b.transform] for b in as_.bones}
    w("\nskel: %d bones with non-zero translation, %d total" % (n, len(a_map)))
    bad_t = [k for k in b_map
             if not all(close(a_map[k][3][i], b_map[k][3][i] * F, 1e-5) for i in range(3))]
    bad_r = [k for k in b_map
             if any(not close(a_map[k][r][c], b_map[k][r][c], 1e-6)
                    for r in range(3) for c in range(4))]
    check(not bad_t, "skel translation wrong on %s" % (bad_t[:3],))
    check(not bad_r, "skel rotation/scale rows changed on %s" % (bad_r[:3],))
    w("  Head trans %s -> %s" % (b_map['Head'][3][:3], a_map['Head'][3][:3]))

    # ------------------------------------------------------------------ MESH
    src = MOD + "/model/body/c80/model.numshb"
    bm = ssbh.mesh_data.read_mesh(src)
    o0 = bm.objects[0]
    b_pos = [list(v) for v in o0.positions[0].data[:50]]
    b_nrm = [list(v) for v in o0.normals[0].data[:50]]
    p = cp(src, "model.numshb")
    verts = FS.scale_mesh_file(p, F)
    am = ssbh.mesh_data.read_mesh(p)
    a0 = am.objects[0]
    a_pos = [list(v) for v in a0.positions[0].data[:50]]
    a_nrm = [list(v) for v in a0.normals[0].data[:50]]
    w("\nmesh: %d verts across %d objects" % (verts, len(am.objects)))
    check(all(close(a, b * F) for A, B in zip(a_pos, b_pos) for a, b in zip(A, B)),
          "mesh positions not scaled")
    check(all(close(a, b, 1e-5) for A, B in zip(a_nrm, b_nrm) for a, b in zip(A, B)),
          "mesh normals changed (must not)")
    check(len(am.objects) == len(bm.objects), "mesh object count changed")
    w("  vert0 %s -> %s" % (b_pos[0], a_pos[0]))

    # ------------------------------------------------------------------ SWING
    src = MOD + "/motion/body/c80/swing.prc"

    def swing_snapshot(path):
        root = pyprc.param(path)
        top = FS._as_dict(root)
        out = {}
        for ln in ("spheres", "capsules", "planes"):
            if ln not in top:
                continue
            for i, it in enumerate(list(top[ln])):
                for k, v in FS._as_dict(it).items():
                    try:
                        val = v.value
                    except Exception:
                        continue
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        out["%s|%d|%s" % (ln, i, k)] = val
        for ci, c in enumerate(list(top["swingbones"])[:2]):
            for bi, bo in enumerate(list(FS._as_dict(c)["params"])):
                for k, v in FS._as_dict(bo).items():
                    if k == "collisions":
                        continue
                    try:
                        val = v.value
                    except Exception:
                        continue
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        out["chain|%d_%d|%s" % (ci, bi, k)] = val
        return out

    b_sw = swing_snapshot(src)
    p = cp(src, "swing.prc")
    n = FS.scale_swing_file(p, F)
    a_sw = swing_snapshot(p)
    w("\nswing: %d values scaled" % n)

    wrong = []
    for k, bv in b_sw.items():
        av = a_sw[k]
        listname, _idx, field = k.split("|")
        if listname == "chain":
            should = field in FS.SWING_BONE_LENGTH_FIELDS
        else:
            should = field in FS.SWING_LENGTH_FIELDS.get(listname, ())
        if should:
            if not close(av, bv * F, 1e-4):
                wrong.append("%s: %s -> %s (expected %s)" % (k, bv, av, bv * F))
        elif not close(av, bv, 1e-6):
            wrong.append("%s: UNEXPECTEDLY CHANGED %s -> %s" % (k, bv, av))
    check(not wrong, "swing field errors: " + "; ".join(wrong[:4]))
    w("  sphere radius        %s -> %s" % (b_sw.get('spheres|0|radius'), a_sw.get('spheres|0|radius')))
    w("  plane nx  (no change) %s -> %s" % (b_sw.get('planes|0|nx'), a_sw.get('planes|0|nx')))
    w("  plane distance       %s -> %s" % (b_sw.get('planes|0|distance'), a_sw.get('planes|0|distance')))
    w("  minanglez (no change) %s -> %s" % (b_sw.get('chain|0_0|minanglez'), a_sw.get('chain|0_0|minanglez')))
    w("  collisionsizetip     %s -> %s" % (b_sw.get('chain|0_0|collisionsizetip'), a_sw.get('chain|0_0|collisionsizetip')))

    # ------------------------------------------------------------------ SCAN
    found = FS.collect_targets(MOD)
    w("\nscan of eflame: " + ", ".join("%s=%d" % (k, len(v)) for k, v in found.items()))
    check(len(found['anim']) > 0 and len(found['skel']) > 0, "collect_targets found nothing")

    w("\nFAILURES: %d" % len(fails))
    for f in fails:
        w("  -", f)
    w("RESULT: " + ("PASS" if not fails else "FAIL"))
    shutil.rmtree(TMP, ignore_errors=True)

except Exception:
    import traceback
    traceback.print_exc(file=OUT)
    traceback.print_exc()
    OUT.flush()
OUT.close()
