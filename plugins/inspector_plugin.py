import tkinter as tk
from tkinter import ttk, filedialog
import tarfile
import json
import zstandard as zstd
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from master import MainApplication

class PatchToolPlugin:
    def __init__(self, app: 'MainApplication'):
        self.app = app
        self.root = app

    def register(self):
        """Adds a new tab to the main notebook for the plugin's UI."""
        self.frame = ttk.Frame(self.app.notebook, padding=8)
        self.app.notebook.add(self.frame, text="🔍 Inspector")
        self._create_widgets()

    def _create_widgets(self):
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        control_frame = ttk.Frame(self.frame)
        control_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        control_frame.columnconfigure(1, weight=1)

        ttk.Label(control_frame, text="Patch File:").grid(row=0, column=0, sticky="w")
        self.path_var = tk.StringVar()
        ttk.Entry(control_frame, textvariable=self.path_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(control_frame, text="Browse...", command=self.load_patch).grid(row=0, column=2)

        self.tree = ttk.Treeview(self.frame, columns=("Size", "SHA256"), show="headings")
        self.tree.heading("Size", text="Size (bytes)")
        self.tree.heading("SHA256", text="SHA256 Hash")
        self.tree.column("Size", width=100, anchor="e")
        self.tree.column("SHA256", width=300)
        self.tree.grid(row=1, column=0, sticky="nsew")

    def load_patch(self):
        path = filedialog.askopenfilename(filetypes=[("Patch Files", "*.tar.zst")])
        if not path:
            return
        
        self.path_var.set(path)
        for i in self.tree.get_children():
            self.tree.delete(i)
        
        try:
            dctx = zstd.ZstdDecompressor()
            
            import io
            with open(path, "rb") as f_in:
                with dctx.stream_reader(f_in) as reader:
                    decompressed_data = reader.read()

                tar_stream = io.BytesIO(decompressed_data)
                
                with tarfile.open(fileobj=tar_stream) as tar:
                    manifest_info = tar.extractfile("manifest.json")
                    if not manifest_info:
                        raise FileNotFoundError("manifest.json not found in archive.")
                    manifest = json.load(manifest_info)
                    for rel_path, info in sorted(manifest.items()):
                        self.tree.insert("", "end", values=(info.get('size', 'N/A'), info.get('sha256', 'N/A')), text=rel_path)
        except Exception as e:
            self.app.log_to_queue(f"Inspector Error: {e}", "error")