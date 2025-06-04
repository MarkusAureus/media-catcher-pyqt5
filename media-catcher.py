import sys
import subprocess
import os
import threading
import re
import json
from urllib.parse import urlparse
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *

# Get the directory where the script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# === Load Themes ===
themes_path = os.path.join(SCRIPT_DIR, "themes.json")
with open(themes_path, "r") as f:
    THEMES = json.load(f)

# === Global variables for process control ===
current_process = None
is_downloading = False
stop_requested = False
output_dir = os.path.expanduser("~/Downloads")

class DownloadThread(QThread):
    progress = pyqtSignal(float)
    status = pyqtSignal(str, str)
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
        global current_process, stop_requested
        
        all_urls = []
        download_info = []
        
        for url in self.urls:
            if is_playlist_url(url) and self.download_playlist:
                playlist_count = get_playlist_count(url)
                if playlist_count > 0:
                    all_urls.append(url)
                    download_info.append({"url": url, "count": playlist_count, "type": "full_playlist"})
                else:
                    all_urls.append(url)
                    download_info.append({"url": url, "count": 1, "type": "single"})
            elif is_playlist_url(url) and not self.download_playlist:
                self.status.emit("⚠️ Playlist detected - downloading first video only", "orange")
                all_urls.append(url)
                download_info.append({"url": url, "count": 1, "type": "single"})
            elif is_video_from_playlist(url) and self.download_playlist:
                video_index = get_video_index_from_url(url)
                total_count = get_playlist_count(url)
                remaining_count = max(1, total_count - video_index + 1)
                self.status.emit(f"📋 Downloading playlist from video #{video_index} to end", "cyan")
                all_urls.append(url)
                download_info.append({"url": url, "count": remaining_count, "type": "partial_playlist", "start_index": video_index})
            elif is_video_from_playlist(url) and not self.download_playlist:
                clean_url = url.split('&list=')[0] if '&list=' in url else url
                all_urls.append(clean_url)
                download_info.append({"url": clean_url, "count": 1, "type": "single"})
            else:
                all_urls.append(url)
                download_info.append({"url": url, "count": 1, "type": "single"})

        total_videos = sum(info["count"] for info in download_info)
        current_video = 0
        
        for index, (url, info) in enumerate(zip(all_urls, download_info), start=1):
            if stop_requested:
                break
                
            if info["type"] in ["full_playlist", "partial_playlist"]:
                self.status.emit(f"⬇️ Starting playlist download...", "white")
            else:
                current_video += 1
                self.status.emit(f"⬇️ Downloading ({current_video}/{total_videos})...", "white")

            output_path = os.path.join(self.output_dir, "%(title)s.%(ext)s")
            cmd = ["yt-dlp", url, "-o", output_path, "--newline"]

            if is_playlist_url(url) and not self.download_playlist:
                cmd.extend(["--playlist-items", "1"])
            elif is_video_from_playlist(url) and self.download_playlist:
                video_index = get_video_index_from_url(url)
                cmd.extend(["--playlist-start", str(video_index)])

            if self.mode == "Audio":
                audio_format = window.combo_audio_format.currentText()
                cmd.extend(["-x", "--audio-format", audio_format, "--force-overwrites"])
                if audio_format in ["mp3", "aac"]:
                    quality = window.combo_quality_audio.currentText()
                    quality_map = {"320K": "0", "192K": "2", "128K": "5", "64K": "9"}
                    cmd.extend(["--audio-quality", quality_map.get(quality, "2")])
            else:
                cmd.extend(["--merge-output-format", "mp4"])
                if is_youtube_url(url):
                    if self.download_subs:
                        cmd.extend(["-f", "bestvideo+bestaudio", "--write-auto-sub", 
                                  "--sub-lang", self.subtitle_lang, "--convert-subs", "srt", 
                                  "--sub-format", "srt"])
                    else:
                        quality_id = window.combo_quality_video.currentText()
                        if quality_id == "Best available":
                            cmd.extend(["-f", "bestvideo+bestaudio"])
                        else:
                            video_code = quality_id.split()[0]
                            cmd.extend(["-f", f"{video_code}+140"])
                else:
                    cmd.extend(["-f", "best"])

            try:
                self.progress.emit(0)
                current_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, 
                                                 stderr=subprocess.PIPE, text=True)
                current_playlist_video = 0
                
                for line in current_process.stdout:
                    if stop_requested:
                        break
                        
                    if info["type"] in ["full_playlist", "partial_playlist"]:
                        if 'has already been downloaded' in line or '[download] 100%' in line:
                            current_playlist_video += 1
                            overall_current = current_video + current_playlist_video
                            self.status.emit(f"⬇️ Completed video {overall_current}/{total_videos}", "white")
                    
                    match = re.search(r'\[download\]\s+(\d{1,3}\.?\d*)%', line)
                    if match:
                        try:
                            percent = float(match.group(1))
                            self.progress.emit(percent)
                        except ValueError:
                            pass
                            
                stdout, stderr = current_process.communicate()
                
                if stop_requested:
                    break
                    
                if current_process.returncode == 0:
                    if info["type"] in ["full_playlist", "partial_playlist"]:
                        current_video += info["count"]
                        self.status.emit(f"✅ Playlist completed ({current_video}/{total_videos})", "green")
                    else:
                        self.status.emit(f"✅ Done ({current_video}/{total_videos})", "green")
                else:
                    error_message = stderr.strip() or "Unknown error"
                    if info["type"] in ["full_playlist", "partial_playlist"]:
                        current_video += info["count"]
                    self.status.emit(f"❌ Error: {error_message[:100]}...", "red")
            except Exception as e:
                if info["type"] in ["full_playlist", "partial_playlist"]:
                    current_video += info["count"]
                self.status.emit(f"❌ Exception: {str(e)}", "red")
        
        self.finished.emit()

class MediaCatcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.download_thread = None
        self.init_ui()
        self.apply_theme("Blueberry")
        
    def init_ui(self):
        self.setWindowTitle("Media Catcher")
        self.setFixedSize(700, 700)
        
        # Set window icon - try multiple formats and locations
        icon_loaded = False
        
        # Try SVG first
        svg_paths = [
            os.path.join(SCRIPT_DIR, "media-catcher.svg"),
            "media-catcher.svg",
            "/usr/share/icons/hicolor/scalable/apps/media-catcher.svg",
            os.path.expanduser("~/.local/share/icons/media-catcher.svg")
        ]
        
        for svg_path in svg_paths:
            if os.path.exists(svg_path):
                print(f"Loading SVG icon from: {svg_path}")
                icon = QIcon(svg_path)
                if not icon.isNull():
                    self.setWindowIcon(icon)
                    icon_loaded = True
                    break
        
        # Try PNG if SVG failed
        if not icon_loaded:
            png_paths = [
                os.path.join(SCRIPT_DIR, "media-catcher.png"),
                "media-catcher.png",
                "/usr/share/pixmaps/media-catcher.png",
                os.path.expanduser("~/.local/share/icons/media-catcher.png")
            ]
            
            for png_path in png_paths:
                if os.path.exists(png_path):
                    print(f"Loading PNG icon from: {png_path}")
                    icon = QIcon(png_path)
                    if not icon.isNull():
                        self.setWindowIcon(icon)
                        icon_loaded = True
                        break
        
        if not icon_loaded:
            print("Warning: Could not load application icon")
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title
        title_label = QLabel("Media Catcher")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 20px; font-weight: bold; padding: 10px;")
        layout.addWidget(title_label)
        
        # URL Input
        self.entry_url = QTextEdit()
        self.entry_url.setMaximumHeight(100)
        self.entry_url.setPlaceholderText("🔗 Enter URL(s) or playlist link here...")
        layout.addWidget(self.entry_url)
        
        # Mode selection
        mode_layout = QHBoxLayout()
        mode_layout.addStretch()
        mode_label = QLabel("Mode:")
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Audio", "Video"])
        self.combo_mode.setFixedWidth(200)
        self.combo_mode.currentTextChanged.connect(self.toggle_quality_options)
        mode_layout.addWidget(mode_label)
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
        
        # Audio options
        self.audio_options_widget = QWidget()
        audio_layout = QVBoxLayout(self.audio_options_widget)
        
        format_layout = QHBoxLayout()
        format_layout.addStretch()
        format_label = QLabel("Audio Format:")
        self.combo_audio_format = QComboBox()
        self.combo_audio_format.addItems(["mp3", "wav", "aac"])
        self.combo_audio_format.setFixedWidth(200)
        self.combo_audio_format.currentTextChanged.connect(self.update_audio_quality_options)
        format_layout.addWidget(format_label)
        format_layout.addWidget(self.combo_audio_format)
        format_layout.addStretch()
        audio_layout.addLayout(format_layout)
        
        quality_layout = QHBoxLayout()
        quality_layout.addStretch()
        quality_label = QLabel("Audio Quality:")
        self.combo_quality_audio = QComboBox()
        self.combo_quality_audio.addItems(["320K", "192K", "128K", "64K"])
        self.combo_quality_audio.setCurrentText("192K")
        self.combo_quality_audio.setFixedWidth(200)
        quality_layout.addWidget(quality_label)
        quality_layout.addWidget(self.combo_quality_audio)
        quality_layout.addStretch()
        audio_layout.addLayout(quality_layout)
        
        layout.addWidget(self.audio_options_widget)
        
        # Video options
        self.video_options_widget = QWidget()
        video_layout = QVBoxLayout(self.video_options_widget)
        
        video_quality_layout = QHBoxLayout()
        video_quality_layout.addStretch()
        video_quality_label = QLabel("Video Quality:")
        self.combo_quality_video = QComboBox()
        self.combo_quality_video.addItems(["Best available", "137 (1080p)", "136 (720p)", 
                                          "135 (480p)", "134 (360p)", "133 (240p)"])
        self.combo_quality_video.setFixedWidth(200)
        video_quality_layout.addWidget(video_quality_label)
        video_quality_layout.addWidget(self.combo_quality_video)
        video_quality_layout.addStretch()
        video_layout.addLayout(video_quality_layout)
        
        subtitle_layout = QHBoxLayout()
        subtitle_layout.addStretch()
        self.checkbox_subtitles = QCheckBox("Download subtitles")
        self.checkbox_subtitles.stateChanged.connect(self.update_video_quality_state)
        subtitle_layout.addWidget(self.checkbox_subtitles)
        subtitle_layout.addStretch()
        video_layout.addLayout(subtitle_layout)
        
        sub_lang_layout = QHBoxLayout()
        sub_lang_layout.addStretch()
        sub_lang_label = QLabel("Subtitle Language:")
        self.combo_sub_lang = QComboBox()
        self.combo_sub_lang.addItems(["en (English)", "sk (Slovak)", "cs (Czech)", 
                                     "de (German)", "fr (French)", "es (Spanish)", 
                                     "ru (Russian)", "ja (Japanese)", "zh (Chinese)"])
        self.combo_sub_lang.setFixedWidth(200)
        sub_lang_layout.addWidget(sub_lang_label)
        sub_lang_layout.addWidget(self.combo_sub_lang)
        sub_lang_layout.addStretch()
        video_layout.addLayout(sub_lang_layout)
        
        layout.addWidget(self.video_options_widget)
        self.video_options_widget.hide()
        
        # Output folder
        folder_layout = QHBoxLayout()
        folder_layout.addStretch()
        self.folder_button = QPushButton("Select Output Folder")
        self.folder_button.setFixedWidth(200)
        self.folder_button.clicked.connect(self.choose_folder)
        folder_layout.addWidget(self.folder_button)
        folder_layout.addStretch()
        layout.addLayout(folder_layout)
        
        # Output label
        self.label_output = QLabel(f"Saving to: {output_dir}")
        self.label_output.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label_output)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.download_button = QPushButton("Download")
        self.download_button.setFixedWidth(120)
        self.download_button.clicked.connect(self.start_download)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setFixedWidth(120)
        self.stop_button.clicked.connect(self.stop_download)
        self.stop_button.setEnabled(False)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setFixedWidth(120)
        self.clear_button.clicked.connect(self.clear_and_reset)
        
        button_layout.addWidget(self.download_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        # Theme selection
        theme_layout = QHBoxLayout()
        theme_layout.addStretch()
        theme_label = QLabel("Theme:")
        self.combo_theme = QComboBox()
        self.combo_theme.addItems(list(THEMES.keys()))
        self.combo_theme.setCurrentText("Blueberry")
        self.combo_theme.setFixedWidth(200)
        self.combo_theme.currentTextChanged.connect(self.on_theme_change)
        theme_layout.addWidget(theme_label)
        theme_layout.addWidget(self.combo_theme)
        theme_layout.addStretch()
        layout.addLayout(theme_layout)
        
        # Progress bar
        progress_container = QHBoxLayout()
        progress_container.addStretch()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedWidth(400)
        progress_container.addWidget(self.progress_bar)
        progress_container.addStretch()
        layout.addLayout(progress_container)
        
        # Progress label
        self.progress_label = QLabel("Progress: 0%")
        self.progress_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.progress_label)
        
        # Status label
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)
        
        layout.addStretch()
        
    def apply_theme(self, theme_name):
        theme = THEMES.get(theme_name, THEMES["Blueberry"])
        
        # Base stylesheet
        if theme["appearance"] == "Dark":
            self.setStyleSheet(f"""
                QMainWindow {{
                    background-color: {theme["bg_color"]};
                }}
                QWidget {{
                    background-color: {theme["bg_color"]};
                    color: white;
                }}
                QTextEdit {{
                    background-color: #2b2b2b;
                    border: 1px solid #555;
                    border-radius: 5px;
                    padding: 5px;
                    color: white;
                }}
                QComboBox {{
                    background-color: #2b2b2b;
                    border: 1px solid #555;
                    border-radius: 5px;
                    padding: 5px;
                    color: white;
                }}
                QComboBox::drop-down {{
                    border: none;
                }}
                QComboBox::down-arrow {{
                    image: none;
                    border-left: 5px solid transparent;
                    border-right: 5px solid transparent;
                    border-top: 5px solid white;
                    margin-right: 5px;
                }}
                QCheckBox {{
                    color: white;
                }}
                QCheckBox::indicator {{
                    width: 18px;
                    height: 18px;
                }}
                QCheckBox::indicator:unchecked {{
                    background-color: #2b2b2b;
                    border: 1px solid #555;
                    border-radius: 3px;
                }}
                QCheckBox::indicator:checked {{
                    background-color: {theme["button_color"]};
                    border: 1px solid {theme["button_color"]};
                    border-radius: 3px;
                }}
                QProgressBar {{
                    background-color: #2b2b2b;
                    border: 1px solid #555;
                    border-radius: 5px;
                    text-align: center;
                }}
                QProgressBar::chunk {{
                    background-color: {theme["button_color"]};
                    border-radius: 5px;
                }}
                QPushButton {{
                    background-color: {theme["button_color"]};
                    color: white;
                    border: none;
                    border-radius: 5px;
                    padding: 8px 16px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {theme["hover_color"]};
                }}
                QPushButton:pressed {{
                    background-color: {theme["hover_color"]};
                }}
                QPushButton:disabled {{
                    background-color: #555;
                    color: #999;
                }}
                QLabel {{
                    color: white;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QMainWindow {{
                    background-color: {theme["bg_color"]};
                }}
                QWidget {{
                    background-color: {theme["bg_color"]};
                    color: black;
                }}
                QTextEdit {{
                    background-color: white;
                    border: 1px solid #ddd;
                    border-radius: 5px;
                    padding: 5px;
                    color: black;
                }}
                QComboBox {{
                    background-color: white;
                    border: 1px solid #ddd;
                    border-radius: 5px;
                    padding: 5px;
                    color: black;
                }}
                QComboBox::drop-down {{
                    border: none;
                }}
                QComboBox::down-arrow {{
                    image: none;
                    border-left: 5px solid transparent;
                    border-right: 5px solid transparent;
                    border-top: 5px solid black;
                    margin-right: 5px;
                }}
                QCheckBox {{
                    color: black;
                }}
                QCheckBox::indicator {{
                    width: 18px;
                    height: 18px;
                }}
                QCheckBox::indicator:unchecked {{
                    background-color: white;
                    border: 1px solid #ddd;
                    border-radius: 3px;
                }}
                QCheckBox::indicator:checked {{
                    background-color: {theme["button_color"]};
                    border: 1px solid {theme["button_color"]};
                    border-radius: 3px;
                }}
                QProgressBar {{
                    background-color: #f0f0f0;
                    border: 1px solid #ddd;
                    border-radius: 5px;
                    text-align: center;
                }}
                QProgressBar::chunk {{
                    background-color: {theme["button_color"]};
                    border-radius: 5px;
                }}
                QPushButton {{
                    background-color: {theme["button_color"]};
                    color: white;
                    border: none;
                    border-radius: 5px;
                    padding: 8px 16px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {theme["hover_color"]};
                }}
                QPushButton:pressed {{
                    background-color: {theme["hover_color"]};
                }}
                QPushButton:disabled {{
                    background-color: #ddd;
                    color: #999;
                }}
                QLabel {{
                    color: black;
                }}
            """)
        
        # Special styling for buttons
        self.stop_button.setStyleSheet("""
            QPushButton {
                background-color: #dc3545;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
            QPushButton:disabled {
                background-color: #555;
                color: #999;
            }
        """)
        
        self.clear_button.setStyleSheet("""
            QPushButton {
                background-color: #444;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #666;
            }
        """)
    
    def on_theme_change(self, theme_name):
        self.apply_theme(theme_name)
    
    def toggle_quality_options(self, choice):
        if choice == "Audio":
            self.audio_options_widget.show()
            self.video_options_widget.hide()
        else:
            self.audio_options_widget.hide()
            self.video_options_widget.show()
    
    def update_audio_quality_options(self, format_choice):
        if format_choice in ["mp3", "aac"]:
            self.combo_quality_audio.clear()
            self.combo_quality_audio.addItems(["320K", "192K", "128K", "64K"])
            self.combo_quality_audio.setCurrentText("192K")
            self.combo_quality_audio.setEnabled(True)
        elif format_choice == "wav":
            self.combo_quality_audio.clear()
            self.combo_quality_audio.addItems(["N/A (lossless)"])
            self.combo_quality_audio.setEnabled(False)
    
    def update_video_quality_state(self):
        if self.checkbox_subtitles.isChecked():
            self.combo_quality_video.setCurrentText("Best available")
            self.combo_quality_video.setEnabled(False)
        else:
            self.combo_quality_video.setEnabled(True)
    
    def choose_folder(self):
        global output_dir
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", output_dir)
        if folder:
            output_dir = folder
            self.label_output.setText(f"Saving to: {output_dir}")
    
    def clear_and_reset(self):
        self.entry_url.clear()
        self.status_label.setText("")
        self.progress_bar.setValue(0)
        self.progress_label.setText("Progress: 0%")
    
    def start_download(self):
        global is_downloading, stop_requested
        
        urls_text = self.entry_url.toPlainText().strip()
        
        if not urls_text:
            self.status_label.setText("❌ Please enter a valid URL")
            self.status_label.setStyleSheet("color: red;")
            return
        
        user_urls = [line.strip() for line in urls_text.splitlines() if line.strip()]
        
        if not user_urls:
            self.status_label.setText("❌ Please enter a valid URL")
            self.status_label.setStyleSheet("color: red;")
            return
        
        # Update button states
        is_downloading = True
        stop_requested = False
        self.download_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        
        # Get settings
        mode = self.combo_mode.currentText()
        download_playlist = self.checkbox_playlist.isChecked()
        download_subs = self.checkbox_subtitles.isChecked()
        subtitle_lang = self.combo_sub_lang.currentText().split()[0]
        
        # Create and start download thread
        self.download_thread = DownloadThread(
            user_urls, mode, download_playlist, 
            download_subs, subtitle_lang, output_dir
        )
        self.download_thread.progress.connect(self.update_progress)
        self.download_thread.status.connect(self.update_status)
        self.download_thread.finished.connect(self.download_finished)
        self.download_thread.start()
    
    def stop_download(self):
        global current_process, stop_requested
        stop_requested = True
        
        if current_process and current_process.poll() is None:
            try:
                current_process.terminate()
                QTimer.singleShot(500, lambda: current_process.kill() if current_process.poll() is None else None)
                
                self.status_label.setText("⏹️ Download stopped by user")
                self.status_label.setStyleSheet("color: orange;")
                self.progress_bar.setValue(0)
                self.progress_label.setText("Progress: 0%")
            except Exception as e:
                print(f"Error stopping process: {e}")
        
        self.download_button.setEnabled(True)
        self.stop_button.setEnabled(False)
    
    def update_progress(self, percent):
        self.progress_bar.setValue(int(percent))
        self.progress_label.setText(f"Progress: {percent:.1f}%")
    
    def update_status(self, message, color):
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {color};")
    
    def download_finished(self):
        global is_downloading
        is_downloading = False
        self.download_button.setEnabled(True)
        self.stop_button.setEnabled(False)

