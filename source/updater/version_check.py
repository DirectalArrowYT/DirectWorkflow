"""
Smash Ultimate Blender Auto-Updater System

This module provides a comprehensive auto-updater for the Smash Ultimate Blender plugin.
It directly monitors this fork's own private repository for new commits and updates
automatically.

1. Branch Monitoring: Checks the private repo for new commits on the main branch
2. One-Click Update: Single button fetches, applies, and restarts automatically
3. Safe Installation: Refuses to run over local edits instead of silently discarding them
4. Auto-Restart: Restarts Blender automatically to complete the update process

Repository: shoup@100.118.148.112:E:/Git/smash-ultimate-blender-animation-workflow
Branch: main

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
LATEST_COMMIT_SHA: str = None
LATEST_COMMIT_MESSAGE: str = None
LATEST_COMMIT_DATE: str = None
CURRENT_COMMIT_SHA: str = None
CURRENT_COMMIT_MESSAGE: str = None
UPDATE_DOWNLOAD_PROGRESS: float = 0.0
UPDATE_STATUS: str = "idle"  # idle, checking, downloading, installing, ready_to_restart

# This fork's own remote - the same private server the SmashScripts build
# tooling uses (see machines.json in that repo for the per-machine layout
# convention this follows). It's reached over SSH (the git protocol), not
# HTTPS, so this can't go through requests/urllib the way the old GitHub API
# check did - every operation below shells out to the system `git` instead.
UPDATE_REMOTE_URL = "shoup@100.118.148.112:E:/Git/smash-ultimate-blender-animation-workflow"
UPDATE_REMOTE_BRANCH = "main"

GIT_TIMEOUT_CHECK = 8       # seconds - this runs on every Blender startup, keep it short
GIT_TIMEOUT_INSTALL = 30    # seconds - only runs when the user clicks the button

# This addon is a locally-maintained fork (own git history, pushed to the
# private remote above) with custom swing-bone-collision tools that don't
# exist upstream. The auto-updater used to overwrite this folder with whatever
# was on CrusherD2/smash-ultimate-blender's animation-workflow branch - an
# unrelated repo - which would have silently deleted those local changes, so
# it was disabled outright.
#
# It's safe to re-enable now that it points at THIS fork's own repo instead:
# pulling from UPDATE_REMOTE_URL *is* the intended way to receive this fork's
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
    """Run git quietly - no console window, raises with stderr on failure."""
    creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
    result = subprocess.run(
        [_git_exe(), *args], cwd=cwd, capture_output=True, text=True,
        timeout=timeout, creationflags=creationflags,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def is_git_repo(path):
    return os.path.isdir(os.path.join(path, ".git"))


def is_working_tree_dirty(addon_path, timeout=GIT_TIMEOUT_CHECK):
    return bool(_run_git(["status", "--porcelain"], addon_path, timeout))


def commit_info(addon_path, ref, timeout=GIT_TIMEOUT_CHECK):
    """(sha, subject, iso date) for a ref that must already exist locally."""
    sha = _run_git(["rev-parse", ref], addon_path, timeout)
    message = _run_git(["log", "-1", "--format=%s", ref], addon_path, timeout)
    date = _run_git(["log", "-1", "--format=%cI", ref], addon_path, timeout)
    return sha, message, date


def fetch_latest(addon_path, timeout):
    """Fetch UPDATE_REMOTE_BRANCH straight from UPDATE_REMOTE_URL into FETCH_HEAD.

    Doesn't touch the working tree or the index, and doesn't depend on a
    locally-configured remote (named 'origin' or otherwise) actually pointing
    at UPDATE_REMOTE_URL - it fetches directly from the URL every time.
    """
    _run_git(["fetch", UPDATE_REMOTE_URL, UPDATE_REMOTE_BRANCH], addon_path, timeout)
    return commit_info(addon_path, "FETCH_HEAD", timeout)


# =============================================================================
# UPDATE CHECK
# =============================================================================
def check_for_newer_version():
    """Fetch the remote branch and compare it against local HEAD."""
    global UPDATE_STATUS, UPDATE_AVAILABLE
    global LATEST_COMMIT_SHA, LATEST_COMMIT_MESSAGE, LATEST_COMMIT_DATE
    global CURRENT_COMMIT_SHA, CURRENT_COMMIT_MESSAGE

    if DISABLE_UPDATE_CHECK:
        UPDATE_STATUS = "idle"
        UPDATE_AVAILABLE = False
        return

    UPDATE_STATUS = "checking"
    addon_path = get_addon_path()

    if not is_git_repo(addon_path):
        print(f"Smash_ultimate_blender: {addon_path} is not a git checkout - can't "
              f"check for updates. Re-clone it from {UPDATE_REMOTE_URL}.")
        UPDATE_STATUS = "idle"
        UPDATE_AVAILABLE = False
        return

    try:
        current_sha, current_message, _ = commit_info(addon_path, "HEAD")
        latest_sha, latest_message, latest_date = fetch_latest(addon_path, GIT_TIMEOUT_CHECK)

        CURRENT_COMMIT_SHA, CURRENT_COMMIT_MESSAGE = current_sha, current_message
        LATEST_COMMIT_SHA = latest_sha
        LATEST_COMMIT_MESSAGE = latest_message
        LATEST_COMMIT_DATE = latest_date
        UPDATE_AVAILABLE = current_sha != latest_sha

        if UPDATE_AVAILABLE:
            print(f"Smash_ultimate_blender: update available - "
                  f"{current_sha[:8]} -> {latest_sha[:8]}: {latest_message[:100]}")
        else:
            print("Smash_ultimate_blender: plugin is up to date")

    except subprocess.TimeoutExpired:
        print(f"Smash_ultimate_blender: timed out reaching {UPDATE_REMOTE_URL} "
              f"(server unreachable, or not on the network right now)")
        UPDATE_AVAILABLE = False
    except Exception as e:
        print(f"Smash_ultimate_blender: couldn't check for updates: {e}")
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
                fetch_latest(addon_path, GIT_TIMEOUT_INSTALL)
                UPDATE_DOWNLOAD_PROGRESS = 0.5
                self._stage = "installing"
                UPDATE_STATUS = "installing"

                _run_git(["reset", "--hard", "FETCH_HEAD"], addon_path, GIT_TIMEOUT_INSTALL)
                UPDATE_DOWNLOAD_PROGRESS = 1.0
                self._stage = "done"
            except subprocess.TimeoutExpired:
                self._error = f"Timed out reaching {UPDATE_REMOTE_URL}"
                self._stage = "error"
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
