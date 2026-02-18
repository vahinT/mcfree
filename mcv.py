import sys
import os
import json
import uuid
import shutil
import zipfile
import urllib.request
import platform
import subprocess
import base64  # Added for the embedded skin
import minecraft_launcher_lib

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QComboBox, QProgressBar, QTextEdit, QFrame, 
                             QGraphicsDropShadowEffect, QMessageBox, QFileDialog, QInputDialog)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPalette, QBrush, QLinearGradient

# === CONFIG & PATHS ===
CONFIG_FILE = "config.json"
# Using a dedicated folder for the launcher data
MC_DIR = os.path.join(os.environ["APPDATA"], "MCV_LauncherData")
JAVA_RUNTIME_DIR = os.path.join(MC_DIR, "runtime")
SKIN_PACK_DIR = os.path.join(MC_DIR, "resourcepacks", "MCV_SkinPack")

# We keep this as a marker for the "Default" state in the UI/Config
DEFAULT_SKIN_MARKER = "DefaultEmbedded"
# The Base64 encoded skin image (Minimal Steve)
DEFAULT_SKIN_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAEAAAABABAMAAABYR2ztAAAAJFBMVEUAAAD///+zel4Ar693QjVSPYdqQDBKSkpBNZs6MYkzJBFCHQpIIVQmAAAAAXRSTlMAQObYZgAAALJJREFUeNrt1LENAiEUxnFmcAM/owWlNLeDnSO4BAPYwAbSUlLb3XI+zpcQOaAgMRR3/wRC8cvreCIWKFCi0FZAyOoDHpBNAKoBvDzezwcv6wC4PIAq8ADeN5AoA8TmGUshQKxzTmtBKcpR3zewBsB/gOGUiuC3HFxPTcD1APvKAbg0YTDQhtJa1wFnrTFPNdFRpgwMNS1XAnt7XHNPAGNA/j+GAN4T4IoTxoK0J+og1gM+G6AgbqeLKhgAAAAASUVORK5CYII="

os.makedirs(MC_DIR, exist_ok=True)
os.makedirs(JAVA_RUNTIME_DIR, exist_ok=True)

