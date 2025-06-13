#!/usr/bin/env python3

# -----------------------------------------------------------------------------
# -- MEDIA CATCHER - A PYQT5-BASED YT-DLP FRONTEND                           --
# -- Author: Markus Aureus                                                   --
# -- Date: [Date of Last Edit]                                               --
# -- Description: A cross-platform desktop application for downloading media --
# --              using yt-dlp. Built with PyQt5 for a native look and feel, --
# --              featuring theming, and robust playlist handling.           --
# -----------------------------------------------------------------------------

# --- Standard Library Imports ---
import sys
import os
import threading
import re
import json
import subprocess
from urllib.parse import urlparse

# --- Third-party Library Imports ---
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *

# --- Global Configuration ---
# Get the directory where the script is located for relative path access.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def load_themes():
    """
    Loads theme definitions from a JSON file.
    It checks multiple standard locations, making it compatible with
    local execution and Flatpak installations.
    If no themes.json is found, it returns a hardcoded dictionary of fallback themes.
    """
    possible_paths = [
        os.path.join(SCRIPT_DIR, "themes.json"),
        "/app/share/media-catcher/themes.json",  # Standard Flatpak data path
        os.path.expanduser("~/.var/app/io.github.MarkusAureus.MediaCatcher/data/themes.json"), # User-specific Flatpak data
        "themes.json" # Fallback for current working directory
    ]
    
    for path in possible_paths:
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    return json.load(f)
        except Exception as e:
            print(f"Could not load themes from {path}: {e}")
    
    # Fallback themes if no file is found
    return {
        "Blueberry": {"appearance": "Dark", "bg_color": "#1a1a2e", "button_color": "#A066D7", "hover_color": "#8847C0"},
        "Light": {"appearance": "Light", "bg_color": "#f5f5f5", "button_color": "#3B82F6", "hover_color": "#2563EB"},
        "YT Theme": {"appearance": "Dark", "bg_color": "#0f0f0f", "button_color": "#FF0000", "hover_color": "#CC0000"},
        "Matrix": {"appearance": "Dark", "bg_color": "#000000", "button_color": "#00FF00", "hover_color": "#00CC00"},
        "Ocean": {"appearance": "Dark", "bg_color": "#0d1117", "button_color": "#58a6ff", "hover_color": "#1f6feb"},
        "Sunset": {"appearance": "Dark", "bg_color": "#1a1625", "button_color": "#ff6b6b", "hover_color": "#ee5a6f"}
    }

# Load themes at startup.
THEMES = load_themes()

# --- Global variables for process and state management ---
current_process = None
stop_requested = False
output_dir = os.path.expanduser("~/Downloads") # Default output directory

