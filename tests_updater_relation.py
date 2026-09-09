"""relation_to_remote must distinguish behind / ahead / diverged / current."""
import sys, os, importlib, subprocess, tempfile, shutil

addon = next(m for n, m in sys.modules.items()
             if n.endswith("smash-ultimate-blender-animation-workflow"))
VC = importlib.import_module(addon.__name__ + ".source.updater.version_check")

fails = []
def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)

# --- the live repo: local is ahead of the remote right now ---
p = VC.get_addon_path()
head, msg, _ = VC.commit_info(p, "HEAD")
try:
    remote, rmsg, rdate, _l, _r = VC.fetch_latest(p, VC.GIT_TIMEOUT_CHECK)
    rel = VC.relation_to_remote(p, head, remote)
    print("live repo: HEAD=%s remote=%s -> %r" % (head[:8], remote[:8], rel))
    check(rel in ("ahead", "current", "behind", "diverged"), "live relation is a known value")
    if rel == "ahead":
        check(True, "local ahead is reported as 'ahead', not an available update")
except Exception as e:
    print("  (live fetch unavailable: %r)" % (e,))

# --- synthetic repo covering every case deterministically ---
tmp = tempfile.mkdtemp(prefix="updrel_")
def git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd or tmp, capture_output=True,
                          text=True, check=True).stdout.strip()
try:
    git("init", "-q", tmp)
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    open(os.path.join(tmp, "f.txt"), "w").write("1")
    git("add", "."); git("commit", "-qm", "c1")
    c1 = git("rev-parse", "HEAD")
    open(os.path.join(tmp, "f.txt"), "w").write("2")
    git("commit", "-qam", "c2")
    c2 = git("rev-parse", "HEAD")
    # a sibling of c2 for the diverged case
    git("checkout", "-q", c1)
    open(os.path.join(tmp, "g.txt"), "w").write("x")
    git("add", "."); git("commit", "-qm", "c3")
    c3 = git("rev-parse", "HEAD")

    print("\nsynthetic repo:")
    check(VC.relation_to_remote(tmp, c2, c2) == "current", "same commit -> current")
    check(VC.relation_to_remote(tmp, c1, c2) == "behind",
          "local older than remote -> behind (a real update)")
    check(VC.relation_to_remote(tmp, c2, c1) == "ahead",
          "local newer than remote -> ahead (the bug: was reported as an update)")
    check(VC.relation_to_remote(tmp, c2, c3) == "diverged",
          "sibling commits -> diverged")
    check(VC.relation_to_remote(tmp, c2, "0" * 40) == "unknown",
          "remote commit absent locally -> unknown")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nFAILURES: %d" % len(fails))
for f in fails:
    print("  - " + f)
print("RESULT: " + ("PASS" if not fails else "FAIL"))
sys.stdout.flush()
