"""
Smash Ultimate Blender Auto-Updater System

This module provides a comprehensive auto-updater for the Smash Ultimate Blender plugin.
It directly monitors this fork's own private repository for new commits and updates
automatically.

1. Branch Monitoring: Checks the private repo for new commits on the main branch
2. One-Click Update: Single button fetches, applies, and restarts automatically
3. Safe Installation: Refuses to run over local edits instead of silently discarding them
4. Auto-Restart: Restarts Blender automatically to complete the update process

Repository: https://github.com/DirectalArrowYT/DirectWorkflow
Branch: DirectWorkflow

The system operates through a state machine with the following states:
- idle: Ready for operations
- checking: Checking for updates
- downloading: Fetching from the remote
- installing: Applying the update and preparing restart

Usage:
The updater automatically checks for new commits when the plugin loads.
If new code is available, a panel will appear in the 3D viewport sidebar
under the "Ultimate" category with a single "Download & Install Update" button
that handles the entire update process automatically.
"""

import os
import platform
import shutil
import subprocess
import threading

import bpy
from bpy.types import Operator

UPDATE_AVAILABLE: bool = None
# How local HEAD relates to the remote:
# current / behind / ahead / diverged / unknown.
UPDATE_RELATION: str = "current"
LATEST_COMMIT_SHA: str = None
LATEST_COMMIT_MESSAGE: str = None
LATEST_COMMIT_DATE: str = None
CURRENT_COMMIT_SHA: str = None
CURRENT_COMMIT_MESSAGE: str = None
UPDATE_DOWNLOAD_PROGRESS: float = 0.0
UPDATE_STATUS: str = "idle"  # idle, checking, downloading, installing, ready_to_restart

# This fork's own public remote. Fetching a public repo over HTTPS needs no
# credentials, which matters here: _run_git() hides the console window, so an
# auth prompt would be invisible and unanswerable. Pushes still go through
# GitHub Desktop from the editable copy; this updater only ever reads.
#
# The branch is DirectWorkflow rather than main - that repo's main holds the
# upstream history it was seeded from, which is unrelated to this fork's.
#
# fetch_latest() below is written to race several addresses and take whichever
# answers first. That's down to one entry now, which costs nothing and leaves
# the door open: adding a LAN or Tailscale address back is a one-line append,
# and the check stays bounded by GIT_TIMEOUT_CHECK however many there are.
UPDATE_REMOTE_URLS = [
    ("GitHub", "https://github.com/DirectalArrowYT/DirectWorkflow.git"),
]
UPDATE_REMOTE_BRANCH = "DirectWorkflow"

GIT_TIMEOUT_CHECK = 8       # seconds - this runs on every Blender startup, keep it short
GIT_TIMEOUT_INSTALL = 30    # seconds - only runs when the user clicks the button

# This addon is a self-maintained fork (own git history, published to the
# remote above) with custom swing-bone-collision tools that don't
# exist upstream. The auto-updater used to overwrite this folder with whatever
# was on CrusherD2/smash-ultimate-blender's animation-workflow branch - an
# unrelated repo - which would have silently deleted those local changes, so
# it was disabled outright.
#
# It's safe to re-enable now that it points at THIS fork's own repo instead:
# pulling from UPDATE_REMOTE_URLS *is* the intended way to receive this fork's
# changes (whatever gets pushed there from the editable copy under Hype Bros
# Studios), not a hazard to them. It still refuses to run over uncommitted
# local edits in THIS folder specifically - see SUB_OP_download_update.
DISABLE_UPDATE_CHECK = False


def get_addon_path():
    """Get the path to the current addon directory"""
    return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


# =============================================================================
# GIT HELPERS
# =============================================================================
def _git_exe():
    return shutil.which("git") or "git"


