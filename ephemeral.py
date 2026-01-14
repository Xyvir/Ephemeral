import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw
import pyperclip
import subprocess
import threading
import keyboard
import sys
import re
import os
import tempfile
import time

# --- Pygments Import (with fail-safe) ---
try:
    from pygments.lexers import guess_lexer
    from pygments.util import ClassNotFound
    HAS_PYGMENTS = True
except ImportError:
    HAS_PYGMENTS = False

# --- Configuration ---
HOTKEY = 'ctrl+alt+x'
APP_NAME = "Ephemeral"

# Map languages to the 'Clean Slate' Image on Docker Hub
LANG_MAP = {
    # --- Standard Interpreted ---
    'python': {'image': 'python:3.10-slim', 'cmd': ['python', '-']},
    'node':   {'image': 'node:18-alpine',   'cmd': ['node', '-']},
    'bash':   {'image': 'alpine:latest',    'cmd': ['sh']},
    'ruby':   {'image': 'ruby:alpine',      'cmd': ['ruby']},
    
    # --- Science & Data ---
    'science': {'image': 'continuumio/anaconda3', 'cmd': ['python', '-']},
    'octave':  {'image': 'gnuoctave/octave:latest', 'cmd': ['octave', '--no-gui', '--quiet']},
    'r':       {'image': 'r-base:latest',          'cmd': ['Rscript', '-']},
    'julia':   {'image': 'julia:alpine',           'cmd': ['julia']},

    # --- Systems & Compiled (Compile-and-Run Chains) ---
    # C: GCC compiler -> pipe source -> output to /tmp -> run
    'c':       {'image': 'gcc:latest', 'cmd': ['sh', '-c', 'gcc -x c - -o /tmp/run && /tmp/run']},
    # C++: G++ compiler
    'cpp':     {'image': 'gcc:latest', 'cmd': ['sh', '-c', 'g++ -x c++ - -o /tmp/run && /tmp/run']},
    # Fortran: GFortran
    'fortran': {'image': 'gcc:latest', 'cmd': ['sh', '-c', 'gfortran -x f95 - -o /tmp/run && /tmp/run']},
    # Rust: RustC
    'rust':    {'image': 'rust:alpine', 'cmd': ['sh', '-c', 'rustc - -o /tmp/run && /tmp/run']},
    # Go: Go Run (Native support for stdin)
    'go':      {'image': 'golang:alpine', 'cmd': ['go', 'run', '/dev/stdin']},

    # --- Hardware Description (HDL) ---
    # Verilog: Icarus Verilog -> Compile to /tmp/out -> Run with vvp
    'verilog': {'image': 'hdlc/iverilog', 'cmd': ['sh', '-c', 'cat > /tmp/run.v && iverilog /tmp/run.v -o /tmp/out && vvp /tmp/out']},
    # VHDL is excluded for now as it requires strict entity-filename matching which is hard for snippets.

    # --- Functional & Scripting ---
    'haskell': {'image': 'haskell:slim', 'cmd': ['runghc']},
    'lua':     {'image': 'lua:alpine',     'cmd': ['lua', '-']},
    'perl':    {'image': 'perl:slim',      'cmd': ['perl', '-']},
    'php':     {'image': 'php:alpine',     'cmd': ['php']},

    # --- Windows-like Shells ---
    'pwsh':    {'image': 'mcr.microsoft.com/powershell', 'cmd': ['pwsh', '-Command', '-']},
    
    # --- Aliases ---
    'py': 'python', 'js': 'node', 'sh': 'bash',
    'numpy': 'science', 'pandas': 'science',
    'matlab': 'octave',
    'powershell': 'pwsh', 'ps1': 'pwsh', 'cmd': 'pwsh', 'batch': 'pwsh',
    'R': 'r',
    'golang': 'go', 'cc': 'c', 'c++': 'cpp',
    'f90': 'fortran', 'f95': 'fortran'
}

