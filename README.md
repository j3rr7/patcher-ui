# Introduction 

patcher-ui is a lightweight desktop application for creating and applying file patches in standard diff/patch formats. 

Quick CLI usage
```sh
# Launch script
python main.py

# Launch with custom UI font scale to 125%
python main.py --font-scale 1.25
```

Or download a binary from the releases.

# Feature

- Create unified diffs for files or entire directories (recursive), producing standard diff/patch.
- Apply patches to files or directories.
- Backup & restore — automatically create backups before applying patches.
- Binary file support — detect and handle binary diffs (copy/replace behavior with metadata).
- Patch metadata — embed author, timestamp, and description into generated patches.
- Batch operations — create or apply multiple patches in a single operation.
- Logging & reporting — detailed operation logs and a summary report after apply/create operations.
- Plugin/extension.

# Plugin

## File and Folder Structure
```txt
patcher-ui
|-- main.py
|-- plugins/
|   |-- my_first_plugin.py
|   `-- inspector_plugin.py
```

## The Basic Plugin Structure
A valid plugin needs two key methods:

- `__init__(self, app):` The constructor for your class. It receives the main application instance (app) as an argument. This is crucial for interacting with the main GUI, like adding new widgets or accessing its variables.

- `register(self):` This method is called right after your plugin is initialized. You should use this method to add UI elements or set up any other functionality your plugin provides.

## Simple Example
```py
import tkinter as tk
from tkinter import ttk, messagebox

class PatchToolPlugin:
    """
    A simple example plugin that adds a new tab to the notebook.
    """
    def __init__(self, app):
        """
        The constructor is called by the main application.

        Args:
            app: The instance of the MainApplication.
        """
        self.app = app
        self.app.log_to_queue("Initializing Hello Plugin...", "info")

    def register(self):
        """
        Called by the main app to set up the plugin's UI and functionality.
        """
        # Create a new frame for our tab
        plugin_frame = ttk.Frame(self.app.notebook, padding=10)

        # 2. Add the new frame as a tab in the main app's notebook
        self.app.notebook.add(plugin_frame, text="Hello Plugin")

        # 3. Add some widgets to our new tab
        label = ttk.Label(
            plugin_frame,
            text="This is a tab from the Hello World plugin!",
            style="Header.TLabel"
        )
        label.pack(pady=10)

        # Create a button that shows a message box when clicked
        my_button = ttk.Button(
            plugin_frame,
            text="Click Me!",
            command=self.show_message
        )
        my_button.pack(pady=20)

        self.app.log_to_queue("Hello Plugin registered successfully.", "success")

    def show_message(self):
        """
        A simple function to show a message box.
        """
        messagebox.showinfo(
            "Hello from Plugin!",
            "You clicked the button in the 'Hello Plugin' tab!"
        )

```

# License
```
This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or distribute this software, either in source code form or as a compiled binary, for any purpose, commercial or non-commercial, and by any means.

In jurisdictions that recognize copyright laws, the author or authors of this software dedicate any and all copyright interest in the software to the public domain. We make this dedication for the benefit of the public at large and to the detriment of our heirs and successors. We intend this dedication to be an overt act of relinquishment in perpetuity of all present and future rights to this software under copyright law.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

For more information, please refer to <https://unlicense.org/>
```