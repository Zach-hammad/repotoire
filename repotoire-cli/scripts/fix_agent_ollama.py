#!/usr/bin/env python3
"""
Repotoire Fix Agent - Ollama Edition

Uses local Ollama models to fix code findings.
No API key needed - runs 100% locally.

Usage:
    python fix_agent_ollama.py --finding-json '<json>' --repo-path /path/to/repo [--model codellama]
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("❌ requests not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")


def check_ollama() -> bool:
    """Check if Ollama is running."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def get_models() -> list[str]:
    """Get available Ollama models."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if resp.status_code == 200:
            return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        pass
    return []


def read_file(repo_path: str, file_path: str) -> str | None:
    """Read a file from the repo."""
    full_path = Path(repo_path) / file_path
    if full_path.exists():
        return full_path.read_text()
    return None


def generate(model: str, prompt: str, system: str = "") -> str:
    """Generate a response from Ollama."""
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {
            "temperature": 0.1,  # Low temp for code
            "num_predict": 4096,
        }
    }
    
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=300,  # 5 min timeout for large responses
        )
        if resp.status_code == 200:
            return resp.json().get("response", "")
        else:
            print(f"❌ Ollama error: {resp.status_code}", file=sys.stderr)
            return ""
    except Exception as e:
        print(f"❌ Ollama request failed: {e}", file=sys.stderr)
        return ""


def run_command(cmd: str, cwd: str) -> tuple[int, str]:
    """Run a shell command."""
    print(f"🔧 Running: {cmd}", flush=True)
    result = subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        print(f"   Exit code: {result.returncode}", flush=True)
    return result.returncode, output


def fix_finding(finding: dict, repo_path: str, model: str) -> None:
    """Use Ollama to fix a finding."""
    
    file_path = finding.get("affected_files", ["unknown"])[0] if finding.get("affected_files") else "unknown"
    line_start = finding.get("line_start", 1)
    line_end = finding.get("line_end", line_start)
    finding_index = finding.get("index", 0)
    
    print(f"🚀 Starting Ollama agent ({model})", flush=True)
    print(f"📁 Repository: {repo_path}", flush=True)
    print(f"📄 File: {file_path}:{line_start}-{line_end}", flush=True)
    print("-" * 60, flush=True)
    
    # Read the file
    print(f"💭 Reading {file_path}...", flush=True)
    content = read_file(repo_path, file_path)
    if not content:
        print(f"❌ Could not read file: {file_path}", flush=True)
        return
    
    lines = content.split('\n')
    
    # Get context around the issue (±10 lines)
    start = max(0, line_start - 11)
    end = min(len(lines), line_end + 10)
    context_lines = lines[start:end]
    context_with_numbers = "\n".join(
        f"{start + i + 1:4} | {line}" 
        for i, line in enumerate(context_lines)
    )
    
    # Build the prompt
    system_prompt = """You are a code fixing assistant. You will be given a code issue and must provide a fix.

IMPORTANT RULES:
1. Output ONLY the fixed code lines, nothing else
2. Do NOT include line numbers in your output
3. Do NOT include markdown code fences
4. Do NOT include explanations
5. Output the exact replacement for lines {start} to {end}
6. Preserve the original indentation exactly"""

    user_prompt = f"""Fix this code issue:

**Issue:** {finding.get("title", "Unknown")}
**Severity:** {finding.get("severity", "Unknown")}
**File:** {file_path}
**Lines:** {line_start}-{line_end}

**Description:**
{finding.get("description", "No description")}

**Suggested Fix:**
{finding.get("suggested_fix", "Apply appropriate fix")}

**Code Context (lines {start+1}-{end}):**
```
{context_with_numbers}
```

