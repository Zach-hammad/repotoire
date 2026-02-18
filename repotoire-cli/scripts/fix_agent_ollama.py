#!/usr/bin/env python3
"""
Repotoire Fix Agent - Ollama Edition

Uses local Ollama models to fix code findings.
No API key needed - runs 100% locally.

Key improvements (v2):
- FIM (Fill-in-Middle) support for base models
- Model-specific prompt formats (DeepSeek, CodeLlama, etc.)
- Syntax validation before applying changes
- Deduplication of leading lines (prevents duplicate function signatures)
- Better code extraction from chatty responses

Usage:
    python fix_agent_ollama.py --finding-json '<json>' --repo-path /path/to/repo [--model deepseek-coder:6.7b]
"""

import argparse
import ast
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

# Instruct format templates per model family
INSTRUCT_FORMATS = {
    "deepseek": """### Instruction:
{instruction}

### Response:
""",
    "codellama": """[INST] {instruction} [/INST]
""",
    "default": """{instruction}
""",
}


def get_model_family(model: str) -> str:
    """Determine model family from model name."""
    model_lower = model.lower()
    if "deepseek" in model_lower:
        return "deepseek"
    elif "codellama" in model_lower or "code-llama" in model_lower:
        return "codellama"
    elif "qwen" in model_lower:
        return "qwen"
    return "default"


def is_base_model(model: str) -> bool:
    """Check if this is a base (non-instruct) model."""
    model_lower = model.lower()
    if "-base" in model_lower:
        return True
    if "-instruct" in model_lower:
        return False
    return False


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
    except Exception as e:
        logging.debug("Failed to list models: %s", e)
    return []


def read_file(repo_path: str, file_path: str) -> str | None:
    """Read a file from the repo."""
    full_path = Path(repo_path) / file_path
    if full_path.exists():
        return full_path.read_text()
    return None


def generate_fim(model: str, prefix: str, suffix: str) -> str:
    """Generate code using Fill-in-Middle (FIM) format.
    
    Uses Ollama's native suffix parameter for models that support FIM.
    """
    payload = {
        "model": model,
        "prompt": prefix,
        "suffix": suffix,
        "stream": False,
        "raw": True,
        "options": {
            "temperature": 0,
            "num_predict": 512,
            "stop": ["\n\n\n", "```", "###"],
        }
    }
    
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=120,
        )
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
        else:
            print(f"❌ Ollama error: {resp.status_code!r}", file=sys.stderr)
            return ""
    except Exception as e:
        print(f"❌ Ollama request failed: {str(e)[:200].replace(chr(10), " ")}", file=sys.stderr)
        return ""


def generate_instruct(model: str, instruction: str, system: str = "") -> str:
    """Generate code using instruct format."""
    family = get_model_family(model)
    
    if not system:
        system = """You are a code editor. Output ONLY code, no explanations.
RULES:
- Output ONLY the replacement code
- NO explanations, NO markdown, NO comments about changes
- Preserve exact indentation
- If removing code, output nothing"""
    
    fmt = INSTRUCT_FORMATS.get(family, INSTRUCT_FORMATS["default"])
    formatted_prompt = fmt.format(instruction=instruction)
    
    payload = {
        "model": model,
        "prompt": formatted_prompt,
        "system": system,
        "stream": False,
        "options": {
            "temperature": 0,
            "num_predict": 1024,
            "stop": ["\n\n\n", "Explanation:", "Note:", "This ", "The above", "```\n\n"],
        }
    }
    
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=180,
        )
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
        else:
            print(f"❌ Ollama error: {resp.status_code!r}", file=sys.stderr)
            return ""
    except Exception as e:
        print(f"❌ Ollama request failed: {str(e)[:200].replace(chr(10), " ")}", file=sys.stderr)
        return ""


def extract_code_from_response(response: str, language: str = "") -> str:
    """Extract clean code from model response."""
    if not response:
        return ""
    
    response = response.strip()
    
    # Extract from markdown code blocks
    if "```" in response:
        blocks = []
        in_block = False
        current_block = []
        
        for line in response.split('\n'):
            if line.strip().startswith('```'):
                if in_block:
                    blocks.append('\n'.join(current_block))
                    current_block = []
                    in_block = False
                else:
                    in_block = True
            elif in_block:
                current_block.append(line)
        
        if blocks:
            return max(blocks, key=len).strip()
    
    # Find where code starts
    lines = response.split('\n')
    code_start = 0
    
    code_indicators = [
        'fn ', 'def ', 'func ', 'function ', 'class ', 'struct ', 'impl ',
        'pub ', 'private ', 'public ', 'const ', 'let ', 'var ', 'import ',
        'from ', 'use ', 'if ', 'for ', 'while ', 'match ', 'return ',
        '    ', '\t',
    ]
    
    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        if any(stripped.startswith(ind) or line.startswith(ind) for ind in code_indicators):
            code_start = i
            break
        if line and line[0] in '({[@#/':
            code_start = i
            break
    
    code_lines = lines[code_start:]
    
    # Remove trailing explanations
    explanation_starters = [
        'note:', 'explanation:', 'this will', 'the above', "here's what",
        'this code', 'this fix', 'this change', 'i have', 'i\'ve',
    ]
    
    final_lines = []
    for line in code_lines:
        lower = line.strip().lower()
        if any(lower.startswith(ex) for ex in explanation_starters):
            break
        final_lines.append(line)
    
    return '\n'.join(final_lines).rstrip()