# === WORKER THREAD (Backend Logic) ===
class LauncherWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal()
    error_signal = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config

    def log(self, message):
        self.log_signal.emit(message)

    def update_progress(self, current, total, *args):
        if total > 0:
            percentage = int((current / total) * 100)
            self.progress_signal.emit(percentage)

    def download_with_callback(self, url, path, description):
        self.log(f"⬇️ {description}...")
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                total_size = int(response.info().get('Content-Length', 0))
                downloaded = 0
                block_size = 8192
                with open(path, 'wb') as file:
                    while True:
                        chunk = response.read(block_size)
                        if not chunk: break
                        file.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            self.progress_signal.emit(int((downloaded / total_size) * 100))
            self.log(f"✅ {description} Done.")
        except Exception as e:
            raise e

    def run(self):
        try:
            username = self.config['username']
            base_version_id = self.config['version']
            ram_gb = self.config['ram_gb']
            loader_type = self.config['loader']  # Vanilla, Fabric, or Forge

            # 1. Resolve Base Version
            if base_version_id == "latest":
                self.log("🔍 Checking for latest version...")
                base_version_id = minecraft_launcher_lib.utils.get_latest_version()["release"]
                self.log(f"🔥 Latest version is {base_version_id}")

            # 2. Check Java
            self.log("☕ Checking Java Runtime...")
            java_path = None
            for root, dirs, files in os.walk(JAVA_RUNTIME_DIR):
                if "java.exe" in files:
                    java_path = os.path.join(root, "java.exe")
                    break
            
            if not java_path:
                self.log("⚠️ Java not found. Downloading Portable Java 21...")
                zip_path = os.path.join(MC_DIR, "java21.zip")
                # Using Java 21 (Good for 1.20.5+). Older MC versions might need Java 8 or 17.
                java_url = "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.4%2B7/OpenJDK21U-jdk_x64_windows_hotspot_21.0.4_7.zip"
                self.download_with_callback(java_url, zip_path, "Java Runtime")
                
                self.log("📦 Extracting Java...")
                with zipfile.ZipFile(zip_path, 'r') as z:
                    z.extractall(JAVA_RUNTIME_DIR)
                os.remove(zip_path)
                
                # Rescan for java
                for root, dirs, files in os.walk(JAVA_RUNTIME_DIR):
                    if "java.exe" in files:
                        java_path = os.path.join(root, "java.exe")
                        break

            if not java_path:
                raise Exception("Failed to install Java.")

            # 3. Base Game Install
            # Even for Forge/Fabric, we usually need the base vanilla jar assets
            installed = [v['id'] for v in minecraft_launcher_lib.utils.get_installed_versions(MC_DIR)]
            if base_version_id not in installed:
                self.log(f"📥 Installing Vanilla {base_version_id} (Base)...")
                minecraft_launcher_lib.install.install_minecraft_version(
                    base_version_id, MC_DIR,
                    callback={"setStatus": lambda x: None, "setProgress": self.update_progress}
                )

            # 4. Mod Loader Logic
            launch_id = base_version_id # Default to vanilla

            if loader_type == "Fabric":
                self.log(f"🧶 Checking Fabric for {base_version_id}...")

                versions = minecraft_launcher_lib.utils.get_installed_versions(MC_DIR)
                fabric_versions = [
                    v["id"] for v in versions
                    if "fabric" in v["id"].lower() and base_version_id in v["id"]
                ]

                if fabric_versions:
                    launch_id = fabric_versions[0] # Grab the first match
                    self.log(f"✅ Fabric already installed: {launch_id}")
                else:
                    self.log(f"⬇️ Installing Fabric for {base_version_id}...")
                    minecraft_launcher_lib.fabric.install_fabric(base_version_id, MC_DIR)

                    # re-scan after install to get the correct ID
                    versions = minecraft_launcher_lib.utils.get_installed_versions(MC_DIR)
                    fabric_versions = [
                        v["id"] for v in versions
                        if "fabric" in v["id"].lower() and base_version_id in v["id"]
                    ]

                    if not fabric_versions:
                        raise Exception("Fabric install failed - could not find version ID after install.")

                    launch_id = fabric_versions[0]
                    self.log(f"✅ Fabric Installed: {launch_id}")

            elif loader_type == "Forge":
                self.log(f"⚒️ Looking up Forge for {base_version_id}...")
                
                # Check if we already have a forge version installed for this base version
                versions = minecraft_launcher_lib.utils.get_installed_versions(MC_DIR)
                forge_installed_versions = [
                    v["id"] for v in versions
                    if "forge" in v["id"].lower() and base_version_id in v["id"]
                ]

                if forge_installed_versions:
                    launch_id = forge_installed_versions[0]
                    self.log(f"✅ Forge already installed: {launch_id}")
                else:
                    forge_version = minecraft_launcher_lib.forge.find_forge_version(base_version_id)
                    if forge_version is None:
                        raise Exception(f"Forge not supported/found for {base_version_id}")
                    
                    self.log(f"⚒️ Installing Forge {forge_version} (This runs the installer)...")
                    # Note: install_forge_version needs the path to java to run the processor
                    minecraft_launcher_lib.forge.install_forge_version(
                        forge_version, 
                        MC_DIR, 
                        callback={"setStatus": lambda x: None, "setProgress": self.update_progress},
                        java=java_path
                    )
                    
                    # Re-scan to find the directory name created by Forge
                    versions = minecraft_launcher_lib.utils.get_installed_versions(MC_DIR)
                    forge_installed_versions = [
                        v["id"] for v in versions
                        if "forge" in v["id"].lower() and base_version_id in v["id"]
                    ]
                    if not forge_installed_versions:
                         raise Exception("Forge install failed - could not find version ID after install.")
                    
                    launch_id = forge_installed_versions[0]
                    self.log(f"✅ Forge Ready: {launch_id}")

            # 5. Skin Logic
            self.log("👕 Generating Custom Skin Pack...")
            pack_dir = SKIN_PACK_DIR
            texture_dir = os.path.join(pack_dir, "assets", "minecraft", "textures", "entity", "player")
            
            # Clean rebuild of skin pack
            if os.path.exists(pack_dir): shutil.rmtree(pack_dir)
            os.makedirs(os.path.join(texture_dir, "wide"), exist_ok=True)
            os.makedirs(os.path.join(texture_dir, "slim"), exist_ok=True)
            
            with open(os.path.join(pack_dir, "pack.mcmeta"), "w") as f:
                json.dump({"pack": {"pack_format": 15, "description": "MCV Custom Skin"}}, f)
            
            skin_temp = os.path.join(MC_DIR, "temp_skin.png")
            
            # Fetch skin
            skin_path = self.config['skin_path']
            try:
                if self.config['skin_type'] == 'url':
                    # Check if it's the embedded default marker OR the old URL for backwards compatibility
                    if skin_path == DEFAULT_SKIN_MARKER or "minimal-steve" in skin_path:
                        self.log("👕 Using Embedded Default Skin...")
                        with open(skin_temp, "wb") as f:
                            f.write(base64.b64decode(DEFAULT_SKIN_BASE64))
                    else:
                        self.download_with_callback(skin_path, skin_temp, "Skin File")
                else:
                    if os.path.exists(skin_path):
                        shutil.copyfile(skin_path, skin_temp)
                    else:
                        self.log("⚠️ Local skin file missing, using default embedded.")
                        with open(skin_temp, "wb") as f:
                            f.write(base64.b64decode(DEFAULT_SKIN_BASE64))
                
                shutil.copyfile(skin_temp, os.path.join(texture_dir, "wide", "steve.png"))
                shutil.copyfile(skin_temp, os.path.join(texture_dir, "slim", "alex.png"))
            except Exception as e:
                self.log(f"⚠️ Skin Error: {e}. Launching without custom skin.")

            # 6. Auto Equip (Options.txt)
            self.log("⚙️ Auto-equipping Skin Pack...")
            options_path = os.path.join(MC_DIR, "options.txt")
            pack_entry = "file/MCV_SkinPack"
            
            # Ensure options.txt exists and has the resource pack enabled
            if not os.path.exists(options_path):
                 with open(options_path, "w") as f: f.write(f'resourcePacks:["{pack_entry}"]\n')
            else:
                with open(options_path, "r") as f: lines = f.readlines()
                new_lines = []
                found = False
                for line in lines:
                    if line.startswith("resourcePacks:"):
                        found = True
                        if "MCV_SkinPack" not in line:
                            try:
                                # Parse existing JSON list in options.txt
                                content_str = line.split(":", 1)[1].strip()
                                content = json.loads(content_str)
                                if pack_entry not in content: 
                                    content.append(pack_entry)
                                new_lines.append(f"resourcePacks:{json.dumps(content)}\n")
                            except:
                                # Fallback if parsing fails
                                new_lines.append(line)
                        else:
                            new_lines.append(line)
                    else:
                        new_lines.append(line)
                if not found:
                    new_lines.append(f'resourcePacks:["{pack_entry}"]\n')
                
                with open(options_path, "w") as f: f.writelines(new_lines)

            # 7. Launch
            self.log(f"🚀 Launching Version: {launch_id}")
            offline_uuid = str(uuid.uuid3(uuid.NAMESPACE_DNS, username))
            options = {
                "username": username,
                "uuid": offline_uuid,
                "token": "offline-token",
                "executablePath": java_path,
                "launcherName": "MCV-Glass",
                "launcherVersion": "4.0",
                "jvmArguments": [
                    f"-Xmx{ram_gb}G", f"-Xms{min(2, int(ram_gb))}G",
                    "-Dminecraft.launcher.brand=minecraft-launcher-lib",
                    "-XX:+UnlockExperimentalVMOptions", "-XX:+UseG1GC"
                ]
            }
            
            cmd = minecraft_launcher_lib.command.get_minecraft_command(launch_id, MC_DIR, options)
            
            self.log("🟢 GO! Game Process Started.")
            subprocess.Popen(cmd)
            self.finished_signal.emit()

        except Exception as e:
            self.error_signal.emit(str(e))

