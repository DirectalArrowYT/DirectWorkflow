"""Nail down the ssbh transform convention before touching any file.

ssbh stores a bone transform as a row-major 4x4 with the translation in row 3, which means
points are row vectors: p' = p @ M. Under that convention, re-expressing bone B's local frame
by a rotation F (about B's own origin) is:

    M_B'  = F @ M_B          B' -> B -> parent
    M_C'  = M_C @ inv(F)     child -> B -> B'

and the child's offset in the new frame is row3(M_C) @ inv(F). We want that on +X, so inv(F)
must map the child direction onto +X, i.e. F maps +X onto it: row 0 of F is the unit child
direction.

This script asserts that, and asserts the property that makes the whole edit safe: every
bone's WORLD transform is unchanged, so the mesh binds exactly as before.
"""
import numpy as np

def correction(child_offset):
    """Rotation F (3x3, row-vector convention) whose +X maps onto `child_offset`."""
    v = np.array(child_offset, dtype=np.float64)
    n = np.linalg.norm(v)
    if n < 1e-9:
        return np.eye(3)
    d = v / n
    x = np.array([1.0, 0.0, 0.0])
    dot = float(np.dot(x, d))
    if dot > 1.0 - 1e-9:
        return np.eye(3)
    # NEAR-flip, not just exact: the minimal rotation turns about cross(+X, d), which vanishes
    # as d approaches -X, so its direction is decided by whatever noise is in the last decimals.
    # Shigaraki's hair sits at (-1.304, -0.0, -0.0) - a reversal to within 1e-6 - and taking the
    # minimal path there would set 22 bones' roll from float dust. Anything this close gets the
    # deterministic half turn instead.
    if dot < -1.0 + 1e-6:
        # Half circle about Z: +X -> -X, +Y -> -Y, +Z kept. Z is kept rather than Y because the
        # bone's Z is the axis the minanglez/maxanglez limits are written against.
        return np.array([[-1.0, 0.0, 0.0],
                         [0.0, -1.0, 0.0],
                         [0.0, 0.0, 1.0]])
    axis = np.cross(x, d)
    axis /= np.linalg.norm(axis)
    angle = np.arccos(max(-1.0, min(1.0, dot)))
    K = np.array([[0.0, -axis[2], axis[1]],
                  [axis[2], 0.0, -axis[0]],
                  [-axis[1], axis[0], 0.0]])
    # Rodrigues for column vectors, transposed for the row-vector convention.
    R_col = np.eye(3) + np.sin(angle) * K + (1.0 - np.cos(angle)) * (K @ K)
    return R_col.T

def m4(r3):
    m = np.eye(4)
    m[:3, :3] = r3
    return m

fails = []
def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)

rng = np.random.default_rng(0)

print("=== correction() puts the child on +X ===")
for name, off in [("exact -X", [-1.304, 0.0, 0.0]),
                  ("exact +X", [1.92, 0.0, 0.0]),
                  ("-X with noise", [-2.05, -1e-6, 3e-7]),
                  ("oblique", [-1.0, 0.4, -0.2]),
                  ("+Y", [0.0, 1.5, 0.0]),
                  ("-Z", [0.0, 0.0, -0.8])]:
    F = correction(off)
    check(abs(np.linalg.det(F) - 1.0) < 1e-9, "%s: F is a rotation (det=1)" % name)
    newoff = np.array(off) @ np.linalg.inv(F)
    # Tolerance scales with the input's own off-axis content: a genuine rotation preserves it.
    tol = max(1e-9, 2.0 * float(np.linalg.norm(np.array(off)[1:])))
    onx = abs(newoff[0]) > 1e-6 and abs(newoff[1]) <= tol and abs(newoff[2]) <= tol
    check(onx and newoff[0] > 0, "%s: child lands on +X -> %s" % (name, np.round(newoff, 6)))
    check(abs(np.linalg.norm(newoff) - np.linalg.norm(off)) < 1e-9,
          "%s: length preserved" % name)

print()
print("=== world transforms are unchanged by the re-expression ===")
# A 3-bone chain with arbitrary local transforms, row-vector convention.
def rand_m():
    q = rng.normal(size=4); q /= np.linalg.norm(q)
    w_, x_, y_, z_ = q
    R = np.array([
        [1-2*(y_*y_+z_*z_), 2*(x_*y_+z_*w_),   2*(x_*z_-y_*w_)],
        [2*(x_*y_-z_*w_),   1-2*(x_*x_+z_*z_), 2*(y_*z_+x_*w_)],
        [2*(x_*z_+y_*w_),   2*(y_*z_-x_*w_),   1-2*(x_*x_+y_*y_)],
    ])
    m = np.eye(4); m[:3, :3] = R; m[3, :3] = rng.normal(size=3) * 2.0
    return m

A, B, C = rand_m(), rand_m(), rand_m()          # A=parent, B=bone, C=child, each local
world_before = {"A": A, "B": B @ A, "C": C @ B @ A}

childoff = C[3, :3]
F = m4(correction(childoff))
Finv = np.linalg.inv(F)
# The bone gets F applied in its own frame; its children absorb the inverse.
B2 = F @ B
C2 = C @ Finv
world_after = {"A": A, "B": B2 @ A, "C": C2 @ B2 @ A}

check(np.allclose(world_before["C"], world_after["C"]),
      "child world transform identical (mesh binds unchanged)")
check(np.allclose(world_before["B"][3, :3], world_after["B"][3, :3]),
      "bone world POSITION identical (only its axes turn)")
check(not np.allclose(world_before["B"][:3, :3], world_after["B"][:3, :3]),
      "bone world ORIENTATION did change, which is the point")
newoff = C2[3, :3]
check(abs(newoff[0]) > 1e-9 and abs(newoff[1]) < 1e-9 and abs(newoff[2]) < 1e-9 and newoff[0] > 0,
      "child now sits on +X of the bone -> %s" % np.round(newoff, 9))

print()
print("=== a whole chain, fixed root-to-tip, ends up all +X ===")
locals_ = [rand_m() for _ in range(5)]          # 0 is the chain root's parent
# Force each child offset to be along -X, as Shigaraki's hair is.
for i in range(1, 5):
    locals_[i][3, :3] = np.array([-1.5 - i * 0.1, 0.0, 0.0])
def world_chain(ls):
    out = []
    acc = np.eye(4)
    for m in ls:
        acc = m @ acc
        out.append(acc.copy())
    return out
before = world_chain(locals_)
Fs = [None] * 5
for i in range(1, 4):                            # bones 1..3 have children
    Fs[i] = m4(correction(locals_[i + 1][3, :3]))
work = [m.copy() for m in locals_]
for i in range(1, 5):
    Fself = Fs[i] if Fs[i] is not None else np.eye(4)
    Fpar = Fs[i - 1] if (i - 1) >= 0 and Fs[i - 1] is not None else np.eye(4)
    work[i] = Fself @ locals_[i] @ np.linalg.inv(Fpar)
after = world_chain(work)
check(all(np.allclose(before[i][3, :3], after[i][3, :3]) for i in range(5)),
      "every bone keeps its world position")
axes = []
for i in range(1, 4):
    off = work[i + 1][3, :3]
    axes.append("+X" if (off[0] > 0 and abs(off[1]) < 1e-9 and abs(off[2]) < 1e-9) else "?")
check(all(a == "+X" for a in axes), "all child offsets now +X -> %s" % axes)

print()
print("FAILURES: %d" % len(fails))
for f in fails:
    print("  - " + f)
print("RESULT: " + ("PASS" if not fails else "FAIL"))
