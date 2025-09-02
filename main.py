import os
import sys
import json
import queue
import shutil
import hashlib
import tarfile
import filecmp
import threading
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, Callable, Tuple, List

# Third-party libraries
import zstandard as zstd
try:
    import detools
except ImportError:
    print("Error: 'detools' library not found. Please run: pip install detools")
    sys.exit(1)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

FONT_SIZES = {
    "small": 10,
    "body": 11,
    "heading": 14,
    "title": 16,
}

THEMES = {
    "dark": {
        "bg": "#282a36",
        "fg": "#f8f8f2",
        "bg_input": "#44475a",
        "fg_input": "#f8f8f2",
        "border": "#6272a4",
        "primary": "#50fa7b",
        "secondary": "#bd93f9",
        "accent": "#ff79c6",
        "info": "#8be9fd",
        "error": "#ff5555",
        "success": "#50fa7b",
        "warning": "#f1fa8c",
        "bg_selected": "#44475a",
    }
}

APP_WIDTH = 560
APP_HEIGHT = 700
CONFIG_FILE = "patch_config.json"

class PatchLogic:
    """Encapsulates the core functionality for creating and applying patches."""

    def __init__(self,
                 log_callback: Callable[[str, str], None],
                 progress_callback: Callable[[int, int], None],
                 status_callback: Callable[[str], None],
                 cancel_event: threading.Event):
        self.log = log_callback
        self.progress = progress_callback
        self.status = status_callback
        self.cancel_event = cancel_event

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise InterruptedError("Operation cancelled by user.")

    @staticmethod
    def _sha256sum(path: str, chunk_size: int = 65536) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                h.update(chunk)
        return h.hexdigest()

    def create_patch(self, old_dir: str, new_dir: str, patch_package: str):
        """Create a patch package between two directory versions."""
        self.log("🚀 Starting patch creation...", "title")
        patch_tmp = Path("patch_tmp_creator")
        if patch_tmp.exists():
            shutil.rmtree(patch_tmp)
        patch_tmp.mkdir(exist_ok=True)

        try:
            manifest = {}
            processed_files = 0

            old_path_obj = Path(old_dir)
            new_path_obj = Path(new_dir)
            
            all_new_files = [p for p in new_path_obj.rglob("*") if p.is_file()]
            all_old_files = [p for p in old_path_obj.rglob("*") if p.is_file()]
            total_files = len(all_new_files) + len(all_old_files)
            
            for file_path in all_new_files:
                self._check_cancel()
                rel_path = file_path.relative_to(new_path_obj).as_posix()
                old_file = old_path_obj / rel_path
                
                self.status(f"Processing: {rel_path}")
                manifest[rel_path] = {
                    "sha256": self._sha256sum(str(file_path)),
                    "size": file_path.stat().st_size
                }

                patch_name = rel_path.replace("/", "__")
                
                if old_file.exists() and filecmp.cmp(str(old_file), str(file_path), shallow=False):
                    processed_files += 1
                    self.progress(processed_files, total_files)
                    continue
                
                if old_file.exists():
                    patch_file = patch_tmp / f"{patch_name}.patch"
                    with open(old_file, "rb") as fin_old, open(file_path, "rb") as fin_new, open(patch_file, "wb") as fout_patch:
                        detools.create_patch(fin_old, fin_new, fout_patch, compression="lzma")
                    self.log(f"📦 Patch created for {rel_path}", "info")
                else:
                    patch_file = patch_tmp / f"{patch_name}.full"
                    shutil.copy2(file_path, patch_file)
                    self.log(f"➕ New file stored: {rel_path}", "success")
                
                processed_files += 1
                self.progress(processed_files, total_files)

            for file_path in all_old_files:
                self._check_cancel()
                rel_path = file_path.relative_to(old_path_obj).as_posix()
                if not (new_path_obj / rel_path).exists():
                    patch_name = rel_path.replace("/", "__")
                    patch_file = patch_tmp / f"{patch_name}.delete"
                    patch_file.write_text("DELETE")
                    self.log(f"❌ Marked for deletion: {rel_path}", "warning")
                
                processed_files += 1
                self.progress(processed_files, total_files)

            self.status("Writing manifest...")
            with open(patch_tmp / "manifest.json", "w") as f:
                json.dump(manifest, f, indent=2)

            self.status("Compressing package...")
            cctx = zstd.ZstdCompressor(level=5)
            with open(patch_package, "wb") as f_out:
                with cctx.stream_writer(f_out) as compressor:
                    with tarfile.open(mode="w", fileobj=compressor) as tar:
                        tar.add(patch_tmp, arcname="")

            self.log(f"🎁 Patch package created successfully: {patch_package}", "success")

        finally:
            shutil.rmtree(patch_tmp, ignore_errors=True)

    def apply_patch(self, game_dir: str, patch_package: str, ram_limit_mb: int):
        """Apply a patch package to a directory."""
        self.log("🚀 Starting patch application...", "title")
        patch_tmp = Path("patch_tmp_applier")
        rollback_dir = patch_tmp / "rollback"

        if patch_tmp.exists():
            shutil.rmtree(patch_tmp)
        patch_tmp.mkdir(parents=True, exist_ok=True)
        rollback_dir.mkdir(exist_ok=True)

        try:
            self.status("Decompressing patch...")
            dctx = zstd.ZstdDecompressor(max_window_size=ram_limit_mb * 1024 * 1024)
            import io
            with open(patch_package, "rb") as f_in:
                with dctx.stream_reader(f_in) as reader:
                    decompressed_data = reader.read()
                    
                tar_stream = io.BytesIO(decompressed_data)
                
                with tarfile.open(fileobj=tar_stream) as tar:
                    tar.extractall(path=patch_tmp, filter="tar")
            
            manifest_path = patch_tmp / "manifest.json"
            if not manifest_path.exists():
                raise FileNotFoundError("manifest.json not found in patch archive.")
            with open(manifest_path, "r") as f:
                manifest = json.load(f)

            patch_files = [p for p in patch_tmp.rglob("*") if p.is_file() and p.name != "manifest.json"]
            total_files = len(patch_files)

            for i, patch_file in enumerate(patch_files):
                self._check_cancel()
                
                patch_name = patch_file.name
                rel_path_safe, ext = os.path.splitext(patch_name)
                rel_path = rel_path_safe.replace("__", os.sep)
                target_file = Path(game_dir) / rel_path
                
                self.status(f"Applying: {rel_path} ({i+1}/{total_files})")
                self.progress(i + 1, total_files)

                target_file.parent.mkdir(parents=True, exist_ok=True)

                if target_file.exists():
                    backup_path = rollback_dir / rel_path
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target_file, backup_path)
                
                if ext == ".patch":
                    if not target_file.exists():
                        raise FileNotFoundError(f"Cannot apply patch: original file not found at {target_file}")
                    tmp_new = str(target_file) + ".tmp"
                    with open(target_file, "rb") as f_old, open(patch_file, "rb") as f_patch, open(tmp_new, "wb") as f_new:
                        detools.apply_patch(f_old, f_patch, f_new)
                    os.replace(tmp_new, target_file)
                    self.log(f"✅ Patched: {rel_path}", "info")
                
                elif ext == ".full":
                    shutil.copy2(patch_file, target_file)
                    self.log(f"➕ Wrote: {rel_path}", "success")
                    
                elif ext == ".delete":
                    if target_file.exists():
                        os.remove(target_file)
                        self.log(f"❌ Deleted: {rel_path}", "warning")

            self.status("Validating files...")
            errors = []
            for rel_path, info in manifest.items():
                abs_path = Path(game_dir) / rel_path
                if not abs_path.exists():
                    errors.append(f"Missing file: {rel_path}")
                elif self._sha256sum(str(abs_path)) != info["sha256"]:
                    errors.append(f"Hash mismatch: {rel_path}")
            
            if errors:
                error_str = "\n".join(errors)
                raise ValueError(f"Validation failed for {len(errors)} files:\n{error_str}")

            self.log("🎉 All files patched and validated successfully!", "success")

        except Exception as e:
            self.log(f"💥 Error: {e}", "error")
            self.log("♻️ Attempting to restore from backup...", "warning")
            for root, _, files in os.walk(rollback_dir):
                for name in files:
                    backup_file = Path(root) / name
                    rel_path = backup_file.relative_to(rollback_dir)
                    target_file = Path(game_dir) / rel_path
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_file, target_file)
            self.log("Rollback completed.", "info")
            raise

        finally:
            shutil.rmtree(patch_tmp, ignore_errors=True)