class DownloadThread(QThread):
    """
    Handles the download process in a separate thread to prevent the GUI from freezing.
    It emits signals to update the main window's progress bar and status labels.
    """
    # Signals to communicate with the main UI thread
    progress = pyqtSignal(float)
    status = pyqtSignal(str, str) # Message and color
    finished = pyqtSignal()
    
    def __init__(self, urls, mode, download_playlist, download_subs, subtitle_lang, output_dir):
        super().__init__()
        self.urls = urls
        self.mode = mode
        self.download_playlist = download_playlist
        self.download_subs = download_subs
        self.subtitle_lang = subtitle_lang
        self.output_dir = output_dir
        
    def run(self):
        """
        The core worker method. This constructs and executes the yt-dlp command.
        """
        global current_process, stop_requested
        
        # --- Pre-computation of URLs and video counts ---
        # This section analyzes the input URLs to provide an accurate total video count.
        all_urls = []
        download_info = [] # Stores metadata for each URL (count, type, etc.)
        
        for url in self.urls:
            if is_playlist_url(url) and self.download_playlist:
                count = get_playlist_count(url)
                all_urls.append(url)
                download_info.append({"url": url, "count": count, "type": "full_playlist"})
            elif is_playlist_url(url) and not self.download_playlist:
                self.status.emit("⚠️ Playlist detected - downloading first video only", "orange")
                all_urls.append(url)
                download_info.append({"url": url, "count": 1, "type": "single"})
            elif is_video_from_playlist(url) and self.download_playlist:
                start_index = get_video_index_from_url(url)
                total_count = get_playlist_count(url)
                remaining = max(1, total_count - start_index + 1)
                self.status.emit(f"📋 Downloading playlist from video #{start_index}", "cyan")
                all_urls.append(url)
                download_info.append({"url": url, "count": remaining, "type": "partial_playlist"})
            else: # Standard video URL or single video from a playlist
                clean_url = url.split('&list=')[0] if '&list=' in url else url
                all_urls.append(clean_url)
                download_info.append({"url": clean_url, "count": 1, "type": "single"})

        total_videos = sum(info["count"] for info in download_info)
        current_video = 0
        
        # --- Main Download Loop ---
        for url, info in zip(all_urls, download_info):
            if stop_requested: break
            
            # --- Construct yt-dlp command ---
            output_path = os.path.join(self.output_dir, "%(title)s.%(ext)s")
            cmd = ["yt-dlp", url, "-o", output_path, "--newline", "--ignore-errors"]

            # Add mode-specific and playlist-specific arguments
            if info["type"] == "single" and is_playlist_url(url):
                cmd.extend(["--playlist-items", "1"])
            elif info["type"] == "partial_playlist":
                cmd.extend(["--playlist-start", str(get_video_index_from_url(url))])

            if self.mode == "Audio":
                audio_format = window.combo_audio_format.currentText()
                cmd.extend(["-x", "--audio-format", audio_format, "--force-overwrites"])
                if audio_format in ["mp3", "aac"]:
                    quality = window.combo_quality_audio.currentText()
                    quality_map = {"320K": "0", "192K": "2", "128K": "5", "64K": "9"}
                    cmd.extend(["--audio-quality", quality_map.get(quality, "2")])
            else: # Video Mode
                cmd.extend(["--merge-output-format", "mp4"])
                if is_youtube_url(url): # YouTube requires specific format codes
                    if self.download_subs:
                        cmd.extend(["-f", "bestvideo+bestaudio", "--write-auto-sub", 
                                    "--sub-lang", self.subtitle_lang, "--convert-subs", "srt"])
                    else:
                        quality_id = window.combo_quality_video.currentText()
                        format_code = f"{quality_id.split()[0]}+140" if quality_id != "Best available" else "bestvideo+bestaudio"
                        cmd.extend(["-f", format_code])
                else: # For other sites, 'best' is more reliable
                    cmd.extend(["-f", "best"])

            # --- Execute and Monitor Process ---
            try:
                self.progress.emit(0)
                if info["type"] != "full_playlist":
                     current_video += 1
                self.status.emit(f"⬇️ Downloading ({current_video}/{total_videos})...", "white")

                current_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, 
                                                   stderr=subprocess.PIPE, text=True,
                                                   encoding='utf-8', errors='replace')
                
                # Parse stdout for progress updates
                for line in current_process.stdout:
                    if stop_requested: break
                    
                    match = re.search(r'\[download\]\s+(\d{1,3}\.?\d*)%', line)
                    if match:
                        self.progress.emit(float(match.group(1)))
                    # Update count for completed playlist items
                    if info["type"] == "full_playlist" and ('[download] 100%' in line or 'has already been downloaded' in line):
                        current_video +=1
                        self.status.emit(f"⬇️ Completed video {current_video}/{total_videos}", "white")

                stdout, stderr = current_process.communicate() # Get final output
                
                if stop_requested: break
                
                if current_process.returncode == 0:
                     self.status.emit(f"✅ Done ({current_video}/{total_videos})", "green")
                else:
                    self.status.emit(f"❌ Error: {stderr.strip()[:100]}...", "red")
            except Exception as e:
                self.status.emit(f"❌ Exception: {str(e)}", "red")
        
        # Signal that the entire process has finished
        self.finished.emit()