def create_icon_image():
    image = Image.new('RGB', (64, 64), (30, 30, 30))
    dc = ImageDraw.Draw(image)
    dc.rectangle((16, 16, 48, 48), fill=(255, 255, 255)) 
    dc.rectangle((20, 20, 44, 28), fill=(0, 120, 215))   
    return image

def get_clipboard():
    return pyperclip.paste()

def parse_codeblock(content):
    if not content or not content.strip():
        return None, None

    # Strategy 1: Markdown
    pattern = r"```(\w+)?\s*\n(.*?)```"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        lang = match.group(1).lower() if match.group(1) else 'auto'
        return lang, match.group(2)

    # Strategy 2: Shebang
    first_line = content.strip().splitlines()[0]
    if first_line.startswith("#!"):
        lower_line = first_line.lower()
        # Scan keys in LANG_MAP to match shebang (simple substring check)
        for key in LANG_MAP:
            if key in lower_line:
                return key, content
    
    # Strategy 3: Pygments (The "Smart" AI-lite detection)
    if HAS_PYGMENTS:
        try:
            lexer = guess_lexer(content)
            if lexer.aliases:
                pyg_lang = lexer.aliases[0].lower()
                if pyg_lang != 'text':
                    return pyg_lang, content
        except ClassNotFound:
            pass

    return None, None

# --- Podman Lifecycle Management ---

