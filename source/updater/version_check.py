"""
Smash Ultimate Blender Auto-Updater System

This module provides a comprehensive auto-updater for the Smash Ultimate Blender plugin.
It directly monitors the animation-workflow branch for code changes and updates automatically.

1. Branch Monitoring: Checks the GitHub repository for new commits on animation-workflow branch
2. One-Click Update: Single button downloads, installs, and restarts automatically
3. Safe Installation: Extracts and installs the update, backing up the current version
4. Auto-Restart: Restarts Blender automatically to complete the update process

Features:
- Monitors commits directly on animation-workflow branch (no releases needed)
- Automatic detection of new code pushes
- Progress tracking during downloads
- Automatic backup creation before installation
- Smart binary dependency handling (handles locked .pyd/.so files)
- Graceful error handling and recovery
- Cross-platform restart functionality

Repository: https://github.com/CrusherD2/smash-ultimate-blender
Branch: animation-workflow (monitors this branch for code changes)

The system operates through a state machine with the following states:
- idle: Ready for operations
- checking: Checking for updates
- downloading: Downloading update file
- installing: Installing update and preparing restart

Usage:
The updater automatically checks for new commits when the plugin loads.
If new code is available, a panel will appear in the 3D viewport sidebar
under the "Ultimate" category with a single "Download & Install Update" button
that handles the entire update process automatically.
"""

import re
import requests
import os
import sys
import zipfile
import shutil
import tempfile
import subprocess
import platform
import bpy
from bpy.types import Operator
from bpy.props import StringProperty
import threading
import time

UPDATE_AVAILABLE: bool = None
LATEST_COMMIT_SHA: str = None
LATEST_COMMIT_MESSAGE: str = None
LATEST_COMMIT_DATE: str = None
CURRENT_COMMIT_SHA: str = None
CURRENT_COMMIT_MESSAGE: str = None
BRANCH_DOWNLOAD_URL: str = "https://github.com/CrusherD2/smash-ultimate-blender/archive/refs/heads/animation-workflow.zip"
UPDATE_DOWNLOAD_PROGRESS: float = 0.0
UPDATE_STATUS: str = "idle"  # idle, checking, downloading, installing, ready_to_restart

def get_commit_file_path():
    """Get the path to store the current commit SHA"""
    addon_path = get_addon_path()
    return os.path.join(addon_path, ".current_commit")

def load_current_commit_sha():
    """Load the currently stored commit SHA"""
    try:
        commit_file = get_commit_file_path()
        if os.path.exists(commit_file):
            with open(commit_file, 'r') as f:
                return f.read().strip()
    except Exception as e:
        print(f"Smash_ultimate_blender: Error loading commit SHA: {e}")
    return None

def save_current_commit_sha(sha):
    """Save the current commit SHA"""
    try:
        commit_file = get_commit_file_path()
        with open(commit_file, 'w') as f:
            f.write(sha)
    except Exception as e:
        print(f"Smash_ultimate_blender: Error saving commit SHA: {e}")

def get_commit_message_by_sha(sha):
    """Get commit message for a specific SHA"""
    if not sha:
        return None
    try:
        response = requests.get(f"https://api.github.com/repos/CrusherD2/smash-ultimate-blender/commits/{sha}", timeout=10)
        response.raise_for_status()
        commit_data = response.json()
        commit_info = commit_data.get("commit", {})
        return commit_info.get("message", "No message")
    except Exception as e:
        print(f"Smash_ultimate_blender: Error fetching commit message for {sha}: {e}")
        return "Unknown message"