# === GUI ===
class GlassLauncher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MCV Launcher // Glass Edition")
        self.resize(950, 650)
        self.config = self.load_config()
        self.setup_ui()
        self.apply_styles()
        self.update_skin_button_text()

    def load_config(self):
        default = {
            "username": "MCVPlayer",
            "ram_gb": 4,
            "version": "latest",
            "loader": "Vanilla",
            "skin_type": "url",
            "skin_path": DEFAULT_SKIN_MARKER
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f: return {**default, **json.load(f)}
            except: pass
        return default

    def save_config(self):
        self.config['username'] = self.user_input.text()
        self.config['ram_gb'] = int(self.ram_combo.currentText().replace("GB", ""))
        self.config['version'] = self.version_input.text()
        self.config['loader'] = self.loader_combo.currentText()
        # skin params updated by dialog
        
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=4)

    def setup_ui(self):
        # Main Container
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        
        # Main Layout
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(50, 50, 50, 50)

        # === LEFT PANEL (Controls) ===
        self.controls_frame = QFrame()
        self.controls_frame.setObjectName("GlassPanel")
        self.controls_layout = QVBoxLayout(self.controls_frame)
        self.controls_layout.setSpacing(20)
        self.controls_layout.setContentsMargins(30, 30, 30, 30)

        # Title
        self.title_label = QLabel("MCV LAUNCHER")
        self.title_label.setObjectName("Title")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.controls_layout.addWidget(self.title_label)

        # Username
        self.user_label = QLabel("USER ID")
        self.user_input = QLineEdit(self.config['username'])
        self.controls_layout.addWidget(self.user_label)
        self.controls_layout.addWidget(self.user_input)

        # RAM, Version, Loader Layout
        self.config_grid = QHBoxLayout()
        
        # RAM
        self.ram_box = QVBoxLayout()
        self.ram_label = QLabel("RAM")
        self.ram_combo = QComboBox()
        self.ram_combo.addItems([f"{i}GB" for i in range(2, 17)])
        self.ram_combo.setCurrentText(f"{self.config['ram_gb']}GB")
        self.ram_box.addWidget(self.ram_label)
        self.ram_box.addWidget(self.ram_combo)
        
        # Version
        self.ver_box = QVBoxLayout()
        self.ver_label = QLabel("VERSION")
        self.version_input = QLineEdit(self.config['version'])
        self.version_input.setPlaceholderText("e.g. 1.20.1")
        self.ver_box.addWidget(self.ver_label)
        self.ver_box.addWidget(self.version_input)

        # Loader
        self.loader_box = QVBoxLayout()
        self.loader_label = QLabel("MOD LOADER")
        self.loader_combo = QComboBox()
        self.loader_combo.addItems(["Vanilla", "Fabric", "Forge"])
        self.loader_combo.setCurrentText(self.config['loader'])
        self.loader_box.addWidget(self.loader_label)
        self.loader_box.addWidget(self.loader_combo)

        self.config_grid.addLayout(self.ram_box)
        self.config_grid.addLayout(self.ver_box)
        self.config_grid.addLayout(self.loader_box)
        
        self.controls_layout.addLayout(self.config_grid)

        # Skin Selector
        self.skin_label = QLabel("PLAYER SKIN")
        self.skin_btn = QPushButton("Change Skin")
        self.skin_btn.setObjectName("SecondaryBtn")
        self.skin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.skin_btn.clicked.connect(self.open_skin_dialog)
        
        self.controls_layout.addWidget(self.skin_label)
        self.controls_layout.addWidget(self.skin_btn)

        self.mod_btn = QPushButton("➕ ADD MOD (.jar)")
        self.mod_btn.setObjectName("SecondaryBtn")
        self.mod_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mod_btn.clicked.connect(self.add_mod)

        self.controls_layout.addWidget(self.mod_btn)

        # Spacer
        self.controls_layout.addStretch()

        # Buttons
        self.launch_btn = QPushButton("🚀 LAUNCH GAME")
        self.launch_btn.setObjectName("LaunchBtn")
        self.launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.launch_btn.clicked.connect(self.start_launch)
        
        self.folder_btn = QPushButton("📂 OPEN FOLDER")
        self.folder_btn.setObjectName("SecondaryBtn")
        self.folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_btn.clicked.connect(lambda: os.startfile(MC_DIR) if platform.system() == "Windows" else subprocess.Popen(["xdg-open", MC_DIR]))

        self.controls_layout.addWidget(self.launch_btn)
        self.controls_layout.addWidget(self.folder_btn)

        # === RIGHT PANEL (Logs & Status) ===
        self.status_frame = QFrame()
        self.status_frame.setObjectName("GlassPanel")
        self.status_layout = QVBoxLayout(self.status_frame)
        self.status_layout.setContentsMargins(30, 30, 30, 30)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setObjectName("Console")
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)

        self.status_layout.addWidget(self.console)
        self.status_layout.addWidget(self.progress_bar)

        # Add frames to main layout
        self.main_layout.addWidget(self.controls_frame, 2)
        self.main_layout.addWidget(self.status_frame, 3)

        # Drop Shadows
        self.add_shadow(self.controls_frame)
        self.add_shadow(self.status_frame)

    def add_shadow(self, widget):
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 5)
        widget.setGraphicsEffect(shadow)

    def update_skin_button_text(self):
        t = self.config['skin_type']
        p = self.config['skin_path']
        if t == "url":
            if p == DEFAULT_SKIN_MARKER:
                self.skin_btn.setText("👕 Current: Default Embedded")
            else:
                self.skin_btn.setText("🔗 Current: Custom URL")
        else:
            filename = os.path.basename(p)
            self.skin_btn.setText(f"📁 Current: {filename}")

    def open_skin_dialog(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("Select Skin Source")
        msg.setText("How do you want to load your skin?")
        msg.setStyleSheet("background-color: #2b2b3b; color: white; font-size: 14px;")
        
        btn_local = msg.addButton("📁 Local File", QMessageBox.ButtonRole.ActionRole)
        btn_url = msg.addButton("🔗 URL", QMessageBox.ButtonRole.ActionRole)
        btn_default = msg.addButton("👕 Default", QMessageBox.ButtonRole.ActionRole)
        btn_cancel = msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        
        msg.exec()

        if msg.clickedButton() == btn_local:
            file_path, _ = QFileDialog.getOpenFileName(self, "Select Skin File", "", "Images (*.png)")
            if file_path:
                self.config['skin_type'] = 'file'
                self.config['skin_path'] = file_path
                self.update_skin_button_text()
                
        elif msg.clickedButton() == btn_url:
            text, ok = QInputDialog.getText(self, "Skin URL", "Enter direct link to skin PNG:")
            if ok and text:
                self.config['skin_type'] = 'url'
                self.config['skin_path'] = text
                self.update_skin_button_text()
                
        elif msg.clickedButton() == btn_default:
            self.config['skin_type'] = 'url'
            self.config['skin_path'] = DEFAULT_SKIN_MARKER
            self.update_skin_button_text()

    def add_mod(self):
        loader = self.loader_combo.currentText()

        if loader == "Vanilla":
            QMessageBox.warning(self, "No Mods", "Mods require Fabric or Forge.")
            return

        version = self.version_input.text()
        mods_dir = os.path.join(MC_DIR, "mods")

        os.makedirs(mods_dir, exist_ok=True)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Mod File",
            "",
            "Minecraft Mods (*.jar)"
        )

        if not file_path:
            return

        try:
            shutil.copy(file_path, mods_dir)
            QMessageBox.information(
                self,
                "Mod Added",
                f"Mod installed successfully!\n\n{os.path.basename(file_path)}"
        )
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def start_launch(self):
        self.save_config()
        self.console.clear()
        self.launch_btn.setEnabled(False)
        self.launch_btn.setText("⏳ WORKING...")
        
        self.worker = LauncherWorker(self.config)
        self.worker.log_signal.connect(self.append_log)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.error_signal.connect(self.handle_error)
        self.worker.finished_signal.connect(self.launch_complete)
        self.worker.start()

    def append_log(self, text):
        self.console.append(text)
        cursor = self.console.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.console.setTextCursor(cursor)

    def handle_error(self, err):
        self.console.append(f"<span style='color:#ff5555'>❌ ERROR: {err}</span>")
        self.launch_btn.setEnabled(True)
        self.launch_btn.setText("🚀 LAUNCH GAME")

    def launch_complete(self):
        self.console.append("<span style='color:#55ff55'>✨ Launcher task finished.</span>")
        self.launch_btn.setEnabled(True)
        self.launch_btn.setText("🚀 LAUNCH GAME")
        self.progress_bar.setValue(100)

    def apply_styles(self):
        p = self.palette()
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, QColor(20, 20, 30))
        gradient.setColorAt(0.5, QColor(40, 30, 60))
        gradient.setColorAt(1.0, QColor(10, 10, 20))
        p.setBrush(QPalette.ColorRole.Window, QBrush(gradient))
        self.setPalette(p)

        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a1a2e, stop:1 #16213e);
            }
            QLabel {
                color: #e0e0e0;
                font-family: 'Segoe UI', sans-serif;
                font-size: 14px;
                font-weight: 600;
            }
            #Title {
                font-size: 24px;
                font-weight: 800;
                color: #ffffff;
                letter-spacing: 2px;
                margin-bottom: 10px;
            }
            
            #GlassPanel {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 20px;
            }

            QLineEdit, QComboBox {
                background-color: rgba(0, 0, 0, 0.3);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 10px;
                padding: 8px 12px;
                color: white;
                font-size: 14px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #4a90e2;
                background-color: rgba(0, 0, 0, 0.5);
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #2b2b3b;
                color: white;
                selection-background-color: #4a90e2;
            }
            
            QTextEdit {
                background-color: rgba(0, 0, 0, 0.4);
                border: none;
                border-radius: 15px;
                color: #00ffcc;
                font-family: 'Consolas', monospace;
                padding: 10px;
                font-size: 12px;
            }

            QProgressBar {
                background-color: rgba(0, 0, 0, 0.4);
                border-radius: 5px;
                height: 8px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00c6ff, stop:1 #0072ff);
                border-radius: 5px;
            }

            #LaunchBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00c6ff, stop:1 #0072ff);
                color: white;
                border: none;
                border-radius: 12px;
                padding: 15px;
                font-size: 16px;
                font-weight: bold;
                margin-top: 10px;
            }
            #LaunchBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00d6ff, stop:1 #0082ff);
            }
            #LaunchBtn:disabled {
                background: #444;
                color: #888;
            }
            
            #SecondaryBtn {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                color: #ccc;
                border-radius: 12px;
                padding: 10px;
                font-weight: 600;
                text-align: left;
                padding-left: 15px;
            }
            #SecondaryBtn:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: white;
            }
            
            QMessageBox {
                background-color: #2b2b3b;
            }
            QMessageBox QPushButton {
                width: 80px;
                padding: 5px;
                border-radius: 5px;
                background-color: #444;
                color: white;
            }
        """)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    window = GlassLauncher()
    window.show()
    sys.exit(app.exec())