class MediaCatcher(QMainWindow):
    """
    The main window of the application.
    It sets up the UI, connects signals and slots, and manages the application state.
    """
    def __init__(self):
        super().__init__()
        self.download_thread = None
        self.init_ui()
        self.apply_theme("Blueberry") # Set default theme
        
    def init_ui(self):
        """Initializes all UI components and layouts."""
        self.setWindowTitle("Media Catcher")
        self.setFixedSize(700, 700) # Prevent resizing to maintain layout
        
        # --- Application Icon Loading ---
        # This extensive check ensures the icon is found in various environments, especially Flatpak.
        self._load_app_icon()
        
        # --- Central Widget and Main Layout ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # --- UI Element Creation ---
        title_label = QLabel("Media Catcher")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 20px; font-weight: bold; padding: 10px;")
        layout.addWidget(title_label)
        
        self.entry_url = QTextEdit()
        self.entry_url.setMaximumHeight(100)
        self.entry_url.setPlaceholderText("🔗 Enter URL(s), one per line...")
        layout.addWidget(self.entry_url)
        
        # Mode, Playlist, and options widgets are created and added here...
        # (Self-explanatory UI setup code omitted for brevity in this comment block)
        self._create_ui_elements(layout)

    def _create_ui_elements(self, layout):
        """Helper function to create and lay out all the UI widgets."""
        # Mode selection
        mode_layout = QHBoxLayout()
        mode_layout.addStretch()
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Audio", "Video"])
        self.combo_mode.setFixedWidth(200)
        self.combo_mode.currentTextChanged.connect(self.toggle_quality_options)
        mode_layout.addWidget(QLabel("Mode:"))
        mode_layout.addWidget(self.combo_mode)
        mode_layout.addStretch()
        layout.addLayout(mode_layout)
        
        # Playlist checkbox
        playlist_layout = QHBoxLayout()
        playlist_layout.addStretch()
        self.checkbox_playlist = QCheckBox("Download entire playlist")
        playlist_layout.addWidget(self.checkbox_playlist)
        playlist_layout.addStretch()
        layout.addLayout(playlist_layout)

        # --- Dynamic Options ---
        # Audio Options
        self.audio_options_widget = QWidget()
        audio_layout = QVBoxLayout(self.audio_options_widget)
        self.combo_audio_format = self._create_combo_box(["mp3", "wav", "aac"], "Audio Format:", audio_layout)
        self.combo_audio_format.currentTextChanged.connect(self.update_audio_quality_options)
        self.combo_quality_audio = self._create_combo_box(["320K", "192K", "128K", "64K"], "Audio Quality:", audio_layout, "192K")
        layout.addWidget(self.audio_options_widget)
        
        # Video Options
        self.video_options_widget = QWidget()
        video_layout = QVBoxLayout(self.video_options_widget)
        self.combo_quality_video = self._create_combo_box(["Best available", "137 (1080p)", "136 (720p)", "135 (480p)", "134 (360p)"], "Video Quality:", video_layout)
        self.checkbox_subtitles = QCheckBox("Download subtitles")
        self.checkbox_subtitles.stateChanged.connect(self.update_video_quality_state)
        video_layout.addWidget(self.checkbox_subtitles, alignment=Qt.AlignCenter)
        self.combo_sub_lang = self._create_combo_box(["en (English)", "sk (Slovak)", "cs (Czech)", "de (German)"], "Subtitle Language:", video_layout)
        layout.addWidget(self.video_options_widget)
        self.video_options_widget.hide()

        # --- Folder and Action Buttons ---
        self.folder_button = QPushButton("Select Output Folder")
        self.folder_button.clicked.connect(self.choose_folder)
        self.label_output = QLabel(f"Saving to: {output_dir}", alignment=Qt.AlignCenter)
        layout.addWidget(self.folder_button)
        layout.addWidget(self.label_output)

        button_layout = QHBoxLayout()
        self.download_button = QPushButton("Download")
        self.download_button.clicked.connect(self.start_download)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_download)
        self.stop_button.setEnabled(False)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_and_reset)
        button_layout.addStretch()
        button_layout.addWidget(self.download_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        # --- Theme, Progress Bar, and Status ---
        self.combo_theme = self._create_combo_box(list(THEMES.keys()), "Theme:", layout, "Blueberry", True)
        self.combo_theme.currentTextChanged.connect(self.on_theme_change)

        self.progress_bar = QProgressBar(textVisible=False)
        self.progress_label = QLabel("Progress: 0%", alignment=Qt.AlignCenter)
        self.status_label = QLabel("", alignment=Qt.AlignCenter)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.status_label)
        layout.addStretch()

    def _create_combo_box(self, items, label_text, parent_layout, default_text=None, centered=False):
        """Helper to create a labeled QComboBox and add it to a layout."""
        layout = QHBoxLayout()
        combo = QComboBox()
        combo.addItems(items)
        combo.setFixedWidth(200)
        if default_text: combo.setCurrentText(default_text)
        
        if centered: layout.addStretch()
        layout.addWidget(QLabel(label_text))
        layout.addWidget(combo)
        if centered: layout.addStretch()

        parent_layout.addLayout(layout)
        return combo

    def _load_app_icon(self):
        """Loads the application icon, checking multiple paths for compatibility."""
        icon_paths = [
            "/app/share/icons/hicolor/scalable/apps/io.github.MarkusAureus.MediaCatcher.svg",
            os.path.join(SCRIPT_DIR, "io.github.MarkusAureus.MediaCatcher.svg"),
            "/app/share/icons/hicolor/256x256/apps/io.github.MarkusAureus.MediaCatcher.png",
             os.path.join(SCRIPT_DIR, "io.github.MarkusAureus.MediaCatcher.png")
        ]
        for path in icon_paths:
            if os.path.exists(path):
                icon = QIcon(path)
                if not icon.isNull():
                    self.setWindowIcon(icon)
                    return
        print("Warning: Could not load application icon.")

    def apply_theme(self, theme_name):
        """Applies a theme to the application using QSS stylesheets."""
        theme = THEMES.get(theme_name, THEMES["Blueberry"])
        is_dark = theme["appearance"] == "Dark"
        
        # Stylesheet templates for dark and light modes
        # QSS allows for CSS-like styling of Qt widgets.
        base_style = """
            QMainWindow, QWidget {{ background-color: {bg_color}; color: {text_color}; }}
            QTextEdit {{ background-color: {input_bg}; border: 1px solid #555; border-radius: 5px; padding: 5px; color: {text_color}; }}
            QComboBox {{ background-color: {input_bg}; border: 1px solid #555; border-radius: 5px; padding: 5px; color: {text_color}; }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox::down-arrow {{ border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 5px solid {text_color}; margin-right: 5px; }}
            QCheckBox::indicator:unchecked {{ background-color: {input_bg}; border: 1px solid #555; border-radius: 3px; }}
            QCheckBox::indicator:checked {{ background-color: {button_color}; border: 1px solid {button_color}; border-radius: 3px; }}
            QProgressBar {{ background-color: {input_bg}; border: 1px solid #555; border-radius: 5px; text-align: center; }}
            QProgressBar::chunk {{ background-color: {button_color}; border-radius: 5px; }}
            QPushButton {{ background-color: {button_color}; color: white; border: none; border-radius: 5px; padding: 8px 16px; font-weight: bold; }}
            QPushButton:hover {{ background-color: {hover_color}; }}
            QPushButton:disabled {{ background-color: #555; color: #999; }}
        """
        self.setStyleSheet(base_style.format(
            bg_color=theme["bg_color"],
            text_color="white" if is_dark else "black",
            input_bg="#2b2b2b" if is_dark else "white",
            button_color=theme["button_color"],
            hover_color=theme["hover_color"]
        ))
        # Specific overrides for certain buttons
        self.stop_button.setStyleSheet("QPushButton { background-color: #dc3545; } QPushButton:hover { background-color: #c82333; }")
        self.clear_button.setStyleSheet("QPushButton { background-color: #444; } QPushButton:hover { background-color: #666; }")

    # --- SLOTS (Event Handlers) ---

    def on_theme_change(self, theme_name):
        """Called when the user selects a new theme from the dropdown."""
        self.apply_theme(theme_name)
    
    def toggle_quality_options(self, choice):
        """Shows/hides audio or video options based on the selected mode."""
        self.audio_options_widget.setVisible(choice == "Audio")
        self.video_options_widget.setVisible(choice == "Video")
        
    def update_audio_quality_options(self, format_choice):
        """Disables the quality dropdown for lossless formats like WAV."""
        is_lossless = format_choice == "wav"
        self.combo_quality_audio.clear()
        if is_lossless:
            self.combo_quality_audio.addItems(["N/A (lossless)"])
        else:
            self.combo_quality_audio.addItems(["320K", "192K", "128K", "64K"])
            self.combo_quality_audio.setCurrentText("192K")
        self.combo_quality_audio.setEnabled(not is_lossless)
    
    def update_video_quality_state(self):
        """Disables video quality selection when subtitles are checked (requires best streams)."""
        self.combo_quality_video.setEnabled(not self.checkbox_subtitles.isChecked())
        if self.checkbox_subtitles.isChecked():
            self.combo_quality_video.setCurrentText("Best available")
    
    def choose_folder(self):
        """Opens a dialog to select the download destination."""
        global output_dir
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", output_dir)
        if folder:
            output_dir = folder
            self.label_output.setText(f"Saving to: {output_dir}")
            
    def clear_and_reset(self):
        """Resets input fields and status labels."""
        self.entry_url.clear()
        self.status_label.setText("")
        self.progress_bar.setValue(0)
        self.progress_label.setText("Progress: 0%")
        
    def start_download(self):
        """Validates input and starts the DownloadThread."""
        global is_downloading, stop_requested
        urls_text = self.entry_url.toPlainText().strip()
        if not urls_text:
            self.update_status("❌ Please enter a valid URL", "red")
            return
        
        is_downloading, stop_requested = True, False
        self.download_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        
        self.download_thread = DownloadThread(
            urls=[line.strip() for line in urls_text.splitlines() if line.strip()],
            mode=self.combo_mode.currentText(),
            download_playlist=self.checkbox_playlist.isChecked(),
            download_subs=self.checkbox_subtitles.isChecked(),
            subtitle_lang=self.combo_sub_lang.currentText().split()[0],
            output_dir=output_dir
        )
        self.download_thread.progress.connect(self.update_progress)
        self.download_thread.status.connect(self.update_status)
        self.download_thread.finished.connect(self.download_finished)
        self.download_thread.start()
        
    def stop_download(self):
        """Requests the download to stop and terminates the process."""
        global stop_requested
        stop_requested = True
        if current_process:
            current_process.terminate() # Send SIGTERM
        self.update_status("⏹️ Download stopped by user", "orange")
        self.download_finished() # Reset UI state immediately
        
    def update_progress(self, percent):
        """Updates the progress bar and label."""
        self.progress_bar.setValue(int(percent))
        self.progress_label.setText(f"Progress: {percent:.1f}%")
        
    def update_status(self, message, color):
        """Updates the main status label with a given message and color."""
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {color};")
        
    def download_finished(self):
        """Resets the UI state after a download completes or is stopped."""
        global is_downloading, stop_requested
        is_downloading, stop_requested = False, False
        self.download_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.progress_bar.setValue(0)