def check_for_newer_version():
    """
    Check the animation-workflow branch for new commits.
    If there's a newer commit than what we have stored, mark update as available.
    """
    global UPDATE_STATUS, UPDATE_AVAILABLE, LATEST_COMMIT_SHA, LATEST_COMMIT_MESSAGE, LATEST_COMMIT_DATE, CURRENT_COMMIT_SHA, CURRENT_COMMIT_MESSAGE
    
    UPDATE_STATUS = "checking"
    
    # Clean up old binary files from previous updates
    cleanup_old_binaries()

    try:
        # Get the latest commit from the animation-workflow branch
        response = requests.get("https://api.github.com/repos/CrusherD2/smash-ultimate-blender/commits/animation-workflow", timeout=10)
        response.raise_for_status()
        
        commit_data = response.json()
        if not isinstance(commit_data, dict):
            print(f"Smash_ultimate_blender: Unexpected commit API response format: {type(commit_data)}")
            UPDATE_STATUS = "idle"
            return
            
        # Extract commit information
        latest_sha = commit_data.get("sha")
        commit_info = commit_data.get("commit", {})
        latest_message = commit_info.get("message", "No message")
        latest_date = commit_info.get("author", {}).get("date", "Unknown date")
        
        if not latest_sha:
            print("Smash_ultimate_blender: Could not extract commit SHA from response")
            UPDATE_STATUS = "idle"
            return
            
        # Load the current commit SHA we have stored
        current_sha = load_current_commit_sha()
        
        # Get current commit message if we have a SHA
        current_message = get_commit_message_by_sha(current_sha) if current_sha else None
        
        # Store the information
        LATEST_COMMIT_SHA = latest_sha
        LATEST_COMMIT_MESSAGE = latest_message
        LATEST_COMMIT_DATE = latest_date
        CURRENT_COMMIT_SHA = current_sha
        CURRENT_COMMIT_MESSAGE = current_message
        
        # Check if we have a new commit
        if current_sha is None:
            # First time running, store current commit and don't show update
            print("Smash_ultimate_blender: First time checking, storing current commit SHA")
            save_current_commit_sha(latest_sha)
            UPDATE_AVAILABLE = False
        elif current_sha != latest_sha:
            # New commit available!
            print(f"Smash_ultimate_blender: New commit available!")
            print(f"  Current: {current_sha[:8] if current_sha else 'None'}")
            print(f"  Latest:  {latest_sha[:8]}")
            print(f"  Message: {latest_message[:100]}...")
            UPDATE_AVAILABLE = True
        else:
            # No update available
            print("Smash_ultimate_blender: Plugin is up to date")
            UPDATE_AVAILABLE = False
            
    except Exception as e:
        print(f"Smash_ultimate_blender: Couldn't check for branch updates. Error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Smash_ultimate_blender: HTTP Status: {e.response.status_code}")
            print(f"Smash_ultimate_blender: Response: {e.response.text}")
        UPDATE_STATUS = "idle"
        return
    
    UPDATE_STATUS = "idle"

def get_addon_path():
    """Get the path to the current addon directory"""
    return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

def should_skip_file_or_dir(name):
    """Check if a file or directory should be skipped during installation"""
    skip_patterns = [
        '.venv',          # Virtual environments
        '__pycache__',    # Python cache
        '.git',           # Git directory
        '.current_commit', # Our commit tracking file
        '*.pyc',          # Compiled Python files
        '.DS_Store',      # macOS system files
        'Thumbs.db',      # Windows system files
    ]
    
    for pattern in skip_patterns:
        if pattern.startswith('*'):
            if name.endswith(pattern[1:]):
                return True
        elif name == pattern:
            return True
    return False

def is_binary_dependency(name):
    """Check if file is a binary dependency that might be locked"""
    binary_extensions = ['.pyd', '.so', '.dll', '.dylib']
    return any(name.endswith(ext) for ext in binary_extensions)

def safe_copy_binary(src_file, dst_file):
    """Safely copy binary files, handling locked files"""
    try:
        # Try normal copy first
        shutil.copy2(src_file, dst_file)
        return True, None
    except PermissionError as e:
        # If locked, try renaming old file and copying new one
        try:
            backup_file = dst_file + '.old'
            if os.path.exists(dst_file):
                # Remove old backup if it exists
                if os.path.exists(backup_file):
                    try:
                        os.remove(backup_file)
                    except:
                        pass
                # Rename current file to .old
                os.rename(dst_file, backup_file)
            # Now try copying the new file
            shutil.copy2(src_file, dst_file)
            print(f"Smash_ultimate_blender: Updated locked binary {os.path.basename(dst_file)} (old version backed up)")
            return True, None
        except Exception as rename_error:
            return False, f"Could not update binary dependency: {rename_error}"
    except Exception as e:
        return False, str(e)

