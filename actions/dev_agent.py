# actions/dev_agent.py
# AI-powered development agent — plans, builds, and debugs full projects.
#
# Flow:
#   Describe project → Gemini plans file structure → Files written one by one
#   → requirements.txt written → VsCode opened → Entry point executed
#   → Error? → Identify file → Fix → Retry (up to MAX_FIX_ATTEMPTS)
#   → Speaks progress updates throughout
#
# Models:
#   Planning / Debugging : gemini-2.5-flash       (architecture, reasoning)
#   File writing         : gemini-2.5-flash-lite   (fast, cheap generation)

import shlex
import subprocess
import sys
import json
import re
import time
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
API_CONFIG_PATH  = BASE_DIR / "config" / "api_keys.json"
PROJECTS_DIR     = Path.home() / "Desktop" / "AtlasProjects"
MAX_FIX_ATTEMPTS = 4
MODEL_PLANNER    = "gemini-2.5-flash"
MODEL_WRITER     = "gemini-2.5-flash-lite"
_RATE_LIMIT_WAIT = 30       # seconds to wait before retrying after 429


# ─── Exception (defined FIRST so all functions can reference it) ───────────────

class RateLimitError(Exception):
    pass


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _get_model(model_name: str):
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())
    return genai.GenerativeModel(model_name)


def _clean(text: str) -> str:
    """Strip markdown fences from a model response."""
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _is_rate_limit(error: Exception) -> bool:
    return "429" in str(error) or "quota" in str(error).lower()


def _has_error(output: str) -> bool:
    if "timed out" in output.lower():
        return False
    signals = [
        "error", "exception", "traceback", "syntaxerror",
        "nameerror", "typeerror", "importerror", "failed",
    ]
    return any(s in output.lower() for s in signals)


def _identify_error_file(error_output: str, project_files: list) -> "str | None":
    for line in error_output.splitlines():
        for f in project_files:
            if Path(f).name in line or f in line:
                return f
    return None


def _safe_run_command(run_command: str) -> list:
    """Parse a run command safely, replacing 'python' with the actual interpreter."""
    try:
        parts = shlex.split(run_command)
    except ValueError:
        parts = run_command.split()
    if parts and parts[0].lower() in ("python", "python3"):
        parts[0] = sys.executable
    return parts


# ─── Planning ─────────────────────────────────────────────────────────────────

def _plan_project(description: str, language: str) -> dict:
    model  = _get_model(MODEL_PLANNER)
    prompt = f"""You are a senior software architect.
Plan the complete file structure for the following project.

Language   : {language}
Description: {description}

Return ONLY a valid JSON object with this exact structure — no markdown, no backticks:
{{
  "project_name": "short_snake_case_name",
  "entry_point": "main.py",
  "files": [
    {{"path": "main.py",           "description": "what this file does"}},
    {{"path": "utils/helpers.py",  "description": "what this file does"}}
  ],
  "run_command": "python main.py",
  "dependencies": ["package1", "package2"]
}}

Rules:
- Only include files that are truly necessary. Keep it simple.
- Entry point must be listed in files.
- Use relative paths only.

JSON:"""

    try:
        response = model.generate_content(prompt)
        raw      = _clean(response.text)
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON: {e}")
    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e))
        raise


# ─── File writing ─────────────────────────────────────────────────────────────

def _write_file(
    file_path: str,
    file_description: str,
    project_description: str,
    all_files: list,
    language: str,
    project_dir: Path,
) -> str:
    model     = _get_model(MODEL_WRITER)
    file_list = "\n".join(f"  - {f['path']}: {f['description']}" for f in all_files)

    prompt = f"""You are an expert {language} developer.
Write the code for ONE specific file in a project.

Project goal : {project_description}
All files    :
{file_list}

Write ONLY the file: {file_path}
Purpose      : {file_description}

Rules:
- Output ONLY the code. No explanation, no markdown, no backticks.
- Use relative imports where needed.
- Add helpful inline comments.
- Handle errors properly.
- Use modern best practices.

Code for {file_path}:"""

    try:
        response  = model.generate_content(prompt)
        code      = _clean(response.text)
        full_path = project_dir / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(code, encoding="utf-8")
        print(f"[DevAgent] ✅ Written: {file_path}")
        return code
    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e))
        raise


def _write_requirements(dependencies: list, project_dir: Path) -> None:
    """Write requirements.txt for Python projects."""
    if not dependencies:
        return
    req_path = project_dir / "requirements.txt"
    req_path.write_text("\n".join(dependencies) + "\n", encoding="utf-8")
    print(f"[DevAgent] 📄 requirements.txt written.")


