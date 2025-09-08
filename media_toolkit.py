# media_toolkit.py
import sys
import os
import json
import threading
import subprocess
import mimetypes
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QTabWidget,
    QPushButton, QLabel, QLineEdit, QFileDialog, QTextEdit,
    QComboBox, QHBoxLayout, QProgressBar, QGridLayout, QAction, QMenuBar
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject

# third-party libs
try:
    import yt_dlp
except Exception as e:
    print("Missing dependency: yt-dlp is required. Install with: pip install yt-dlp")
    raise

try:
    from PIL import Image
except Exception:
    Image = None

try:
    import pypandoc
except Exception:
    pypandoc = None

# -------------------------
# Settings persistence
# -------------------------
SETTINGS_FILE = Path.home() / ".media_toolkit_settings.json"
DEFAULT_SETTINGS = {
    "ffmpeg_path": "",  # user can set; if empty, uses 'ffmpeg' on PATH
    "default_output": str(Path.home() / "Downloads"),
}

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
                for k, v in DEFAULT_SETTINGS.items():
                    if k not in s:
                        s[k] = v
                return s
        except Exception:
            return DEFAULT_SETTINGS.copy()
    else:
        return DEFAULT_SETTINGS.copy()

def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except Exception as e:
        print("Failed to save settings:", e)

settings = load_settings()

def ffmpeg_exec():
    p = settings.get("ffmpeg_path", "").strip()
    if p:
        exe = Path(p) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        return str(exe)
    return "ffmpeg"

# -------------------------
# Signals emitter
# -------------------------
class Emitter(QObject):
    log = pyqtSignal(str)        # append a log line
    progress = pyqtSignal(float) # percent 0..100

# -------------------------
# Worker utilities
# -------------------------
def run_threaded(target, *args, on_done=None):
    def wrapper():
        try:
            result = target(*args)
            if on_done:
                on_done(result)
        except Exception as e:
            # If target raises, ensure it prints/logs
            print("Background task error:", e)
    t = threading.Thread(target=wrapper, daemon=True)
    t.start()
    return t

# -------------------------
# yt-dlp helpers
# -------------------------
def build_ydl_opts_for_download(outdir, fmt, emitter: Emitter = None, cookiefile=None):
    opts = {
        "outtmpl": os.path.join(outdir, "%(uploader)s - %(title)s.%(ext)s"),
        "noplaylist": False,
        "nocheckcertificate": True,
    }
    # progress hook
    if emitter:
        def p_hook(d):
            status = d.get("status")
            if status == "downloading":
                p = d.get("_percent_str", "0%").strip().replace("%", "")
                try:
                    emitter.progress.emit(float(p))
                except Exception:
                    emitter.progress.emit(0.0)
                # log some info
                line = f"Downloading: {d.get('filename','')} {d.get('_percent_str','')}"
                emitter.log.emit(line)
            elif status == "finished":
                emitter.progress.emit(100.0)
                emitter.log.emit("Merging/processing...")
        opts["progress_hooks"] = [p_hook]

    if cookiefile:
        opts["cookiefile"] = cookiefile

    if fmt == "mp3":
        # audio extraction using ffmpeg (yt-dlp will call ffmpeg if available)
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "prefer_ffmpeg": True,
            "postprocessor_args": [],
            "ffmpeg_location": settings.get("ffmpeg_path", "") or None,
        })
    elif fmt == "mp4":
        opts.update({
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "ffmpeg_location": settings.get("ffmpeg_path", "") or None,
        })
    else:
        opts.update({"format": "best", "ffmpeg_location": settings.get("ffmpeg_path", "") or None})
    return opts

def yt_download(url, outdir, fmt, emitter: Emitter = None, cookiefile=None):
    if not url or not url.strip():
        return "❌ No URL provided"
    outdir = outdir or settings.get("default_output") or os.getcwd()
    try:
        if emitter:
            emitter.log.emit(f"Starting download: {url} → {outdir} as {fmt}")
            emitter.progress.emit(0.0)
        ydl_opts = build_ydl_opts_for_download(outdir, fmt, emitter, cookiefile)
        # remove None values (yt-dlp doesn't like None)
        ydl_opts = {k: v for k, v in ydl_opts.items() if v is not None}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if emitter:
                emitter.log.emit(f"Saved: {filename}")
                emitter.progress.emit(100.0)
        return f"✅ Download complete: {url}"
    except Exception as e:
        if emitter:
            emitter.log.emit(f"❌ Error: {e}")
            emitter.progress.emit(0.0)
        return f"❌ Error: {e}"