def safe_copy_tree(src, dst, skip_existing=True):
    """Safely copy directory tree, skipping problematic files"""
    copied_items = []
    failed_items = []
    
    for root, dirs, files in os.walk(src):
        # Filter out directories we should skip
        dirs[:] = [d for d in dirs if not should_skip_file_or_dir(d)]
        
        # Calculate relative path
        rel_path = os.path.relpath(root, src)
        dst_dir = os.path.join(dst, rel_path) if rel_path != '.' else dst
        
        # Create destination directory
        os.makedirs(dst_dir, exist_ok=True)
        
        # Copy files
        for file in files:
            if should_skip_file_or_dir(file):
                continue
                
            src_file = os.path.join(root, file)
            dst_file = os.path.join(dst_dir, file)
            
            try:
                # Skip if file exists and skip_existing is True
                if skip_existing and os.path.exists(dst_file):
                    continue
                
                # Handle binary dependencies specially
                if is_binary_dependency(file):
                    success, error = safe_copy_binary(src_file, dst_file)
                    if success:
                        copied_items.append(dst_file)
                    else:
                        failed_items.append((src_file, error))
                        print(f"Smash_ultimate_blender: Failed to copy binary {src_file}: {error}")
                else:
                    shutil.copy2(src_file, dst_file)
                    copied_items.append(dst_file)
            except Exception as e:
                failed_items.append((src_file, str(e)))
                print(f"Smash_ultimate_blender: Failed to copy {src_file}: {e}")
    
    return copied_items, failed_items

def cleanup_old_binaries():
    """Clean up .old binary files from previous updates"""
    try:
        addon_path = get_addon_path()
        for root, dirs, files in os.walk(addon_path):
            for file in files:
                if file.endswith('.old') and is_binary_dependency(file[:-4]):  # Remove .old extension to check
                    old_file = os.path.join(root, file)
                    try:
                        os.remove(old_file)
                        print(f"Smash_ultimate_blender: Cleaned up old binary: {file}")
                    except Exception as e:
                        print(f"Smash_ultimate_blender: Could not clean up {file}: {e}")
    except Exception as e:
        print(f"Smash_ultimate_blender: Error during binary cleanup: {e}")

def restart_blender_after_update():
    """Standalone function to restart Blender after update"""
    try:
        # Get current blend file path
        current_file = bpy.data.filepath
        
        # Get Blender executable path
        blender_exe = bpy.app.binary_path
        
        # Build command
        if current_file:
            cmd = [blender_exe, current_file]
        else:
            cmd = [blender_exe]
        
        # Start new Blender instance
        if platform.system() == "Windows":
            subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            subprocess.Popen(cmd, start_new_session=True)
        
        # Quit current instance
        bpy.ops.wm.quit_blender()
        
    except Exception as e:
        print(f"Error restarting Blender: {e}")
    
    return None  # Don't reschedule the timer