Output ONLY the fixed code for lines {line_start} to {line_end}. No explanations."""

    print(f"💭 Asking {model} for fix...", flush=True)
    fixed_code = generate(model, user_prompt, system_prompt.format(start=line_start, end=line_end))
    
    if not fixed_code.strip():
        print("❌ No fix generated", flush=True)
        return
    
    # Clean up the response
    fixed_code = fixed_code.strip()
    if fixed_code.startswith("```"):
        # Remove markdown fences if present
        fixed_code = "\n".join(fixed_code.split("\n")[1:])
        if fixed_code.endswith("```"):
            fixed_code = "\n".join(fixed_code.split("\n")[:-1])
    
    print(f"📋 Generated fix:", flush=True)
    for line in fixed_code.split('\n')[:10]:
        print(f"   {line}", flush=True)
    if fixed_code.count('\n') > 10:
        print(f"   ... ({fixed_code.count(chr(10)) - 10} more lines)", flush=True)
    
    # Apply the fix
    print(f"💭 Applying fix to {file_path}...", flush=True)
    new_lines = lines[:line_start - 1] + fixed_code.split('\n') + lines[line_end:]
    new_content = '\n'.join(new_lines)
    
    full_path = Path(repo_path) / file_path
    full_path.write_text(new_content)
    print(f"✅ Fixed {file_path}", flush=True)
    
    # Git operations
    print("-" * 60, flush=True)
    branch_name = f"fix/finding-{finding_index}"
    
    # Check if we're on main/master
    code, current_branch = run_command("git rev-parse --abbrev-ref HEAD", repo_path)
    current_branch = current_branch.strip()
    
    # Create branch
    code, _ = run_command(f"git checkout -b {branch_name}", repo_path)
    if code != 0:
        # Branch might exist, try switching
        run_command(f"git checkout {branch_name}", repo_path)
    
    # Stage and commit
    run_command(f"git add {file_path}", repo_path)
    commit_msg = f"fix: {finding.get('title', 'code issue')}"
    code, _ = run_command(f'git commit -m "{commit_msg}"', repo_path)
    
    if code == 0:
        print(f"✅ Committed: {commit_msg}", flush=True)
        
        # Try to push
        code, output = run_command(f"git push -u origin {branch_name}", repo_path)
        if code == 0:
            print(f"✅ Pushed to origin/{branch_name}", flush=True)
            
            # Try to create PR
            code, output = run_command(
                f'gh pr create --title "{commit_msg}" --body "Fixes finding #{finding_index}\n\n{finding.get("description", "")}"',
                repo_path
            )
            if code == 0:
                print(f"✅ Created PR!", flush=True)
            else:
                print(f"⚠️ Could not create PR (gh not configured?)", flush=True)
        else:
            print(f"⚠️ Could not push (no remote access?)", flush=True)
    else:
        print(f"⚠️ Nothing to commit (no changes?)", flush=True)
    
    print("-" * 60, flush=True)
    print(f"✅ Agent completed!", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Fix a code finding using Ollama")
    parser.add_argument("--finding-json", required=True, help="Finding as JSON string")
    parser.add_argument("--repo-path", required=True, help="Path to the repository")
    parser.add_argument("--model", default="codellama", help="Ollama model to use")
    args = parser.parse_args()
    
    # Check Ollama is running
    if not check_ollama():
        print(f"❌ Ollama not running at {OLLAMA_URL}", file=sys.stderr)
        print("   Start it with: ollama serve", file=sys.stderr)
        sys.exit(1)
    
    # Check model is available
    models = get_models()
    if args.model not in models and not any(args.model in m for m in models):
        print(f"⚠️ Model '{args.model}' not found. Available: {', '.join(models)}", file=sys.stderr)
        print(f"   Pull it with: ollama pull {args.model}", file=sys.stderr)
        # Try to continue anyway
    
    # Parse the finding JSON
    try:
        finding = json.loads(args.finding_json)
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Verify repo path exists
    if not Path(args.repo_path).is_dir():
        print(f"❌ Repository not found: {args.repo_path}", file=sys.stderr)
        sys.exit(1)
    
    # Run the fix
    fix_finding(finding, args.repo_path, args.model)


if __name__ == "__main__":
    main()
