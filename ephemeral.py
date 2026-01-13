import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw
import pyperclip
import subprocess
import threading
import keyboard
import sys
import re

# --- Configuration ---
HOTKEY = 'ctrl+alt+x'
APP_NAME = "Ephemeral"

# Map languages to the 'Clean Slate' Image on Docker Hub
LANG_MAP = {
    'python': {'image': 'python:3.10-slim', 'cmd': ['python', '-']},
    'node':   {'image': 'node:18-alpine',   'cmd': ['node', '-']},
    'bash':   {'image': 'alpine:latest',    'cmd': ['sh']},
    'ruby':   {'image': 'ruby:alpine',      'cmd': ['ruby']},
    
    # --- Science & Engineering ---
    # Anaconda includes numpy, pandas, scipy, matplotlib, etc.
    'science': {'image': 'continuumio/anaconda3', 'cmd': ['python', '-']},
    # GNU Octave for Matlab-like engineering calculations
    'octave':  {'image': 'gnuoctave/octave:latest', 'cmd': ['octave', '--no-gui', '--quiet', '--eval', '-']},
    
    # Aliases
    'py': 'python', 'js': 'node', 'sh': 'bash',
    'numpy': 'science', 'pandas': 'science', # Route specific libs to the science container
    'matlab': 'octave' # Open-source alternative
}

def create_icon_image():
    # A distinct 'Clean Slate' Icon (White/Blue)
    image = Image.new('RGB', (64, 64), (30, 30, 30))
    dc = ImageDraw.Draw(image)
    dc.rectangle((16, 16, 48, 48), fill=(255, 255, 255)) # White Paper
    dc.rectangle((20, 20, 44, 28), fill=(0, 120, 215))   # Blue Header
    return image

def get_clipboard():
    return pyperclip.paste()

def parse_codeblock(content):
    pattern = r"```(\w+)?\n(.*?)```"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        lang = match.group(1).lower() if match.group(1) else 'auto'
        return lang, match.group(2)
    return None, None

def run_logic(icon):
    content = get_clipboard()
    lang, code = parse_codeblock(content)

    if not code: return

    # Resolve alias (e.g., py -> python)
    config = None
    if lang in LANG_MAP:
        config = LANG_MAP[lang]
    elif lang in ['py', 'js', 'sh', 'numpy', 'pandas', 'matlab']: 
        if lang == 'py': config = LANG_MAP['python']
        elif lang == 'js': config = LANG_MAP['node']
        elif lang == 'sh': config = LANG_MAP['bash']
        elif lang in ['numpy', 'pandas']: config = LANG_MAP['science']
        elif lang == 'matlab': config = LANG_MAP['octave']
    
    if not config: return

    try:
        # --- THE CLEAN SLATE STRATEGY ---
        podman_cmd = [
            'podman', 'run', 
            '--rm',              # Auto-delete container (discard changes)
            '-i',                # Keep stdin open for the code pipe
            '--network', 'none', # Sandbox (Security)
            '--memory', '128m',
            config['image']
        ] + config['cmd']

        # Windows Magic: Suppress the ugly cmd.exe window
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        process = subprocess.Popen(
            podman_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            startupinfo=startupinfo
        )
        
        stdout, stderr = process.communicate(input=code)
        
        # Combine output
        result = f"{stdout}\nError:\n{stderr}" if stderr else stdout
        
        # Return to Clipboard
        pyperclip.copy(f"Result ({lang}):\n---\n{result}")
        icon.notify(f"Executed {lang}", title="Ephemeral")

    except Exception as e:
        icon.notify(f"Error: {str(e)}", title="Ephemeral Failed")

def on_hotkey(icon):
    threading.Thread(target=run_logic, args=(icon,)).start()

def setup(icon):
    icon.visible = True
    # Pass icon to the hotkey handler
    keyboard.add_hotkey(HOTKEY, lambda: on_hotkey(icon))

def quit_app(icon, item):
    icon.stop()
    sys.exit()

if __name__ == '__main__':
    image = create_icon_image()
    menu = (item('Quit', quit_app),)
    icon = pystray.Icon("Ephemeral", image, "Ephemeral", menu)
    icon.run(setup)