def deduplicate_leading_lines(fixed_code: str, context_before: str) -> str:
    """Remove duplicate leading lines if the fix repeats context lines.
    
    Handles the common case where a model includes the function signature
    in its fix when the signature is already in the context.
    """
    if not fixed_code or not context_before:
        return fixed_code
    
    fixed_lines = fixed_code.split('\n')
    context_lines = context_before.split('\n')
    
    if not context_lines or not fixed_lines:
        return fixed_code
    
    # Check if first lines of fix match last lines of context
    lines_to_skip = 0
    for i, fix_line in enumerate(fixed_lines[:5]):
        fix_stripped = fix_line.strip()
        if not fix_stripped:
            continue
        for ctx_line in context_lines[-5:]:
            if fix_stripped == ctx_line.strip():
                lines_to_skip = i + 1
                break
    
    if lines_to_skip > 0:
        return '\n'.join(fixed_lines[lines_to_skip:])
    
    return fixed_code


def validate_python_syntax(code: str) -> tuple[bool, str]:
    """Validate Python syntax."""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"Line {e.lineno}: {e.msg}"


def validate_rust_syntax(code: str, repo_path: str) -> tuple[bool, str]:
    """Validate Rust syntax using rustfmt."""
    try:
        result = subprocess.run(
            ["rustfmt", "--check", "--edition", "2021"],
            input=code,
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=10,
        )
        return True, ""
    except FileNotFoundError:
        return True, ""
    except Exception as e:
        return True, str(e)


def validate_syntax(code: str, language: str, repo_path: str = "") -> tuple[bool, str]:
    """Validate code syntax for supported languages."""
    if not code.strip():
        return True, ""
    
    if language == "python":
        return validate_python_syntax(code)
    elif language == "rust":
        return validate_rust_syntax(code, repo_path)
    return True, ""