def _write_readme(project_name: str, description: str, run_command: str, project_dir: Path) -> None:
    content = (
        f"# {project_name}\n\n"
        f"{description}\n\n"
        f"## Run\n\n"
        f"```\n{run_command}\n```\n"
    )
    (project_dir / "README.md").write_text(content, encoding="utf-8")


# ─── Dependency install ───────────────────────────────────────────────────────

def _install_dependencies(dependencies: list, project_dir: Path) -> str:
    if not dependencies:
        return "No dependencies."
    print(f"[DevAgent] 📦 Installing: {dependencies}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + dependencies,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120, cwd=str(project_dir),
        )
        if result.returncode == 0:
            return f"Installed: {', '.join(dependencies)}"
        return f"Install warning: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return "Dependency install timed out."
    except Exception as e:
        return f"Install error: {e}"


# ─── VSCode ───────────────────────────────────────────────────────────────────

def _open_vscode(project_dir: Path) -> bool:
    """Open project directory in VSCode. Tries several known locations."""
    candidates = [
        "code",
        rf"C:\Users\{Path.home().name}\AppData\Local\Programs\Microsoft VS Code\Code.exe",
        r"C:\Program Files\Microsoft VS Code\Code.exe",
    ]
    for cmd in candidates:
        try:
            # Use a string with shell=True so Windows resolves PATH correctly
            subprocess.Popen(
                f'"{cmd}" "{project_dir}"',
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(2)
            print(f"[DevAgent] 💻 VSCode opened: {project_dir}")
            return True
        except Exception:
            continue
    print("[DevAgent] ⚠️ VSCode not found — skipping.")
    return False


# ─── Run & syntax check ───────────────────────────────────────────────────────

def _syntax_check_python(project_dir: Path, files: list) -> "str | None":
    """
    Quick py_compile check on all .py files.
    Returns the first syntax error found, or None if all clean.
    """
    import py_compile
    for f in files:
        if not f.get("path", "").endswith(".py"):
            continue
        full = project_dir / f["path"]
        if not full.exists():
            continue
        try:
            py_compile.compile(str(full), doraise=True)
        except py_compile.PyCompileError as e:
            return str(e)
    return None


def _run_project(run_command: str, project_dir: Path, timeout: int = 30) -> str:
    print(f"[DevAgent] 🚀 Running: {run_command}")
    try:
        parts  = _safe_run_command(run_command)
        result = subprocess.run(
            parts,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(project_dir),
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        parts_out = []
        if out: parts_out.append(f"Output:\n{out}")
        if err: parts_out.append(f"Stderr:\n{err}")
        return "\n\n".join(parts_out) if parts_out else "Ran with no output."
    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s. (Long-running app may be fine.)"
    except FileNotFoundError as e:
        return f"Command not found: {e}"
    except Exception as e:
        return f"Run error: {e}"


# ─── Error fixing ─────────────────────────────────────────────────────────────

def _fix_file(
    file_path: str,
    current_code: str,
    error_output: str,
    project_description: str,
    all_files: list,
    language: str,
    project_dir: Path,
) -> str:
    model     = _get_model(MODEL_PLANNER)
    file_list = "\n".join(f"  - {f['path']}: {f['description']}" for f in all_files)

    prompt = f"""You are an expert {language} debugger.
Fix the file below. It caused an error when the project was run.

Project goal : {project_description}
All files    :
{file_list}

File to fix  : {file_path}

Error output :
{error_output[:3000]}

Current code :
{current_code}

Return ONLY the fixed code — no explanation, no markdown, no backticks.

Fixed code:"""

    try:
        response  = model.generate_content(prompt)
        fixed     = _clean(response.text)
        full_path = project_dir / file_path
        full_path.write_text(fixed, encoding="utf-8")
        print(f"[DevAgent] 🔧 Fixed: {file_path}")
        return fixed
    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e))
        raise


# ─── Main build loop ──────────────────────────────────────────────────────────