def _run_git(args, cwd, timeout):
    """Run git quietly - no console window, raises with stderr on failure.

    Sets GIT_SSH_COMMAND to bound the SSH connection attempt itself, not just
    rely on subprocess.run's own `timeout`. Measured on this machine: without
    it, fetching from an unreachable host took 21s wall-clock even with
    timeout=3 passed here. `git fetch` over SSH spawns ssh.exe as a
    grandchild; Python's timeout only terminates the immediate child
    (git.exe), and Popen.communicate() then blocks reading stdout/stderr
    until every handle to that pipe closes - including the orphaned ssh.exe's,
    which doesn't happen until SSH's own much longer OS-default connect
    timeout finally gives up. `ConnectTimeout` makes ssh give up on its own
    well before that, so git.exe (and therefore this call) exits promptly.
    BatchMode=yes matters independently of the timeout: with CREATE_NO_WINDOW
    hiding the console, a host-key or password prompt would be invisible and
    block forever with no way to answer it.
    """
    creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
    env = os.environ.copy()
    ssh_connect_timeout = max(1, int(timeout) - 1)
    env["GIT_SSH_COMMAND"] = (
        f"ssh -o BatchMode=yes -o ConnectTimeout={ssh_connect_timeout} "
        f"-o StrictHostKeyChecking=accept-new"
    )
    result = subprocess.run(
        [_git_exe(), *args], cwd=cwd, capture_output=True, text=True,
        timeout=timeout, creationflags=creationflags, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def is_git_repo(path):
    return os.path.isdir(os.path.join(path, ".git"))


def is_working_tree_dirty(addon_path, timeout=GIT_TIMEOUT_CHECK):
    return bool(_run_git(["status", "--porcelain"], addon_path, timeout))


def _is_ancestor(addon_path, maybe_ancestor, descendant, timeout=GIT_TIMEOUT_CHECK):
    """True if `maybe_ancestor` is reachable from `descendant`.

    git exits 0 for yes and 1 for no, and _run_git raises on any non-zero, so
    the "no" answer arrives as an exception rather than a return value.
    """
    try:
        _run_git(["merge-base", "--is-ancestor", maybe_ancestor, descendant],
                 addon_path, timeout)
        return True
    except Exception:
        return False


def relation_to_remote(addon_path, local_sha, remote_sha, timeout=GIT_TIMEOUT_CHECK):
    """How local HEAD relates to the fetched remote commit.

    'current'  - same commit
    'behind'   - remote is ahead of us, a fast-forward: a real update
    'ahead'    - we have commits the remote does not; nothing to install
    'diverged' - both sides have unique commits; installing would lose work
    'unknown'  - the remote commit is not in the object store, so no answer
    """
    if local_sha == remote_sha:
        return "current"
    try:
        _run_git(["cat-file", "-e", remote_sha + "^{commit}"], addon_path, timeout)
    except Exception:
        return "unknown"
    local_in_remote = _is_ancestor(addon_path, local_sha, remote_sha, timeout)
    remote_in_local = _is_ancestor(addon_path, remote_sha, local_sha, timeout)
    if local_in_remote:
        return "behind"
    if remote_in_local:
        return "ahead"
    return "diverged"


def commit_info(addon_path, ref, timeout=GIT_TIMEOUT_CHECK):
    """(sha, subject, iso date) for a ref that must already exist locally."""
    sha = _run_git(["rev-parse", ref], addon_path, timeout)
    message = _run_git(["log", "-1", "--format=%s", ref], addon_path, timeout)
    date = _run_git(["log", "-1", "--format=%cI", ref], addon_path, timeout)
    return sha, message, date


def fetch_latest(addon_path, timeout):
    """Race every address in UPDATE_REMOTE_URLS, take whichever answers first.

    Each attempt fetches into its OWN ref (refs/_update_check/<label>) rather
    than the shared FETCH_HEAD - two `git fetch` calls running at once would
    otherwise both be writing that same file, which is exactly the kind of
    race this function exists to avoid. Since every address points at the
    same underlying repo, whichever wins carries the same commit, so there's
    no "wrong" one to pick - only a faster or slower one.

    Doesn't touch the working tree or the index. Returns
    (sha, message, iso_date, label, winning_ref).
    """
    result = {}
    lock = threading.Lock()
    all_done = threading.Event()
    remaining = len(UPDATE_REMOTE_URLS)

    def attempt(label, url):
        nonlocal remaining
        ref = f"refs/_update_check/{label}"
        try:
            _run_git(["fetch", url, f"{UPDATE_REMOTE_BRANCH}:{ref}"], addon_path, timeout)
            with lock:
                result.setdefault("winner", (label, url, ref))
        except Exception as e:
            with lock:
                result.setdefault("errors", []).append(f"{label} ({url}): {e}")
        finally:
            with lock:
                remaining -= 1
                if remaining == 0:
                    all_done.set()

    threads = [threading.Thread(target=attempt, args=(label, url), daemon=True)
               for label, url in UPDATE_REMOTE_URLS]
    for t in threads:
        t.start()

    # Wake as soon as anyone wins rather than waiting for every attempt to
    # finish; a losing fetch may still be mid-timeout in the background, but
    # nothing after this point depends on it.
    while True:
        with lock:
            if "winner" in result or remaining == 0:
                break
        if all_done.wait(0.1):
            break

    with lock:
        winner = result.get("winner")
        errors = list(result.get("errors", []))

    if winner is None:
        raise RuntimeError(" | ".join(errors) or "no update remotes configured")

    label, url, ref = winner
    if label != UPDATE_REMOTE_URLS[0][0]:
        print(f"Smash_ultimate_blender: reached the update server via {label} ({url})")
    sha, message, date = commit_info(addon_path, ref, timeout)
    return sha, message, date, label, ref


# =============================================================================
# UPDATE CHECK
# =============================================================================
def check_for_newer_version():
    """Fetch the remote branch and compare it against local HEAD."""
    global UPDATE_STATUS, UPDATE_AVAILABLE
    global LATEST_COMMIT_SHA, LATEST_COMMIT_MESSAGE, LATEST_COMMIT_DATE
    global CURRENT_COMMIT_SHA, CURRENT_COMMIT_MESSAGE
    global UPDATE_RELATION

    if DISABLE_UPDATE_CHECK:
        UPDATE_STATUS = "idle"
        UPDATE_AVAILABLE = False
        UPDATE_RELATION = "current"
        return

    UPDATE_STATUS = "checking"
    addon_path = get_addon_path()

    if not is_git_repo(addon_path):
        urls = " or ".join(url for _, url in UPDATE_REMOTE_URLS)
        print(f"Smash_ultimate_blender: {addon_path} is not a git checkout - can't "
              f"check for updates. Re-clone it from {urls}.")
        UPDATE_STATUS = "idle"
        UPDATE_AVAILABLE = False
        return

    try:
        current_sha, current_message, _ = commit_info(addon_path, "HEAD")
        latest_sha, latest_message, latest_date, _label, _ref = fetch_latest(
            addon_path, GIT_TIMEOUT_CHECK)

        CURRENT_COMMIT_SHA, CURRENT_COMMIT_MESSAGE = current_sha, current_message
        LATEST_COMMIT_SHA = latest_sha
        LATEST_COMMIT_MESSAGE = latest_message
        LATEST_COMMIT_DATE = latest_date
        # Comparing the two SHAs for inequality is not enough. This fork is
        # developed in place and pushed from here, so local is routinely AHEAD
        # of the remote - and inequality alone reported that as an available
        # update, offering to "upgrade" the addon to one of its own ancestors
        # and delete the newer local commits on install.
        UPDATE_RELATION = relation_to_remote(addon_path, current_sha, latest_sha)
        UPDATE_AVAILABLE = UPDATE_RELATION == "behind"

        if UPDATE_RELATION == "behind":
            print(f"Smash_ultimate_blender: update available - "
                  f"{current_sha[:8]} -> {latest_sha[:8]}: {latest_message[:100]}")
        elif UPDATE_RELATION == "ahead":
            print(f"Smash_ultimate_blender: local is ahead of the remote "
                  f"({current_sha[:8]}, remote {latest_sha[:8]}) - nothing to install. "
                  f"Push when ready.")
        elif UPDATE_RELATION == "diverged":
            print(f"Smash_ultimate_blender: local {current_sha[:8]} and remote "
                  f"{latest_sha[:8]} have diverged - not offering an update, since "
                  f"installing it would discard local commits.")
        elif UPDATE_RELATION == "unknown":
            print(f"Smash_ultimate_blender: remote commit {latest_sha[:8]} is not in "
                  f"the local object store - can't tell whether it is newer.")
        else:
            print("Smash_ultimate_blender: plugin is up to date")

    except Exception as e:
        # fetch_latest() already tried every address in UPDATE_REMOTE_URLS and
        # folded each one's error (including any timeout) into this message,
        # so there's nothing a narrower except clause would add here.
        print(f"Smash_ultimate_blender: couldn't check for updates - {e}")
        UPDATE_AVAILABLE = False

    UPDATE_STATUS = "idle"


def restart_blender_after_update():
    """Standalone function to restart Blender after update"""
    try:
        current_file = bpy.data.filepath
        blender_exe = bpy.app.binary_path
        cmd = [blender_exe, current_file] if current_file else [blender_exe]

        if platform.system() == "Windows":
            subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            subprocess.Popen(cmd, start_new_session=True)

        bpy.ops.wm.quit_blender()

    except Exception as e:
        print(f"Error restarting Blender: {e}")

    return None  # Don't reschedule the timer


# =============================================================================
# INSTALL
# =============================================================================
class SUB_OP_download_update(Operator):
    """Fetch and apply the latest update from the private repo (non-blocking)"""
    bl_idname = "sub.download_update"
    bl_label = "Download & Install Update"
    bl_description = "Fetch and apply the latest version of the plugin, then restart Blender"

    _thread = None
    _error = None
    _stage = "idle"  # idle, downloading, installing, done, error
    _timer = None

    def execute(self, context):
        global UPDATE_STATUS, UPDATE_DOWNLOAD_PROGRESS

        if DISABLE_UPDATE_CHECK:
            self.report({'ERROR'}, "Updater is disabled.")
            return {'CANCELLED'}

        if not UPDATE_AVAILABLE:
            self.report({'ERROR'}, "No update available")
            return {'CANCELLED'}

        addon_path = get_addon_path()
        try:
            if is_working_tree_dirty(addon_path):
                self.report({'ERROR'},
                    "This addon folder has local changes - refusing to overwrite "
                    "them. Edit and push from the SmashScripts copy under Hype "
                    "Bros Studios instead, then run this update to pull it here.")
                return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"Couldn't check the addon folder's git status: {e}")
            return {'CANCELLED'}

        UPDATE_STATUS = "downloading"
        UPDATE_DOWNLOAD_PROGRESS = 0.0
        self._stage = "downloading"
        self._error = None

        def thread_func():
            global UPDATE_DOWNLOAD_PROGRESS, UPDATE_STATUS, LATEST_COMMIT_SHA
            try:
                _sha, _msg, _date, _label, winning_ref = fetch_latest(
                    addon_path, GIT_TIMEOUT_INSTALL)
                UPDATE_DOWNLOAD_PROGRESS = 0.5
                self._stage = "installing"
                UPDATE_STATUS = "installing"

                # Reset to the ref fetch_latest() actually populated, not
                # FETCH_HEAD - a second, still-running attempt against the
                # other address could overwrite FETCH_HEAD after this point.
                _run_git(["reset", "--hard", winning_ref], addon_path, GIT_TIMEOUT_INSTALL)
                UPDATE_DOWNLOAD_PROGRESS = 1.0
                self._stage = "done"
            except Exception as e:
                self._error = str(e)
                self._stage = "error"

        self._thread = threading.Thread(target=thread_func)
        self._thread.start()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        wm = context.window_manager
        if event.type == 'TIMER':
            global UPDATE_STATUS
            if self._stage == "downloading":
                UPDATE_STATUS = "downloading"
            elif self._stage == "installing":
                UPDATE_STATUS = "installing"
            elif self._stage == "done":
                UPDATE_STATUS = "idle"
                wm.event_timer_remove(self._timer)
                self.report({'INFO'}, "Update installed successfully. Restarting Blender...")
                bpy.app.timers.register(restart_blender_after_update, first_interval=1.0)
                return {'FINISHED'}
            elif self._stage == "error":
                UPDATE_STATUS = "idle"
                wm.event_timer_remove(self._timer)
                self.report({'ERROR'}, f"Update failed: {self._error}")
                return {'CANCELLED'}

            for area in context.screen.areas:
                area.tag_redraw()
        return {'PASS_THROUGH'}