def check_podman_alive():
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        subprocess.check_call(['podman', 'info'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startupinfo)
        return True
    except:
        return False

def check_image_exists(image_name):
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        subprocess.check_call(['podman', 'image', 'exists', image_name], startupinfo=startupinfo)
        return True
    except:
        return False

def ensure_podman_running(icon):
    if check_podman_alive(): return

    icon.notify("Podman is not running. Attempting to start...", title="Ephemeral Init")
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    
    try:
        subprocess.check_call(['podman', 'machine', 'start'], startupinfo=startupinfo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        icon.notify("Podman machine started successfully.", title="Ephemeral Init")
    except subprocess.CalledProcessError:
        icon.notify("Start failed. Initializing new machine...", title="Ephemeral Init")
        try:
            subprocess.check_call(['podman', 'machine', 'init'], startupinfo=startupinfo)
            subprocess.check_call(['podman', 'machine', 'start'], startupinfo=startupinfo)
            icon.notify("Podman machine initialized and started.", title="Ephemeral Init")
        except Exception as e:
            icon.notify(f"Could not start Podman: {e}", title="Ephemeral Fatal Error")

def stop_podman_machine(icon):
    icon.notify("Stopping Podman machine...", title="Ephemeral Shutdown")
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    try:
        subprocess.run(['podman', 'machine', 'stop'], startupinfo=startupinfo)
    except Exception as e:
        print(f"Error stopping podman: {e}")

# --- Helper for Post-Mortem Debugging ---
def show_post_mortem_error(error_text):
    try:
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as tmp:
            tmp.write("--- EPHEMERAL EXECUTION ERROR ---\n\n")
            tmp.write(error_text)
            tmp_path = tmp.name
        
        subprocess.Popen(
            f'start cmd /K "type "{tmp_path}" && echo. && echo. && echo [Ephemeral Debug] Window persisted due to error. Close to dismiss."', 
            shell=True
        )
    except Exception as e:
        print(f"Failed to show error window: {e}")

# --- Execution Logic ---

def run_logic(icon):
    content = get_clipboard()
    lang, code = parse_codeblock(content)

    if not code:
        icon.notify("No recognized code found (Markdown, Shebang, or Pygments).", title="Ephemeral Error")
        return

    config = None
    is_fallback = False

    # 1. Check Explicit Map
    if lang in LANG_MAP:
        val = LANG_MAP[lang]
        if isinstance(val, str):
            if val in LANG_MAP:
                config = LANG_MAP[val]
                # Double resolve for deeper aliases
                if isinstance(config, str) and config in LANG_MAP:
                    config = LANG_MAP[config]
        else:
            config = val
    
    # 2. Wildcard Fallback (Best Effort)
    if not config:
        icon.notify(f"Language '{lang}' unknown. Attempting generic fallback...", title="Ephemeral Warning")
        config = {
            'image': f'{lang}:latest',
            'cmd': [lang, '-']
        }
        is_fallback = True

    icon.notify(f"Launching {lang}...", title="Ephemeral Status")

    image_name = config['image']
    is_cached = check_image_exists(image_name)

    # Force visible run if not cached OR if we are guessing (fallback)
    if not is_cached or is_fallback:
        run_visible_console_logic(icon, config, code, lang)
    else:
        run_hidden_logic(icon, config, code, lang)

def run_visible_console_logic(icon, config, code, lang):
    fd_code, path_code = tempfile.mkstemp(suffix='.src')
    fd_out, path_out = tempfile.mkstemp(suffix='.res')
    os.close(fd_code)
    os.close(fd_out)

    try:
        with open(path_code, 'w', encoding='utf-8') as f:
            f.write(code)

        podman_run_base = [
            'podman', 'run', '--rm', '-i', '--network', 'none', '--memory', '128m',
            config['image']
        ] + config['cmd']
        
        podman_run_str = " ".join(f'"{arg}"' if " " in arg else arg for arg in podman_run_base)
        image_name = config['image']
        
        preamble = f"echo [Ephemeral] Image {image_name} not found or Fallback Mode. Downloading..."
        
        cmd_line = (
            f'cmd /C "{preamble} '
            f'&& podman pull {image_name} '
            f'&& echo. && echo [Ephemeral] Executing Code... '
            f'&& (type "{path_code}" | {podman_run_str} > "{path_out}") || pause"'
        )

        process = subprocess.Popen(cmd_line, creationflags=subprocess.CREATE_NEW_CONSOLE)
        process.wait()

        if os.path.exists(path_out):
            with open(path_out, 'r', encoding='utf-8', errors='replace') as f:
                result = f.read()
            
            if result.strip():
                pyperclip.copy(f"Result ({lang}):\n---\n{result}")
                icon.notify("Success", title="Ephemeral")

    except Exception as e:
        icon.notify(f"System Error: {str(e)}", title="Ephemeral Failed")
    finally:
        if os.path.exists(path_code): os.remove(path_code)
        if os.path.exists(path_out): os.remove(path_out)

def run_hidden_logic(icon, config, code, lang):
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
        podman_cmd = [
            'podman', 'run', '--rm', '-i', '--network', 'none', '--memory', '128m',
            config['image']
        ] + config['cmd']

        process = subprocess.Popen(
            podman_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, startupinfo=startupinfo
        )
        
        stdout, stderr = process.communicate(input=code)
        
        if process.returncode == 0:
            result = stdout
            pyperclip.copy(f"Result ({lang}):\n---\n{result}")
            icon.notify("Success", title="Ephemeral")
        else:
            full_error = f"Exit Code: {process.returncode}\n\nSTDERR:\n{stderr}\n\nSTDOUT:\n{stdout}"
            show_post_mortem_error(full_error)
            icon.notify("Execution Failed. Debug window opened.", title="Ephemeral Error")

    except Exception as e:
        show_post_mortem_error(f"System Exception:\n{str(e)}")
        icon.notify("Critical System Error", title="Ephemeral Failed")

def on_hotkey(icon):
    threading.Thread(target=run_logic, args=(icon,)).start()

def setup(icon):
    icon.visible = True
    def init_sequence():
        ensure_podman_running(icon)
    threading.Thread(target=init_sequence).start()
    keyboard.add_hotkey(HOTKEY, lambda: on_hotkey(icon))

def quit_app(icon, item):
    stop_podman_machine(icon)
    icon.stop()
    sys.exit()

if __name__ == '__main__':
    image = create_icon_image()
    menu = (
        item('Run Clipboard', lambda icon, item: on_hotkey(icon), default=True),
        item('Quit', quit_app)
    )
    icon = pystray.Icon("Ephemeral", image, "Ephemeral", menu)
    icon.run(setup)
