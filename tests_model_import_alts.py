"""Exercise the remaining alts / fallback_path branches in import_model."""
import bpy, sys, os, importlib, traceback

OUT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "alts_paths.txt"),
           "w", encoding="utf-8")
def w(*a):
    print(*a, file=OUT); OUT.flush(); print(*a)

fails = []
addon = next(m for n, m in sys.modules.items()
             if n.endswith("smash-ultimate-blender-animation-workflow"))
IM = importlib.import_module(addon.__name__ + ".source.model.import_model")
ssp = bpy.context.scene.sub_scene_properties

BODY = r"Z:/Documents/Hype Bros Studios/Shigaraki/(Moveset) Shigaraki/fighter/eflame/model/body"
MODS = r"Z:/Documents/Hype Bros Studios/Shigaraki"

w("=== _list_body_alts / _first_complete_alt ===")
try:
    alts = IM._list_body_alts(BODY)
    w("  _list_body_alts -> %r" % (alts,))
    w("  _first_complete_alt -> %r" % (IM._first_complete_alt(alts),))
except Exception:
    traceback.print_exc(file=OUT); traceback.print_exc()
    fails.append("_list_body_alts raised")

w("")
w("=== populate_mods_directory_models (branches at :178 and :221) ===")
try:
    ssp.model_import_models.clear()
    n = IM.populate_mods_directory_models(ssp, MODS)
    w("  returned %r, %d item(s)" % (n, len(ssp.model_import_models)))
    for item in list(ssp.model_import_models)[:10]:
        w("    %-24s alts=%d fallback=%s files=%d"
          % (item.name, len(item.alts), bool(item.fallback_path), len(item.files)))
        for alt in list(item.alts)[:4]:
            w("        alt %-8s -> %s" % (alt.name, os.path.basename(alt.path)))
    if not len(ssp.model_import_models):
        w("  NOTE: nothing found, so those branches did not run")
except Exception:
    traceback.print_exc(file=OUT); traceback.print_exc()
    fails.append("populate_mods_directory_models raised")

w("")
w("=== _add_model_folder_to_list directly (line 78, where it crashed) ===")
try:
    ssp.model_import_models.clear()
    ok = IM._add_model_folder_to_list(ssp, BODY + "/c81", "c81")
    w("  returned %r, %d item(s)" % (ok, len(ssp.model_import_models)))
    for item in ssp.model_import_models:
        w("    name=%r alts=%d fallback=%r"
          % (item.name, len(item.alts), bool(item.fallback_path)))
except Exception:
    traceback.print_exc(file=OUT); traceback.print_exc()
    fails.append("_add_model_folder_to_list raised")

w("")
w("FAILURES: %d" % len(fails))
for f in fails:
    w("  - " + f)
w("RESULT: " + ("PASS" if not fails else "FAIL"))
OUT.close()