class SUB_OP_install_update(Operator):
    """[DEPRECATED] Kept only so registration doesn't break old keymaps/scripts"""
    bl_idname = "sub.install_update"
    bl_label = "Install Update (Legacy)"
    bl_description = "[DEPRECATED] Use Download & Install Update instead"

    def execute(self, context):
        self.report({'INFO'}, "This step is no longer needed - "
                               "'Download & Install Update' now does both in one click.")
        return {'FINISHED'}


class SUB_OP_restart_blender(Operator):
    """Restart Blender to complete the update"""
    bl_idname = "sub.restart_blender"
    bl_label = "Restart Blender"
    bl_description = "Restart Blender to complete the update process"

    def execute(self, context):
        current_file = bpy.data.filepath
        try:
            blender_exe = bpy.app.binary_path
            cmd = [blender_exe, current_file] if current_file else [blender_exe]

            if platform.system() == "Windows":
                subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                subprocess.Popen(cmd, start_new_session=True)

            bpy.ops.wm.quit_blender()

        except Exception as e:
            print(f"Error restarting Blender: {e}")
            self.report({'ERROR'}, f"Failed to restart Blender: {str(e)}")
            return {'CANCELLED'}

        return {'FINISHED'}

    def invoke(self, context, event):
        if bpy.data.is_dirty:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Current file has unsaved changes!")
        layout.label(text="Save before restarting?")
        layout.operator("wm.save_mainfile", text="Save and Continue")


class SUB_OP_check_for_updates(Operator):
    """Manually check for updates"""
    bl_idname = "sub.check_for_updates"
    bl_label = "Check for Updates"
    bl_description = "Manually check for available updates"

    def execute(self, context):
        if DISABLE_UPDATE_CHECK:
            self.report({'INFO'}, "Updater is disabled.")
            return {'FINISHED'}

        check_for_newer_version()

        if UPDATE_AVAILABLE:
            commit_short = LATEST_COMMIT_SHA[:8] if LATEST_COMMIT_SHA else "unknown"
            self.report({'INFO'}, f"Update available: commit {commit_short}")
        else:
            self.report({'INFO'}, "No updates available")

        return {'FINISHED'}


# Register properties for the scene
def register_properties():
    pass


def unregister_properties():
    pass