def download_update_with_progress(url, destination, progress_callback=None):
    """Download file with progress tracking"""
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(destination, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        if total_size > 0:
                            progress = downloaded / total_size
                        else:
                            # If we don't know total size, show progress based on chunks downloaded
                            progress = min(0.95, downloaded / (8192 * 100))  # Estimate based on chunks
                        progress_callback(progress)
        
        # Ensure we show 100% when complete
        if progress_callback:
            progress_callback(1.0)
        
        return True
    except Exception as e:
        print(f"Error downloading update: {e}")
        return False

class SUB_OP_download_update(Operator):
    """Download and install the latest update (non-blocking)"""
    bl_idname = "sub.download_update"
    bl_label = "Download & Install Update"
    bl_description = "Download and install the latest version of the plugin, then restart Blender"

    _thread = None
    _error = None
    _success = False
    _progress = 0.0
    _stage = "idle"  # idle, downloading, installing, done, error
    _download_path = None
    _context = None
    _timer = None

    def execute(self, context):
        global UPDATE_STATUS, UPDATE_DOWNLOAD_PROGRESS, BRANCH_DOWNLOAD_URL

        if not UPDATE_AVAILABLE:
            self.report({'ERROR'}, "No update available")
            return {'CANCELLED'}

        UPDATE_STATUS = "downloading"
        UPDATE_DOWNLOAD_PROGRESS = 0.0
        self._stage = "downloading"
        self._progress = 0.0
        self._error = None
        self._success = False
        self._context = context
        self._download_path = None

        # Create temporary directory for download
        temp_dir = tempfile.mkdtemp()
        download_path = os.path.join(temp_dir, "update.zip")
        self._download_path = download_path

        def progress_callback(progress):
            try:
                # Update progress from background thread
                self._progress = progress
                global UPDATE_DOWNLOAD_PROGRESS
                UPDATE_DOWNLOAD_PROGRESS = progress
                # Note: UI updates will be handled by the modal timer on the main thread
            except Exception as e:
                print(f"Error in progress callback: {e}")

        def thread_func():
            try:
                if not download_update_with_progress(BRANCH_DOWNLOAD_URL, download_path, progress_callback):
                    self._error = "Failed to download update"
                    self._stage = "error"
                    return
                self._stage = "installing"
                UPDATE_STATUS = "installing"
                # Store download path for installation
                context.scene.sub_updater_download_path = download_path
                # Install update
                success = self._install_update(download_path, context)
                if success:
                    self._success = True
                    self._stage = "done"
                else:
                    self._error = "Failed to install update"
                    self._stage = "error"
            except Exception as e:
                self._error = str(e)
                self._stage = "error"

        # Start background thread
        self._thread = threading.Thread(target=thread_func)
        self._thread.start()
        # Add a timer to poll for progress - increased frequency for smoother updates
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        wm = context.window_manager
        if event.type == 'TIMER':
            # Update progress
            global UPDATE_DOWNLOAD_PROGRESS, UPDATE_STATUS
            UPDATE_DOWNLOAD_PROGRESS = self._progress
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
                # Clean up temp dir if needed
                try:
                    if self._download_path:
                        temp_dir = os.path.dirname(self._download_path)
                        if os.path.exists(temp_dir):
                            shutil.rmtree(temp_dir)
                except Exception:
                    pass
                return {'CANCELLED'}
            
            # Force UI redraw to update progress bar
            for area in context.screen.areas:
                area.tag_redraw()
        return {'PASS_THROUGH'}

    def _install_update(self, download_path, context):
        """Internal method to install the downloaded update"""
        global UPDATE_STATUS, LATEST_COMMIT_SHA
        if not download_path or not os.path.exists(download_path):
            self._error = "No downloaded update found"
            return False
        try:
            # Get addon path
            addon_path = get_addon_path()
            # Extract update
            temp_extract_dir = tempfile.mkdtemp()
            with zipfile.ZipFile(download_path, 'r') as zip_ref:
                zip_ref.extractall(temp_extract_dir)
            # Find the extracted folder (should be smash-ultimate-blender-animation-workflow when downloading from branch)
            extracted_contents = os.listdir(temp_extract_dir)
            if len(extracted_contents) == 1:
                extracted_folder = os.path.join(temp_extract_dir, extracted_contents[0])
                # Remove current addon files (except protected files)
                for item in os.listdir(addon_path):
                    if not should_skip_file_or_dir(item):
                        item_path = os.path.join(addon_path, item)
                        try:
                            if os.path.isdir(item_path):
                                shutil.rmtree(item_path)
                            else:
                                os.remove(item_path)
                        except Exception as e:
                            print(f"Smash_ultimate_blender: Warning - couldn't remove {item}: {e}")
                # Copy new files using safe copy method
                copied_items, failed_items = safe_copy_tree(extracted_folder, addon_path, skip_existing=False)
                if failed_items:
                    print(f"Smash_ultimate_blender: Warning - {len(failed_items)} files failed to copy")
                    for failed_file, error in failed_items:
                        print(f"  {failed_file}: {error}")
                # Save the new commit SHA since we successfully installed
                if LATEST_COMMIT_SHA:
                    save_current_commit_sha(LATEST_COMMIT_SHA)
                # Clean up
                shutil.rmtree(temp_extract_dir)
                os.remove(download_path)
                return True
            else:
                self._error = "Unexpected archive structure"
                return False
        except Exception as e:
            self._error = f"Failed to install update: {str(e)}"
            return False

class SUB_OP_install_update(Operator):
    """[DEPRECATED] Install the downloaded update - use Download & Install Update instead"""
    bl_idname = "sub.install_update"
    bl_label = "Install Update (Legacy)"
    bl_description = "[DEPRECATED] Manual installation operator - the main update process now handles this automatically"
    
    def execute(self, context):
        global UPDATE_STATUS
        
        download_path = getattr(context.scene, 'sub_updater_download_path', None)
        if not download_path or not os.path.exists(download_path):
            self.report({'ERROR'}, "No downloaded update found")
            return {'CANCELLED'}
        
        UPDATE_STATUS = "installing"
        
        try:
            # Get addon path
            addon_path = get_addon_path()
            
            # Extract update
            temp_extract_dir = tempfile.mkdtemp()
            with zipfile.ZipFile(download_path, 'r') as zip_ref:
                zip_ref.extractall(temp_extract_dir)
            
            # Find the extracted folder (should be smash-ultimate-blender-animation-workflow when downloading from branch)
            extracted_contents = os.listdir(temp_extract_dir)
            if len(extracted_contents) == 1:
                extracted_folder = os.path.join(temp_extract_dir, extracted_contents[0])
                print(f"Smash_ultimate_blender: Found extracted folder: {extracted_contents[0]}")
                
                # Remove current addon files (except protected files)
                for item in os.listdir(addon_path):
                    if not should_skip_file_or_dir(item):
                        item_path = os.path.join(addon_path, item)
                        try:
                            if os.path.isdir(item_path):
                                shutil.rmtree(item_path)
                            else:
                                os.remove(item_path)
                        except Exception as e:
                            print(f"Smash_ultimate_blender: Warning - couldn't remove {item}: {e}")
                
                # Copy new files using safe copy method
                copied_items, failed_items = safe_copy_tree(extracted_folder, addon_path, skip_existing=False)
                
                if failed_items:
                    print(f"Smash_ultimate_blender: Warning - {len(failed_items)} files failed to copy")
                    for failed_file, error in failed_items:
                        print(f"  {failed_file}: {error}")
                else:
                    print(f"Smash_ultimate_blender: All files copied successfully!")
                
                print(f"Smash_ultimate_blender: Successfully copied {len(copied_items)} files")
                
                # Save the new commit SHA since we successfully installed
                if LATEST_COMMIT_SHA:
                    save_current_commit_sha(LATEST_COMMIT_SHA)
                    print(f"Smash_ultimate_blender: Updated to commit {LATEST_COMMIT_SHA[:8]}")
                
                # Clean up
                shutil.rmtree(temp_extract_dir)
                os.remove(download_path)
                
                # Automatically restart Blender
                self.report({'INFO'}, "Update installed successfully. Restarting Blender...")
                
                # Trigger restart using standalone function
                bpy.app.timers.register(restart_blender_after_update, first_interval=1.0)
                UPDATE_STATUS = "idle"  # Reset status since we're restarting
                
            else:
                raise Exception("Unexpected archive structure")
            
        except Exception as e:
            UPDATE_STATUS = "idle"
            self.report({'ERROR'}, f"Failed to install update: {str(e)}")
            return {'CANCELLED'}
        
        return {'FINISHED'}

class SUB_OP_restart_blender(Operator):
    """Restart Blender to complete the update"""
    bl_idname = "sub.restart_blender"
    bl_label = "Restart Blender"
    bl_description = "Restart Blender to complete the update process"
    
    def execute(self, context):
        # Get current blend file path
        current_file = bpy.data.filepath
        
        # Restart Blender with a more robust approach
        try:
            # Get Blender executable path
            blender_exe = bpy.app.binary_path
            
            # Build command
            if current_file:
                cmd = [blender_exe, current_file]
            else:
                cmd = [blender_exe]
            
            # Start new Blender instance
            if platform.system() == "Windows":
                # On Windows, use CREATE_NEW_PROCESS_GROUP to prevent inheriting signals
                subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                # On Unix-like systems
                subprocess.Popen(cmd, start_new_session=True)
            
            # Quit current instance
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
        check_for_newer_version()
        
        if UPDATE_AVAILABLE:
            commit_short = LATEST_COMMIT_SHA[:8] if LATEST_COMMIT_SHA else "unknown"
            self.report({'INFO'}, f"Update available: commit {commit_short}")
        else:
            self.report({'INFO'}, "No updates available")
        
        return {'FINISHED'}

# Register properties for the scene
def register_properties():
    bpy.types.Scene.sub_updater_download_path = StringProperty(
        name="Download Path",
        description="Path to downloaded update file",
        default=""
    )

def unregister_properties():
    if hasattr(bpy.types.Scene, 'sub_updater_download_path'):
        del bpy.types.Scene.sub_updater_download_path 