def _build_project(
    description: str,
    language: str,
    project_name: str,
    timeout: int,
    speak=None,
    player=None,
) -> str:

    def log(msg: str):
        print(f"[DevAgent] {msg}")
        if player:
            player.write_log(f"[DevAgent] {msg}")

    def say(msg: str):
        log(msg)
        if speak:
            speak(msg)

    # ── 1. Plan ────────────────────────────────────────────────────────────
    say("Planning the project structure, sir.")
    try:
        plan = _plan_project(description, language)
    except RateLimitError:
        msg = "Rate limit reached, sir. Please try again in a moment."
        say(msg); return msg
    except ValueError as e:
        msg = f"Planning failed: {e}"
        say(msg); return msg

    proj_name    = project_name or plan.get("project_name", "atlas_project")
    proj_name    = re.sub(r"[^\w\-]", "_", proj_name)
    project_dir  = PROJECTS_DIR / proj_name
    project_dir.mkdir(parents=True, exist_ok=True)

    files        = plan.get("files", [])
    entry_point  = plan.get("entry_point", "main.py")
    run_command  = plan.get("run_command", f"python {entry_point}")
    dependencies = plan.get("dependencies", [])

    log(f"Project: {proj_name} | Files: {len(files)} | Entry: {entry_point}")
    say(f"Plan ready. Writing {len(files)} file{'s' if len(files) != 1 else ''}, sir.")

    # ── 2. Write files ─────────────────────────────────────────────────────
    file_codes: dict = {}
    for i, file_info in enumerate(files, 1):
        file_path = file_info.get("path", "")
        file_desc = file_info.get("description", "")
        if not file_path:
            continue
        log(f"Writing {file_path} ({i}/{len(files)})...")
        try:
            code = _write_file(file_path, file_desc, description, files, language, project_dir)
            file_codes[file_path] = code
        except RateLimitError:
            log(f"Rate limit hit writing {file_path} — waiting {_RATE_LIMIT_WAIT}s...")
            time.sleep(_RATE_LIMIT_WAIT)
            try:
                code = _write_file(file_path, file_desc, description, files, language, project_dir)
                file_codes[file_path] = code
            except Exception as e:
                log(f"Retry failed for {file_path}: {e}")
        except Exception as e:
            log(f"Failed to write {file_path}: {e}")

    if not file_codes:
        msg = "I could not write any files for this project, sir."
        say(msg); return msg

    # ── 3. requirements.txt + README ───────────────────────────────────────
    if language.lower() == "python":
        _write_requirements(dependencies, project_dir)
    _write_readme(proj_name, description, run_command, project_dir)

    # ── 4. Install dependencies ────────────────────────────────────────────
    if dependencies:
        say(f"Installing {len(dependencies)} package{'s' if len(dependencies) != 1 else ''}, sir.")
        _install_dependencies(dependencies, project_dir)

    # ── 5. Open VSCode ─────────────────────────────────────────────────────
    _open_vscode(project_dir)

    # ── 6. Python syntax pre-check ─────────────────────────────────────────
    if language.lower() == "python":
        syntax_err = _syntax_check_python(project_dir, files)
        if syntax_err:
            log(f"Syntax error detected before run: {syntax_err}")

    # ── 7. Run + fix loop ──────────────────────────────────────────────────
    last_output = ""
    for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
        log(f"Running (attempt {attempt}/{MAX_FIX_ATTEMPTS})...")
        if attempt > 1:
            say(f"Attempt {attempt}, sir.")

        last_output = _run_project(run_command, project_dir, timeout)
        log(f"Output: {last_output[:150]}")

        if not _has_error(last_output):
            msg = (
                f"Project '{proj_name}' is working, sir. "
                f"Built in {attempt} attempt{'s' if attempt > 1 else ''}. "
                f"Saved at {project_dir}."
            )
            say(msg)
            return f"{msg}\n\nOutput:\n{last_output}"

        if attempt == MAX_FIX_ATTEMPTS:
            break

        error_file = _identify_error_file(last_output, list(file_codes.keys())) or entry_point
        log(f"Error detected in '{error_file}', fixing...")
        say(f"Found an error in {error_file}, fixing it now, sir.")

        try:
            fixed = _fix_file(
                error_file, file_codes.get(error_file, ""),
                last_output, description, files, language, project_dir,
            )
            file_codes[error_file] = fixed
        except RateLimitError:
            log(f"Rate limit during fix — waiting {_RATE_LIMIT_WAIT}s...")
            time.sleep(_RATE_LIMIT_WAIT)
        except Exception as e:
            log(f"Fix failed: {e}")

    msg = (
        f"I could not get '{proj_name}' fully working after {MAX_FIX_ATTEMPTS} attempts, sir. "
        f"The project is saved at {project_dir} — you can open it in VSCode and check manually."
    )
    say(msg)
    return f"{msg}\n\nLast error:\n{last_output[:500]}"


# ─── Public entry point ───────────────────────────────────────────────────────

def dev_agent(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    """
    Called from main.py.

    parameters:
        description  : What the project should do (required)
        language     : Programming language (default: python)
        project_name : Optional folder name (auto-generated if omitted)
        timeout      : Run timeout in seconds (default: 30)
    """
    p            = parameters or {}
    description  = p.get("description", "").strip()
    language     = p.get("language", "python").strip()
    project_name = p.get("project_name", "").strip()
    timeout      = int(p.get("timeout", 30))

    if not description:
        return "Please describe the project you want me to build, sir."

    return _build_project(
        description  = description,
        language     = language,
        project_name = project_name,
        timeout      = timeout,
        speak        = speak,
        player       = player,
    )