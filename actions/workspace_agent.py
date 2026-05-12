# actions/workspace_agent.py
# Advanced workspace analysis tool for ATLAS — making it a true Claude Cowork alternative.
# It scans a directory, reads important files, and provides a comprehensive project overview.

import os
import json
import sys
from pathlib import Path

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

def _get_model():
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())
    return genai.GenerativeModel("gemini-1.5-flash") # Use flash for fast analysis

def analyze_workspace(path: str = ".", deep: bool = False) -> str:
    """
    Analyzes a project workspace. 
    1. Scans file structure
    2. Identifies project type (Python, Node, etc.)
    3. Reads key files (README, package.json, requirements.txt, main files)
    4. Provides a summary of what the project does and how it's structured.
    """
    try:
        root = Path(path).expanduser().resolve()
        if not root.exists():
            return f"Workspace path not found: {path}"
        
        # 1. Scan structure (limited depth to avoid overload)
        structure = []
        key_contents = {}
        
        important_names = [
            "readme.md", "package.json", "requirements.txt", 
            "main.py", "app.py", "index.js", "tsconfig.json",
            "dockerfile", ".env.example", "go.mod", "cargo.toml"
        ]
        
        for p in root.rglob("*"):
            # Skip hidden and common ignored dirs
            if any(part.startswith(".") for part in p.parts) or "node_modules" in p.parts or "__pycache__" in p.parts or "venv" in p.parts:
                continue
                
            rel = p.relative_to(root)
            if p.is_dir():
                if len(rel.parts) <= 2: # Only show top 2 levels of dirs in structure
                    structure.append(f"📁 {rel}/")
            else:
                if len(rel.parts) <= 3:
                    structure.append(f"📄 {rel}")
                
                # Read key files
                if p.name.lower() in important_names and len(key_contents) < 5:
                    try:
                        content = p.read_text(encoding="utf-8", errors="ignore")[:2000]
                        key_contents[str(rel)] = content
                    except: pass

        struct_str = "\n".join(structure[:50])
        if len(structure) > 50:
            struct_str += f"\n... and {len(structure)-50} more items."

        context = f"Project Path: {root}\n\nStructure:\n{struct_str}\n\n"
        for fname, fcontent in key_contents.items():
            context += f"--- {fname} ---\n{fcontent}\n\n"

        # 2. Ask Gemini to summarize
        model = _get_model()
        prompt = f"""You are an expert software architect analyzing a new project.
Based on the file structure and contents below, provide a concise summary of:
1. What this project is (technology stack, purpose).
2. Key entry points and important files.
3. How to run or build it.

Context:
{context}

Summary:"""

        response = model.generate_content(prompt)
        summary = response.text.strip()
        
        return f"Workspace Analysis for: {root.name}\n\n{summary}"

    except Exception as e:
        return f"Workspace analysis failed: {e}"

def workspace_agent(parameters: dict, player=None, **kwargs) -> str:
    path = parameters.get("path", ".")
    deep = parameters.get("deep", False)
    
    if player:
        player.write_log(f"[Workspace] Analyzing {path}...")
        
    return analyze_workspace(path, deep)