class ToolTip:
    """Creates a tooltip for a given widget."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip_window = None
        self.widget.bind("<Enter>", self.show_tooltip)
        self.widget.bind("<Leave>", self.hide_tooltip)

    def show_tooltip(self, event=None):
        x, y, _, _ = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25

        self.tooltip_window = tk.Toplevel(self.widget)
        self.tooltip_window.wm_overrideredirect(True)
        self.tooltip_window.wm_geometry(f"+{x}+{y}")

        label = tk.Label(self.tooltip_window, text=self.text,
                         background="#44475a", foreground="#f8f8f2",
                         relief="solid", borderwidth=1,
                         font=("Segoe UI", 10))
        label.pack(ipadx=1)

    def hide_tooltip(self, event=None):
        if self.tooltip_window:
            self.tooltip_window.destroy()
        self.tooltip_window = None

class CollapsiblePane(ttk.Frame):
    """A collapsible pane widget."""
    def __init__(self, parent, text="", **kwargs):
        super().__init__(parent, **kwargs)
        self.columnconfigure(0, weight=1)
        self.text = text
        
        self.toggle_button = ttk.Checkbutton(self, text=self.text, command=self.toggle, style="Toggle.TButton")
        self.toggle_button.grid(row=0, column=0, sticky="ew")

        self.sub_frame = ttk.Frame(self, padding=(6, 6, 6, 6))
        self.sub_frame.grid(row=1, column=0, sticky="nsew")
        
        self.toggle_button.invoke()

    def toggle(self):
        if not self.toggle_button.instate(['selected']):
            self.sub_frame.grid_remove()
            self.toggle_button.configure(text=f"▶ {self.text}")
        else:
            self.sub_frame.grid()
            self.toggle_button.configure(text=f"▼ {self.text}")

class MainApplication(tk.Tk):
    def __init__(self, config: Dict[str, Any]):
        super().__init__()
        self.config = config
        self.current_font_scale = config.get("font_scale", 1.0)
        self.current_theme = "dark"
        self.plugins = {}
        self.is_running_task = False
        self.cancel_event = threading.Event()
        self.log_queue = queue.Queue()

        self.title("Patch Tool")
        self.geometry(f"{APP_WIDTH}x{APP_HEIGHT}")
        self.minsize(APP_WIDTH, 500)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.vars = {
            "creator_old_dir": tk.StringVar(value=self.config.get("creator_old_dir", "")),
            "creator_new_dir": tk.StringVar(value=self.config.get("creator_new_dir", "")),
            "creator_patch_file": tk.StringVar(value=self.config.get("creator_patch_file", "")),
            "applier_game_dir": tk.StringVar(value=self.config.get("applier_game_dir", "")),
            "applier_patch_file": tk.StringVar(value=self.config.get("applier_patch_file", "")),
            "applier_ram_limit": tk.IntVar(value=self.config.get("applier_ram_limit", 512)),
        }

        self._setup_styles()
        self._create_widgets()
        self._load_plugins()
        self.process_log_queue()

    def _setup_styles(self):
        self.style = ttk.Style(self)
        theme = THEMES[self.current_theme]
        font_scale = self.current_font_scale

        self.fonts = {
            "small": ("Segoe UI", int(FONT_SIZES["small"] * font_scale)),
            "body": ("Segoe UI", int(FONT_SIZES["body"] * font_scale)),
            "heading": ("Segoe UI", int(FONT_SIZES["heading"] * font_scale), "bold"),
            "title": ("Segoe UI", int(FONT_SIZES["title"] * font_scale), "bold"),
        }

        self.style.theme_use("clam")
        self.configure(bg=theme["bg"])
        
        self.style.configure(".", background=theme["bg"], foreground=theme["fg"], font=self.fonts["body"], padding=4)
        self.style.configure("TFrame", background=theme["bg"])
        self.style.configure("TLabel", background=theme["bg"], foreground=theme["fg"], padding=4)
        self.style.configure("Title.TLabel", font=self.fonts["title"], foreground=theme["primary"])
        self.style.configure("Header.TLabel", font=self.fonts["heading"], foreground=theme["secondary"])

        self.style.configure("TButton", padding=6, font=self.fonts["body"], relief="flat", borderwidth=1)
        self.style.map("TButton",
            background=[("active", theme["bg_selected"]), ("!disabled", theme["bg_input"])],
            foreground=[("!disabled", theme["fg"])],
            bordercolor=[("!disabled", theme["border"])])
        self.style.configure("Primary.TButton", background=theme["primary"], foreground=theme["bg"])
        self.style.map("Primary.TButton", background=[("active", theme["secondary"])])

        self.style.configure("TEntry", fieldbackground=theme["bg_input"], foreground=theme["fg_input"], insertcolor=theme["fg_input"])
        self.style.configure("TProgressbar", thickness=12, background=theme["primary"], troughcolor=theme["bg_input"])
        self.style.configure("TNotebook", background=theme["bg"], borderwidth=0)
        self.style.configure("TNotebook.Tab", background=theme["bg_input"], foreground=theme["fg"], padding=[8, 4], font=self.fonts["body"])
        self.style.map("TNotebook.Tab", background=[("selected", theme["bg"]), ("active", theme["bg_selected"])])

        self.style.configure("Toggle.TButton", anchor="w", padding=6, font=self.fonts["body"])
        self.style.layout("Toggle.TButton", [('Checkbutton.padding', {'sticky': 'nswe', 'children': [('Checkbutton.indicator', {'side': 'left', 'sticky': ''}), ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [('Checkbutton.label', {'sticky': 'nswe'})]})]})])
        
        self.style.configure(
            "TSpinbox",
            font=(self.fonts["title"][0], max(self.fonts["body"][1], 12)),
            fieldbackground=theme["bg_input"],
            foreground=theme["fg_input"],
            insertcolor=theme["fg_input"],
            padding=6
        )
        self.style.configure("Treeview",
                             background=theme["bg_input"],
                             foreground=theme["fg_input"],
                             fieldbackground=theme["bg_input"],
                             rowheight=int(25 * font_scale))
        self.style.map("Treeview", background=[("selected", theme["border"])])
        self.style.configure("Treeview.Heading",
                             background=theme["bg"],
                             foreground=theme["secondary"],
                             font=self.fonts["body"] + ("bold",))
        self.style.map("Treeview.Heading",
                       background=[("active", theme["bg_selected"])])

    def _create_widgets(self):
        self.main_frame = ttk.Frame(self, padding=8)
        self.main_frame.pack(fill="both", expand=True)
        self.main_frame.columnconfigure(0, weight=1)
        self.main_frame.rowconfigure(1, weight=1)

        header_frame = ttk.Frame(self.main_frame)
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header_frame.columnconfigure(0, weight=1)
        ttk.Label(header_frame, text="Patch Tool", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        
        toolbar_frame = ttk.Frame(header_frame)
        toolbar_frame.grid(row=0, column=1, sticky="e")
        
        font_inc_btn = ttk.Button(toolbar_frame, text="A+", width=3, command=lambda: self.scale_font(1.1))
        font_inc_btn.pack(side="left", padx=2)
        ToolTip(font_inc_btn, "Increase Font Size (Ctrl+Plus)")
        
        font_dec_btn = ttk.Button(toolbar_frame, text="A-", width=3, command=lambda: self.scale_font(0.9))
        font_dec_btn.pack(side="left", padx=2)
        ToolTip(font_dec_btn, "Decrease Font Size (Ctrl+Minus)")

        self.notebook = ttk.Notebook(self.main_frame)
        self.notebook.grid(row=1, column=0, sticky="nsew", pady=8)

        self.creator_tab = self._create_creator_tab(self.notebook)
        self.applier_tab = self._create_applier_tab(self.notebook)
        self.notebook.add(self.creator_tab, text="Create Patch")
        self.notebook.add(self.applier_tab, text="Apply Patch")

        action_pane = CollapsiblePane(self.main_frame, text="Actions & Progress")
        action_pane.grid(row=2, column=0, sticky="ew", pady=8)
        action_frame = action_pane.sub_frame
        action_frame.columnconfigure(0, weight=1)
        
        self.progress = ttk.Progressbar(action_frame, orient="horizontal", mode="determinate")
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))

        self.status_label = ttk.Label(action_frame, text="Idle")
        self.status_label.grid(row=1, column=0, columnspan=2, sticky="ew")

        button_frame = ttk.Frame(action_frame)
        button_frame.grid(row=2, column=0, columnspan=2, sticky="w", pady=4)
        self.start_btn = ttk.Button(button_frame, text="Start", command=self.start_task)
        self.start_btn.pack(side="left", padx=(0, 6))
        self.cancel_btn = ttk.Button(button_frame, text="Cancel", command=self.cancel_task, state="disabled")
        self.cancel_btn.pack(side="left")

        self.log_pane = CollapsiblePane(self.main_frame, text="Logs")
        self.log_pane.grid(row=3, column=0, sticky="nsew")
        self.main_frame.rowconfigure(3, weight=1)
        log_frame = self.log_pane.sub_frame
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_box = scrolledtext.ScrolledText(log_frame, state="disabled", wrap=tk.WORD, height=10,
            background=THEMES[self.current_theme]["bg_input"], foreground=THEMES[self.current_theme]["fg"])
        self.log_box.grid(row=0, column=0, sticky="nsew")
        self._setup_log_tags()

        self.bind("<Control-plus>", lambda e: self.scale_font(1.1))
        self.bind("<Control-minus>", lambda e: self.scale_font(0.9))
        self.bind("<Return>", lambda e: self.start_btn.invoke() if self.start_btn['state'] == 'normal' else None)

    def _create_creator_tab(self, parent):
        frame = ttk.Frame(parent, padding=8)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Create Patch", style="Header.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        self._create_path_selector(frame, 1, "Old Version Dir", self.vars["creator_old_dir"], "dir")
        self._create_path_selector(frame, 2, "New Version Dir", self.vars["creator_new_dir"], "dir")
        self._create_path_selector(frame, 3, "Patch Output File", self.vars["creator_patch_file"], "save")
        return frame

    def _create_applier_tab(self, parent):
        frame = ttk.Frame(parent, padding=8)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Apply Patch", style="Header.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        self._create_path_selector(frame, 1, "Game Directory", self.vars["applier_game_dir"], "dir")
        self._create_path_selector(frame, 2, "Patch Input File", self.vars["applier_patch_file"], "open")
        
        options_frame = ttk.Frame(frame)
        options_frame.grid(row=3, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Label(options_frame, text="RAM Limit (MB):").pack(side="left", padx=(0, 4))
        validate_cmd = (self.register(self._validate_numeric_input), '%P')
        ram_spinbox = ttk.Spinbox(options_frame, from_=64, to=8192, increment=64,
                                  textvariable=self.vars["applier_ram_limit"], width=8,
                                  validate="key", 
                                  validatecommand=validate_cmd)
        ram_spinbox.pack(side="left")
        ToolTip(ram_spinbox, "Max RAM for patch decompression. Higher values can be faster.")
        return frame

    def _create_path_selector(self, parent, row, label_text, var, dialog_type):
        ttk.Label(parent, text=label_text).grid(row=row, column=0, sticky="w", padx=(0, 8))
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        
        def browse():
            path = ""
            if dialog_type == "dir":
                path = filedialog.askdirectory(title=f"Select {label_text}")
            elif dialog_type == "open":
                path = filedialog.askopenfilename(title=f"Select {label_text}", filetypes=[("Patch Files", "*.tar.zst"), ("All Files", "*.*")])
            elif dialog_type == "save":
                path = filedialog.asksaveasfilename(title=f"Select {label_text}", defaultextension=".tar.zst", filetypes=[("Patch Files", "*.tar.zst")])
            if path:
                var.set(path)

        button = ttk.Button(parent, text="...", command=browse, width=4)
        button.grid(row=row, column=2, sticky="w", padx=(6, 0))
        ToolTip(entry, f"Path for {label_text}")
        ToolTip(button, f"Browse for {label_text}")

    def start_task(self):
        if self.is_running_task:
            return
            
        self.is_running_task = True
        self.cancel_event.clear()
        self._toggle_ui_state(running=True)
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")
        
        mode = self.notebook.tab(self.notebook.select(), "text")
        logic = PatchLogic(self.log_to_queue, self.progress_to_queue, self.status_to_queue, self.cancel_event)
        
        if mode == "Create Patch":
            args = (
                self.vars["creator_old_dir"].get(),
                self.vars["creator_new_dir"].get(),
                self.vars["creator_patch_file"].get()
            )
            if not all(args):
                messagebox.showerror("Error", "All paths must be specified for patch creation.")
                self.task_finished()
                return
            target_func = logic.create_patch
        elif mode == "Apply Patch":
            args = (
                self.vars["applier_game_dir"].get(),
                self.vars["applier_patch_file"].get(),
                self.vars["applier_ram_limit"].get()
            )
            if not all(args[:2]):
                messagebox.showerror("Error", "Game directory and patch file must be specified.")
                self.task_finished()
                return
            target_func = logic.apply_patch
        else:
            self.task_finished()
            return
            
        thread = threading.Thread(target=self.run_task_in_thread, args=(target_func, args), daemon=True)
        thread.start()

    def run_task_in_thread(self, target_func: Callable, args: Tuple):
        try:
            target_func(*args)
            self.log_to_queue("✅ Task completed successfully.", "success")
            messagebox.showinfo("Success", "The operation completed successfully.")
        except InterruptedError as e:
            self.log_to_queue(f"🟡 {e}", "warning")
            messagebox.showwarning("Cancelled", str(e))
        except Exception as e:
            self.log_to_queue(f"❌ An error occurred: {e}", "error")
            messagebox.showerror("Error", f"An unexpected error occurred:\n{e}")
        finally:
            self.log_queue.put(("finished", None, None))
            
    def task_finished(self):
        self.is_running_task = False
        self._toggle_ui_state(running=False)
        self.status_label.config(text="Idle")
        self.progress['value'] = 0

    def cancel_task(self):
        if self.is_running_task:
            if messagebox.askyesno("Cancel?", "Are you sure you want to cancel the current operation?"):
                self.cancel_event.set()
                self.status_label.config(text="Cancelling...")

    def _toggle_ui_state(self, running: bool):
        state = "disabled" if running else "normal"
        self.start_btn.config(state=state)
        self.cancel_btn.config(state="normal" if running else "disabled")
        for widget in self.creator_tab.winfo_children() + self.applier_tab.winfo_children():
             if isinstance(widget, (ttk.Entry, ttk.Button, ttk.Spinbox)):
                widget.config(state=state)

    def log_to_queue(self, msg: str, level: str):
        self.log_queue.put(("log", msg, level))

    def progress_to_queue(self, value: int, total: int):
        self.log_queue.put(("progress", value, total))
        
    def status_to_queue(self, msg: str):
        self.log_queue.put(("status", msg, None))

    def process_log_queue(self):
        try:
            while True:
                q_type, data1, data2 = self.log_queue.get_nowait()
                if q_type == "log":
                    self.log_box.config(state="normal")
                    self.log_box.insert("end", f"{data1}\n", data2)
                    self.log_box.see("end")
                    self.log_box.config(state="disabled")
                elif q_type == "progress":
                    self.progress['maximum'] = data2
                    self.progress['value'] = data1
                elif q_type == "status":
                    self.status_label.config(text=data1)
                elif q_type == "finished":
                    self.task_finished()
        except queue.Empty:
            pass
        self.after(100, self.process_log_queue)

    def _setup_log_tags(self):
        theme = THEMES[self.current_theme]
        self.log_box.tag_config("info", foreground=theme["info"])
        self.log_box.tag_config("success", foreground=theme["success"])
        self.log_box.tag_config("error", foreground=theme["error"])
        self.log_box.tag_config("warning", foreground=theme["warning"])
        self.log_box.tag_config("title", foreground=theme["primary"], font=self.fonts["body"] + ("bold",))

    def scale_font(self, factor: float):
        self.current_font_scale *= factor
        self.current_font_scale = max(0.7, min(2.0, self.current_font_scale))
        self.config["font_scale"] = self.current_font_scale
        self._setup_styles()
        self._setup_log_tags()

    def _load_plugins(self):
        plugin_dir = Path("plugins")
        if not plugin_dir.is_dir():
            return
        
        for plugin_file in plugin_dir.glob("*.py"):
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(plugin_file.stem, plugin_file)
                if spec is None:
                    self.log_to_queue(f"⚠️ Failed to load plugin {plugin_file.stem}: spec is None", "warning")
                    continue
                module = importlib.util.module_from_spec(spec)
                if spec.loader is not None:
                    spec.loader.exec_module(module)
                    
                    if hasattr(module, "PatchToolPlugin"):
                        plugin_instance = module.PatchToolPlugin(self)
                        plugin_instance.register()
                        self.plugins[plugin_file.stem] = plugin_instance
                        self.log_to_queue(f"🔌 Loaded plugin: {plugin_file.stem}", "info")
                else:
                    self.log_to_queue(f"⚠️ Failed to load plugin {plugin_file.stem}: spec.loader is None", "warning")
            except Exception as e:
                self.log_to_queue(f"⚠️ Failed to load plugin {plugin_file.stem}: {e}", "warning")

    def _validate_numeric_input(self, proposed_value: str) -> bool:
        """Allows only integers or an empty string."""
        if proposed_value == "":
            return True
        try:
            int(proposed_value)
            return True 
        except ValueError:
            return False

    def on_closing(self):
        if self.is_running_task:
            if not messagebox.askyesno("Exit?", "A task is running. Are you sure you want to exit?"):
                return

        for key, var in self.vars.items():
            self.config[key] = var.get()
        
        if "theme" in self.config:
            del self.config["theme"]
        
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=2)

        self.destroy()

def load_config(path: str) -> Dict[str, Any]:
    """Loads configuration from a JSON file, with defaults."""
    defaults = {
        "font_scale": 1.0,
        "applier_ram_limit": 512
    }
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                loaded_config = json.load(f)
                defaults.update(loaded_config)
                return defaults
        except (json.JSONDecodeError, IOError):
            return defaults
    return defaults

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Patch Tool")
    parser.add_argument("--font-scale", type=float, help="Override the startup font scale (e.g., 1.25 for 125%).")
    args = parser.parse_args()

    config = load_config(CONFIG_FILE)

    if args.font_scale:
        config["font_scale"] = args.font_scale

    app = MainApplication(config)
    app.mainloop()