# === Helper Functions ===
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
    try:
        result = subprocess.run([
            "yt-dlp", "--flat-playlist", "--print", "%(title)s", playlist_url
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        videos = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        return len(videos)
    except Exception as e:
        print(f"Playlist count error: {e}")
        return 0

# === Main ===
if __name__ == "__main__":
    # Create QApplication first
    app = QApplication(sys.argv)
    
    # Set application metadata
    app.setApplicationName("MediaCatcher")
    app.setOrganizationName("MediaCatcher")
    app.setApplicationDisplayName("Media Catcher")
    app.setDesktopFileName("media-catcher")
    
    # Set application icon with multiple fallbacks
    icon_loaded = False
    
    # Try SVG icons
    svg_paths = [
        os.path.join(SCRIPT_DIR, "media-catcher.svg"),
        "media-catcher.svg",
        "/usr/share/icons/hicolor/scalable/apps/media-catcher.svg",
        os.path.expanduser("~/.local/share/icons/media-catcher.svg")
    ]
    
    for svg_path in svg_paths:
        if os.path.exists(svg_path):
            print(f"Setting app SVG icon from: {svg_path}")
            icon = QIcon(svg_path)
            if not icon.isNull():
                app.setWindowIcon(icon)
                icon_loaded = True
                break
    
    # Try PNG if SVG failed
    if not icon_loaded:
        png_paths = [
            os.path.join(SCRIPT_DIR, "media-catcher.png"),
            "media-catcher.png",
            "/usr/share/pixmaps/media-catcher.png",
            os.path.expanduser("~/.local/share/icons/media-catcher.png")
        ]
        
        for png_path in png_paths:
            if os.path.exists(png_path):
                print(f"Setting app PNG icon from: {png_path}")
                icon = QIcon(png_path)
                if not icon.isNull():
                    app.setWindowIcon(icon)
                    icon_loaded = True
                    break
    
    if not icon_loaded:
        print("Warning: Could not set application icon")
    
    # Set style
    app.setStyle("Fusion")
    
    # Create and show window
    window = MediaCatcher()
    window.show()
    
    sys.exit(app.exec_())