# === Helper Functions for URL Analysis ===

def is_youtube_url(url):
    hostname = urlparse(url).hostname or ""
    return any(domain in hostname for domain in ["youtube.com", "youtu.be"])

def is_playlist_url(url):
    return "playlist?list=" in url and "watch?v=" not in url

def is_video_from_playlist(url):
    return "watch?v=" in url and "&list=" in url

def get_video_index_from_url(url):
    match = re.search(r'[&?]index=(\d+)', url)
    return int(match.group(1)) if match else 1

def get_playlist_count(playlist_url):
    """Runs a quick yt-dlp command to get the number of items in a playlist."""
    try:
        # --flat-playlist is very fast as it doesn't fetch metadata for each video.
        result = subprocess.run(["yt-dlp", "--flat-playlist", "-J", playlist_url], 
                                capture_output=True, text=True, check=True)
        playlist_data = json.loads(result.stdout)
        return len(playlist_data.get("entries", []))
    except (subprocess.CalledProcessError, json.JSONDecodeError, Exception) as e:
        print(f"Could not get playlist count for {playlist_url}: {e}")
        return 0 # Return 0 indicates an error or an empty/invalid playlist

# --- Application Entry Point ---
if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Set metadata for better integration with desktop environments (e.g., Wayland, Flatpak)
    app.setApplicationName("MediaCatcher")
    app.setOrganizationName("MarkusAureus")
    app.setApplicationDisplayName("Media Catcher")
    app.setDesktopFileName("io.github.MarkusAureus.MediaCatcher")
    
    # Set application style for consistency across platforms
    app.setStyle("Fusion")
    
    window = MediaCatcher()
    window.show()
    
    sys.exit(app.exec_())