# -------------------------
# Conversion helpers
# -------------------------
def convert_image_with_pillow(infile, outfile, target_format):
    if Image is None:
        raise RuntimeError("Pillow is not installed (pip install Pillow)")
    img = Image.open(infile).convert("RGBA")
    fmt = target_format.lower()
    if fmt == "ico":
        # Provide multiple sizes
        sizes = [(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]
        img.save(outfile, format="ICO", sizes=sizes)
    else:
        # For PNG/JPG/WebP etc.
        # Determine appropriate mode
        if fmt in ("jpg","jpeg") and img.mode in ("RGBA","LA"):
            # convert to RGB and drop alpha for JPEG
            background = Image.new("RGB", img.size, (255,255,255))
            background.paste(img, mask=img.split()[3]) # 3 is alpha
            background.save(outfile, format=fmt.upper())
        else:
            img.save(outfile, format=fmt.upper())

def convert_with_ffmpeg_cmd(infile, outfile):
    ff = ffmpeg_exec()
    cmd = [ff, "-y", "-i", infile, outfile]
    subprocess.run(cmd, check=True)

def convert_document_with_pandoc(infile, outfile, target_format):
    if pypandoc is None:
        raise RuntimeError("pypandoc not installed or pandoc not available")
    pypandoc.convert_file(infile, target_format, outputfile=outfile)

def convert_generic(infile, outfile, target_format, emitter: Emitter = None):
    try:
        mime, _ = mimetypes.guess_type(infile)
        if emitter:
            emitter.log.emit(f"Converting {os.path.basename(infile)} → {os.path.basename(outfile)}")
        # If image
        if mime and mime.startswith("image"):
            convert_image_with_pillow(infile, outfile, target_format)
        # Document types -> use pandoc if available
        elif target_format.lower() in ("pdf","docx","txt","md","rtf") and pypandoc:
            convert_document_with_pandoc(infile, outfile, target_format)
        # Video/audio -> ffmpeg
        elif mime and (mime.startswith("video") or mime.startswith("audio")):
            convert_with_ffmpeg_cmd(infile, outfile)
        else:
            # fallback try ffmpeg
            convert_with_ffmpeg_cmd(infile, outfile)
        if emitter:
            emitter.log.emit(f"✅ Converted: {outfile}")
        return f"✅ Converted to {target_format}"
    except Exception as e:
        if emitter:
            emitter.log.emit(f"❌ Conversion error: {e}")
        return f"❌ Conversion error: {e}"

# -------------------------
# GUI: main window and tabs
# -------------------------
class MainApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🎬 All-in-One Media Toolkit")
        self.setGeometry(200, 120, 920, 620)

        # Menu: Settings quick access
        menubar = QMenuBar(self)
        settings_menu = menubar.addMenu("Settings")
        set_ffmpeg_action = QAction("Set ffmpeg folder...", self)
        set_ffmpeg_action.triggered.connect(self.set_ffmpeg_folder)
        settings_menu.addAction(set_ffmpeg_action)
        set_default_out = QAction("Set default output folder...", self)
        set_default_out.triggered.connect(self.set_default_output_folder)
        settings_menu.addAction(set_default_out)
        self.setMenuBar(menubar)

        # Main tabs
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.youtube_tab = self.build_youtube_tab()
        self.converter_tab = self.build_converter_tab()
        self.insta_tab = self.build_instagram_tab()
        self.tabs.addTab(self.youtube_tab, "Downloader")
        self.tabs.addTab(self.converter_tab, "Converter")
        self.tabs.addTab(self.insta_tab, "Instagram")

    # ---------- Settings helpers ----------
    def set_ffmpeg_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select ffmpeg bin folder (contains ffmpeg)")
        if folder:
            settings["ffmpeg_path"] = folder
            save_settings(settings)
            self.youtube_log_append(f"ffmpeg path set to: {folder}")

    def set_default_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select default output folder")
        if folder:
            settings["default_output"] = folder
            save_settings(settings)
            self.youtube_log_append(f"default output set to: {folder}")

    # ---------- YouTube Tab ----------
    def build_youtube_tab(self):
        t = QWidget()
        layout = QVBoxLayout()

        grid = QGridLayout()
        grid.addWidget(QLabel("URL:"), 0, 0)
        self.yt_url = QLineEdit()
        grid.addWidget(self.yt_url, 0, 1, 1, 4)

        grid.addWidget(QLabel("Format:"), 1, 0)
        self.yt_format_box = QComboBox()
        self.yt_format_box.addItems(["mp4","mp3"])
        grid.addWidget(self.yt_format_box, 1, 1)

        grid.addWidget(QLabel("Cookie file (optional):"), 2, 0)
        self.yt_cookie = QLineEdit()
        grid.addWidget(self.yt_cookie, 2, 1, 1, 3)
        cookie_btn = QPushButton("Browse")
        cookie_btn.clicked.connect(self.browse_cookie_file)
        grid.addWidget(cookie_btn, 2, 4)

        out_btn = QPushButton("Output folder")
        out_btn.clicked.connect(self.choose_youtube_output)
        grid.addWidget(out_btn, 1, 2)

        self.yt_out_display = QLineEdit(settings.get("default_output",""))
        grid.addWidget(self.yt_out_display, 1, 3, 1, 2)

        layout.addLayout(grid)

        btn_layout = QHBoxLayout()
        self.yt_download_btn = QPushButton("Start Download")
        self.yt_download_btn.clicked.connect(self.start_youtube_download)
        btn_layout.addWidget(self.yt_download_btn)

        self.yt_progress = QProgressBar()
        self.yt_progress.setValue(0)
        btn_layout.addWidget(self.yt_progress)

        layout.addLayout(btn_layout)

        self.yt_log = QTextEdit()
        self.yt_log.setReadOnly(True)
        layout.addWidget(self.yt_log)

        t.setLayout(layout)
        return t

    def browse_cookie_file(self):
        f,_ = QFileDialog.getOpenFileName(self, "Select cookie file (Netscape format)", filter="All files (*)")
        if f:
            self.yt_cookie.setText(f)

    def choose_youtube_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self.yt_out_display.setText(folder)

    def yt_log_append(self, text):
        self.yt_log.append(text)

    def yt_progress_update(self, val):
        self.yt_progress.setValue(int(val))

    def start_youtube_download(self):
        url = self.yt_url.text().strip()
        fmt = self.yt_format_box.currentText()
        out = self.yt_out_display.text().strip() or settings.get("default_output") or os.getcwd()
        cookie = self.yt_cookie.text().strip() or None

        if not url:
            self.yt_log_append("❌ Please paste a URL.")
            return

        emitter = Emitter()
        emitter.log.connect(self.yt_log_append)
        emitter.progress.connect(self.yt_progress_update)
        # Run in background
        run_threaded(yt_download, url, out, fmt, emitter, cookie)

    # ---------- Converter Tab ----------
    def build_converter_tab(self):
        t = QWidget()
        layout = QVBoxLayout()

        # input file selection
        in_layout = QHBoxLayout()
        self.conv_input = QLineEdit()
        self.conv_input.setPlaceholderText("Select input file")
        in_layout.addWidget(self.conv_input)
        browse_in = QPushButton("Browse")
        browse_in.clicked.connect(self.browse_conv_input)
        in_layout.addWidget(browse_in)

        # output folder selection
        self.conv_out = QLineEdit(settings.get("default_output",""))
        self.conv_out.setPlaceholderText("Output folder (leave empty for same folder as input)")
        out_btn = QPushButton("Choose Output Folder")
        out_btn.clicked.connect(self.browse_conv_output)
        in_layout.addWidget(out_btn)
        in_layout.addWidget(self.conv_out)

        layout.addLayout(in_layout)

        # format selection + convert button
        op_layout = QHBoxLayout()
        op_layout.addWidget(QLabel("Convert to:"))
        self.conv_format = QComboBox()
        self.conv_format.setEditable(False)
        self.conv_format.addItems(["mp3","mp4","wav","png","jpg","ico","webp","pdf","txt","docx"])
        op_layout.addWidget(self.conv_format)
        self.conv_btn = QPushButton("Convert")
        self.conv_btn.clicked.connect(self.start_conversion)
        op_layout.addWidget(self.conv_btn)

        self.conv_progress = QProgressBar()
        self.conv_progress.setValue(0)
        op_layout.addWidget(self.conv_progress)

        layout.addLayout(op_layout)

        # log area
        self.conv_log = QTextEdit()
        self.conv_log.setReadOnly(True)
        layout.addWidget(self.conv_log)

        t.setLayout(layout)
        return t

    def browse_conv_input(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select file to convert", filter="All files (*)")
        if f:
            self.conv_input.setText(f)
            # auto-suggest formats based on input type
            mt, _ = mimetypes.guess_type(f)
            available = []
            if mt and mt.startswith("video"):
                available = ["mp4","mp3","wav","gif"]
            elif mt and mt.startswith("audio"):
                available = ["mp3","wav","mp4"]
            elif mt and mt.startswith("image"):
                available = ["png","jpg","ico","webp"]
            elif mt and (mt.startswith("application") or mt=="text/plain"):
                available = ["pdf","docx","txt","md"]
            else:
                available = ["mp3","mp4","png","jpg","pdf","txt"]
            self.conv_format.clear()
            self.conv_format.addItems(available)

    def browse_conv_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self.conv_out.setText(folder)

    def conv_log_append(self, text):
        self.conv_log.append(text)

    def conv_progress_update(self, val):
        self.conv_progress.setValue(int(val))

    def start_conversion(self):
        infile = self.conv_input.text().strip()
        if not infile or not os.path.isfile(infile):
            self.conv_log_append("❌ Select a valid input file.")
            return
        target = self.conv_format.currentText().strip()
        out_folder = self.conv_out.text().strip() or os.path.dirname(infile) or settings.get("default_output")
        base = os.path.splitext(os.path.basename(infile))[0]
        outfile = os.path.join(out_folder, f"{base}.{target}")

        emitter = Emitter()
        emitter.log.connect(self.conv_log_append)
        emitter.progress.connect(self.conv_progress_update)
        # run conversion in background
        run_threaded(convert_generic := (lambda a,b,c,d: convert_generic_wrapper(a,b,c,d)), infile, outfile, target, emitter)
    
    # ---------- Instagram Tab ----------
    # Use yt-dlp for Instagram downloads (works for posts/reels)
    def build_instagram_tab(self):
        t = QWidget()
        layout = QVBoxLayout()

        grid = QGridLayout()
        grid.addWidget(QLabel("Instagram URL:"), 0, 0)
        self.insta_url = QLineEdit()
        grid.addWidget(self.insta_url, 0, 1, 1, 3)

        grid.addWidget(QLabel("Cookie file (optional):"), 1, 0)
        self.insta_cookie = QLineEdit()
        grid.addWidget(self.insta_cookie, 1, 1, 1, 2)
        inst_cookie_btn = QPushButton("Browse")
        inst_cookie_btn.clicked.connect(self.browse_insta_cookie)
        grid.addWidget(inst_cookie_btn, 1, 3)

        out_btn = QPushButton("Output folder")
        out_btn.clicked.connect(self.choose_insta_output)
        grid.addWidget(out_btn, 2, 0)

        self.insta_out = QLineEdit(settings.get("default_output",""))
        grid.addWidget(self.insta_out, 2, 1, 1, 3)

        layout.addLayout(grid)
        btns = QHBoxLayout()
        self.insta_download_btn = QPushButton("Download Post/Reel")
        self.insta_download_btn.clicked.connect(self.start_insta_download)
        btns.addWidget(self.insta_download_btn)

        self.insta_progress = QProgressBar()
        self.insta_progress.setValue(0)
        btns.addWidget(self.insta_progress)
        layout.addLayout(btns)

        self.insta_log = QTextEdit()
        self.insta_log.setReadOnly(True)
        layout.addWidget(self.insta_log)

        t.setLayout(layout)
        return t

    def browse_insta_cookie(self):
        f,_ = QFileDialog.getOpenFileName(self, "Select cookie file (Netscape format)", filter="All files (*)")
        if f:
            self.insta_cookie.setText(f)

    def choose_insta_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self.insta_out.setText(folder)

    def insta_log_append(self, text):
        self.insta_log.append(text)

    def insta_progress_update(self, val):
        self.insta_progress.setValue(int(val))

    def start_insta_download(self):
        url = self.insta_url.text().strip()
        if not url:
            self.insta_log_append("❌ Paste an Instagram URL.")
            return
        out = self.insta_out.text().strip() or settings.get("default_output") or os.getcwd()
        cookie = self.insta_cookie.text().strip() or None

        emitter = Emitter()
        emitter.log.connect(self.insta_log_append)
        emitter.progress.connect(self.insta_progress_update)

        # run via yt-dlp; reuse yt_download helper but force noplaylist True for IG
        def insta_worker(u,o,c,em):
            try:
                em.log.emit(f"Starting Instagram download: {u}")
                opts = build_ydl_opts_for_download(o, "mp4", em, c)
                opts["noplaylist"] = True
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(u, download=True)
                    fn = ydl.prepare_filename(info)
                    em.log.emit(f"Saved: {fn}")
                em.progress.emit(100.0)
                return "✅ Done"
            except Exception as e:
                em.log.emit(f"❌ Error: {e}")
                em.progress.emit(0.0)
                return f"❌ Error: {e}"

        run_threaded(insta_worker, url, out, cookie, emitter)



def convert_generic_wrapper(infile, outfile, target, emitter):
    # wrapper that calls convert_generic and emits progress
    emitter.log.emit(f"Starting conversion: {os.path.basename(infile)} → {os.path.basename(outfile)}")
    # simple coarse progress: 0 -> 50 during conversion, 50 -> 100 on success
    emitter.progress.emit(0.0)
    try:
        # call main converter
        res = convert_generic(infile, outfile, target, emitter)
        emitter.progress.emit(80.0)
        emitter.log.emit(res)
        emitter.progress.emit(100.0)
        return res
    except Exception as e:
        emitter.log.emit(f"❌ Error: {e}")
        emitter.progress.emit(0.0)
        return f"❌ Error: {e}"

# -------------------------
# Run application
# -------------------------
def main():
    app = QApplication(sys.argv)
    win = MainApp()
    win.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()