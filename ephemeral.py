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

# --- Configuration ---
HOTKEY = 'ctrl+alt+x'
APP_NAME = "Ephemeral"
LAST_DETECTED_LANG = "python" # Default starting language, updates dynamically

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
    'r':       {'image': 'r-base:latest',          'cmd': ['R', '--slave', '-f', '/dev/stdin']},
    'julia':   {'image': 'julia:alpine',           'cmd': ['julia']},

    # --- Systems & Compiled (Compile-and-Run Chains) ---
    'c':       {'image': 'gcc:latest', 'cmd': ['sh', '-c', 'gcc -x c - -o /tmp/run && /tmp/run']},
    'cpp':     {'image': 'gcc:latest', 'cmd': ['sh', '-c', 'g++ -x c++ - -o /tmp/run && /tmp/run']},
    'fortran': {'image': 'gcc:latest', 'cmd': ['sh', '-c', 'gfortran -x f95 - -o /tmp/run && /tmp/run']},
    'rust':    {'image': 'rust:alpine', 'cmd': ['sh', '-c', 'rustc - -o /tmp/run && /tmp/run']},
    'go':      {'image': 'golang:alpine', 'cmd': ['go', 'run', '/dev/stdin']},

    # --- Hardware Description (HDL) ---
    'verilog': {'image': 'hdlc/iverilog', 'cmd': ['sh', '-c', 'cat > /tmp/run.v && iverilog /tmp/run.v -o /tmp/out && vvp /tmp/out']},

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
    """
    Attempts to detect language via Markdown or Shebang.
    Returns (lang, code).
    If no detection, returns (None, None).
    """
    if not content or not content.strip():
        return None, None

    # Strategy 1: Markdown
    # Capture [^\s]+ instead of \w+ to allow 'python:2.7' or 'node-14'
    pattern = r"```([^\s\n]+)?\s*\n(.*?)```"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        # If group(1) is None (just ```), we return None for lang so we can prompt user
        lang = match.group(1).lower() if match.group(1) else None
        return lang, match.group(2)

    # Strategy 2: Shebang
    first_line = content.strip().splitlines()[0]
    if first_line.startswith("#!"):
        lower_line = first_line.lower()
        for key in LANG_MAP:
            if key in lower_line:
                return key, content
    
    return None, None

def prompt_user_for_language(default_lang):
    """
    Launches a visible CMD window asking the user to input a language.
    Returns the entered string, or the default if empty.
    """
    fd_out, path_out = tempfile.mkstemp(suffix='.txt')
    os.close(fd_out)
    
    fd_bat, path_bat = tempfile.mkstemp(suffix='.bat')
    os.close(fd_bat)
    
    detected_lang = None
    
    try:
        # Create a transient batch file for the input UI
        with open(path_bat, 'w') as f:
            f.write('@echo off\n')
            # Using default system colors
            f.write('title Ephemeral Input\n')
            f.write('cls\n')
            f.write('echo.\n')
            f.write('echo  --------------------------------------------------\n')
            f.write('echo   No language detected in clipboard.\n')
            f.write('echo  --------------------------------------------------\n')
            f.write('echo.\n')
            f.write(f'set /p "lang= Enter Language [Default: {default_lang}]: "\n')
            f.write(f'if "%lang%"=="" set lang={default_lang}\n')
            f.write(f'echo %lang%> "{path_out}"\n')
        
        # Run it in a new visible console (blocking)
        subprocess.run(path_bat, creationflags=subprocess.CREATE_NEW_CONSOLE)
        
        # Read the result
        if os.path.exists(path_out):
            with open(path_out, 'r') as f:
                val = f.read().strip()
                if val: detected_lang = val
            
    except Exception as e:
        print(f"Input error: {e}")
        return None
    finally:
        if os.path.exists(path_out): os.remove(path_out)
        if os.path.exists(path_bat): os.remove(path_bat)
        
    return detected_lang

def resolve_runtime_config(lang):
    """
    Resolves the language string (e.g., 'python:2.7', 'js') to a docker config.
    Handles aliases and version overrides.
    """
    base_lang = lang
    version = None

    # 1. Parse Version Tag (python:2.7, python-2.7, node14)
    # Match: (name) + optional ( separator + number/dots )
    match = re.match(r"^([a-z]+)(?:[:\-](\d+(?:\.\d+)*))?$", lang)
    if match:
        base_lang = match.group(1)
        version = match.group(2) # Might be None

    # 2. Resolve Alias (py -> python)
    if base_lang in LANG_MAP:
        resolved = LANG_MAP[base_lang]
        if isinstance(resolved, str):
            # It's an alias string
            base_lang = resolved
            # Check for double alias
            if base_lang in LANG_MAP and isinstance(LANG_MAP[base_lang], str):
                 base_lang = LANG_MAP[base_lang]
        elif isinstance(resolved, dict):
            pass
    
    # 3. Get Base Config
    config = None
    if base_lang in LANG_MAP and isinstance(LANG_MAP[base_lang], dict):
        config = LANG_MAP[base_lang].copy()
    
    # 4. Fallback or Apply Version
    if config:
        if version:
            original_image = config['image']
            repo = original_image.split(':')[0]
            config['image'] = f"{repo}:{version}"
            
    else:
        # Wildcard Fallback
        image_tag = f"{lang}" if ':' in lang else f"{lang}:latest"
        config = {
            'image': image_tag,
            'cmd': [base_lang, '-'] # Best guess: cmd matches language name
        }

    return config

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
    global LAST_DETECTED_LANG
    content = get_clipboard()
    
    # 1. Try explicit detection
    lang, code = parse_codeblock(content)

    # 2. If explicit detection failed (lang is None), but we have text...
    if not lang:
        if content and content.strip():
            # Assume content is raw code
            code = content
            # Trigger Manual Prompt
            user_input = prompt_user_for_language(LAST_DETECTED_LANG)
            if user_input:
                lang = user_input
            else:
                # User likely closed the window or hit cancel
                icon.notify("Execution cancelled.", title="Ephemeral")
                return
        else:
             icon.notify("Clipboard is empty.", title="Ephemeral Error")
             return

    # 3. Update 'Memory' (Last Detected/Used)
    # We update this regardless of whether it came from markdown or manual input
    LAST_DETECTED_LANG = lang

    # 4. Resolve Config
    config = resolve_runtime_config(lang)
    
    icon.notify(f"Launching {lang}...", title="Ephemeral Status")

    image_name = config['image']
    is_cached = check_image_exists(image_name)

    # Force visible run if not cached (so we see download)
    if not is_cached:
        run_visible_console_logic(icon, config, code, lang)
    else:
        run_hidden_logic(icon, config, code, lang)

def run_visible_console_logic(icon, config, code, lang):
    fd_code, path_code = tempfile.mkstemp(suffix='.src')
    fd_out, path_out = tempfile.mkstemp(suffix='.res')
    os.close(fd_code)
    os.close(fd_out)

    try:
        # SAFETY: Append newline if missing to prevent interpreter EOF errors
        if not code.endswith('\n'):
            code += '\n'

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
        
        # SAFETY: Append newline here as well
        if not code.endswith('\n'):
            code += '\n'

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