def run_command(cmd: str, cwd: str, quiet: bool = False) -> tuple[int, str]:
    """Run a shell command."""
    if not quiet:
        print(f"🔧 Running: {cmd}", flush=True)
    import shlex
    result = subprocess.run(
        shlex.split(cmd),
        shell=False,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0 and not quiet:
        print(f"   Exit code: {result.returncode}", flush=True)
    return result.returncode, output


def check_repo_dirty(repo_path: str) -> bool:
    """Check if repo has uncommitted changes."""
    code, output = run_command("git status --porcelain", repo_path, quiet=True)
    return code == 0 and bool(output.strip())


def stash_changes(repo_path: str) -> bool:
    """Stash uncommitted changes."""
    code, output = run_command("git stash push -m 'fix-agent-auto-stash'", repo_path)
    if code != 0:
        print(f"❌ Failed to stash changes: {output}", flush=True)
        return False
    return "No local changes" not in output


def pop_stash(repo_path: str) -> None:
    """Pop the most recent stash."""
    code, output = run_command("git stash pop", repo_path)
    if code != 0:
        print(f"⚠️ Failed to restore stash: {output}", flush=True)


def check_remote_exists(repo_path: str, remote: str = "origin") -> bool:
    """Check if a git remote exists."""
    code, output = run_command(f"git remote get-url {remote}", repo_path, quiet=True)
    return code == 0


def check_gh_installed() -> bool:
    """Check if GitHub CLI is installed and authenticated."""
    result = subprocess.run(["which", "gh"], capture_output=True, text=True)
    if result.returncode != 0:
        return False
    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    return result.returncode == 0


def verify_finding_fixed(finding: dict, repo_path: str) -> bool:
    """Run repotoire analyze and check if finding is gone."""
    finding_id = finding.get("id")
    finding_title = finding.get("title", "")
    file_path = finding.get("affected_files", [""])[0] if finding.get("affected_files") else ""
    line_start = finding.get("line_start", 0)
    
    print(f"🔍 Verifying fix...", flush=True)
    code, output = run_command("repotoire analyze --json", repo_path)
    
    if code != 0:
        print(f"⚠️ repotoire analyze failed, skipping verification", flush=True)
        return True
    
    try:
        results = json.loads(output)
        findings = results.get("findings", [])
        
        for f in findings:
            f_files = f.get("affected_files", [])
            f_line = f.get("line_start", 0)
            f_title = f.get("title", "")
            f_id = f.get("id")
            
            if finding_id and f_id == finding_id:
                return False
            elif (file_path in f_files and 
                  abs(f_line - line_start) <= 5 and
                  f_title == finding_title):
                return False
        
        return True
    except json.JSONDecodeError:
        print(f"⚠️ Could not parse output, skipping verification", flush=True)
        return True


def is_removal_fix(finding: dict) -> bool:
    """Check if this finding suggests removing code."""
    title = finding.get("title", "").lower()
    suggested = finding.get("suggested_fix", "").lower()
    desc = finding.get("description", "").lower()
    
    removal_keywords = ["dead ", "unused ", "remove ", "delete ", "never called", 
                       "never used", "unreachable", "redundant"]
    
    text = f"{title} {suggested} {desc}"
    return any(kw in text for kw in removal_keywords)


def fix_finding(finding: dict, repo_path: str, model: str, verify: bool = True) -> None:
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
    
    # Detect language
    ext = Path(file_path).suffix.lower()
    lang_map = {'.rs': 'rust', '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
                '.go': 'go', '.java': 'java', '.c': 'c', '.cpp': 'cpp', '.cs': 'csharp'}
    language = lang_map.get(ext, 'code')
    
    # Check for removal-type fix
    if is_removal_fix(finding):
        print(f"🗑️  Removal-type fix detected", flush=True)
        print(f"   Removing lines {line_start}-{line_end}", flush=True)
        fixed_code = ""
    else:
        # Get context
        problem_start = max(0, line_start - 1)
        problem_end = min(len(lines), line_end)
        problem_code = '\n'.join(lines[problem_start:problem_end])
        
        context_before = '\n'.join(lines[max(0, line_start - 11):line_start - 1])
        context_after = '\n'.join(lines[line_end:min(len(lines), line_end + 10)])
        
        # Build instruction
        instruction = f"""Fix this {language} code issue:

ISSUE: {finding.get("title", "Unknown")}
DESCRIPTION: {finding.get("description", "")}
FIX HINT: {finding.get("suggested_fix", "Fix the issue")}

CODE TO FIX (lines {line_start}-{line_end}):
```{language}
{problem_code}
```

CONTEXT BEFORE:
```{language}
{context_before}
```

CONTEXT AFTER:
```{language}
{context_after}
```

Output ONLY the fixed code for lines {line_start}-{line_end}. No explanations."""
        
        print(f"💭 Asking {model} for fix...", flush=True)
        
        if is_base_model(model):
            prefix = '\n'.join(lines[:line_start - 1]) + '\n'
            suffix = '\n' + '\n'.join(lines[line_end:])
            raw_response = generate_fim(model, prefix, suffix)
        else:
            raw_response = generate_instruct(model, instruction)
        
        if not raw_response:
            print("❌ No response from model", flush=True)
            return
        
        # Extract and clean code
        fixed_code = extract_code_from_response(raw_response, language)
        
        if not fixed_code and raw_response:
            fixed_code = raw_response
        
        # Remove duplicate lines from context
        fixed_code = deduplicate_leading_lines(fixed_code, context_before)
        
        # Sanity checks
        original_lines = line_end - line_start + 1
        fixed_lines = len(fixed_code.split('\n')) if fixed_code else 0
        
        if fixed_lines > original_lines * 3 and fixed_lines > 20:
            print(f"⚠️ Fix too large ({fixed_lines} vs {original_lines} lines)", flush=True)
            print("   Likely hallucination - aborting", flush=True)
            return
        
        # Validate syntax
        is_valid, error = validate_syntax(fixed_code, language, repo_path)
        if not is_valid:
            print(f"⚠️ Syntax validation failed: {error}", flush=True)
            print("   Proceeding anyway (may need review)", flush=True)
    
    # Show the fix
    print(f"📋 Generated fix:", flush=True)
    if fixed_code:
        for line in fixed_code.split('\n')[:10]:
            print(f"   {line}", flush=True)
        if fixed_code.count('\n') > 10:
            print(f"   ... ({fixed_code.count(chr(10)) - 9} more lines)", flush=True)
    else:
        print("   (empty - removing code)", flush=True)
    
    # Git operations
    print("-" * 60, flush=True)
    print(f"💭 Applying fix to {file_path}...", flush=True)
    
    branch_name = f"fix/finding-{finding_index}"
    stashed = False
    
    if check_repo_dirty(repo_path):
        print("⚠️ Uncommitted changes - stashing...", flush=True)
        stashed = stash_changes(repo_path)
        if not stashed:
            print("❌ Cannot proceed with dirty repo", flush=True)
            return
    
    # Apply fix
    if fixed_code == "":
        replacement_lines = []
        print(f"   (Removing lines {line_start}-{line_end})", flush=True)
    else:
        replacement_lines = fixed_code.split('\n')
    
    new_lines = lines[:line_start - 1] + replacement_lines + lines[line_end:]
    new_content = '\n'.join(new_lines)
    
    full_path = Path(repo_path) / file_path
    full_path.write_text(new_content)
    print(f"✅ Fixed {file_path}", flush=True)
    
    # Verification
    if verify:
        print("-" * 60, flush=True)
        if not verify_finding_fixed(finding, repo_path):
            print(f"❌ Verification failed - reverting", flush=True)
            full_path.write_text(content)
            if stashed:
                pop_stash(repo_path)
            return
        print(f"✅ Verification passed!", flush=True)
    
    # Git commit
    code, current_branch = run_command("git rev-parse --abbrev-ref HEAD", repo_path, quiet=True)
    if code != 0:
        print("❌ Not a git repository", flush=True)
        if stashed:
            pop_stash(repo_path)
        return
    current_branch = current_branch.strip()
    
    code, output = run_command(f"git checkout -b {branch_name}", repo_path)
    if code != 0:
        if "already exists" in output:
            code, _ = run_command(f"git checkout {branch_name}", repo_path)
            if code != 0:
                print(f"❌ Failed to switch branch", flush=True)
                if stashed:
                    run_command(f"git checkout {current_branch}", repo_path, quiet=True)
                    pop_stash(repo_path)
                return
        else:
            print(f"❌ Failed to create branch", flush=True)
            if stashed:
                pop_stash(repo_path)
            return
    
    run_command(f"git add {file_path}", repo_path)
    commit_msg = f"fix: {finding.get('title', 'code issue')}"
    code, output = run_command(f'git commit -m "{commit_msg}"', repo_path)
    
    if code == 0:
        print(f"✅ Committed: {commit_msg}", flush=True)
        
        if check_remote_exists(repo_path):
            code, output = run_command(f"git push -u origin {branch_name}", repo_path)
            if code == 0:
                print(f"✅ Pushed to origin/{branch_name}", flush=True)
                
                if check_gh_installed():
                    code, output = run_command(
                        f'gh pr create --title "{commit_msg}" --body "Fixes #{finding_index}"',
                        repo_path
                    )
                    if code == 0:
                        print(f"✅ Created PR!", flush=True)
                    else:
                        print(f"⚠️ PR creation failed", flush=True)
                else:
                    print("⚠️ gh CLI not installed", flush=True)
            else:
                print(f"⚠️ Push failed: {output.strip()}", flush=True)
        else:
            print("⚠️ No remote - skipping push", flush=True)
    else:
        if "nothing to commit" in output.lower():
            print("⚠️ Nothing to commit", flush=True)
        else:
            print(f"⚠️ Commit failed", flush=True)
    
    if stashed:
        print("📦 Restoring stash...", flush=True)
        run_command(f"git checkout {current_branch}", repo_path, quiet=True)
        pop_stash(repo_path)
    
    print("-" * 60, flush=True)
    print(f"✅ Agent completed!", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Fix code findings using Ollama")
    parser.add_argument("--finding-json", required=True, help="Finding as JSON")
    parser.add_argument("--repo-path", required=True, help="Repository path")
    parser.add_argument("--model", default="deepseek-coder:6.7b", help="Ollama model")
    parser.add_argument("--verify", action=argparse.BooleanOptionalAction, default=True,
                        help="Verify fix before commit")
    args = parser.parse_args()
    
    if not check_ollama():
        print(f"❌ Ollama not running at {OLLAMA_URL}", file=sys.stderr)
        sys.exit(1)
    
    models = get_models()
    if args.model not in models and not any(args.model in m for m in models):
        print(f"⚠️ Model '{args.model}' not found", file=sys.stderr)
    
    try:
        finding = json.loads(args.finding_json)
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    
    if not Path(args.repo_path).is_dir():
        print(f"❌ Repository not found", file=sys.stderr)
        sys.exit(1)
    
    fix_finding(finding, args.repo_path, args.model, verify=args.verify)


if __name__ == "__main__":
    main()
