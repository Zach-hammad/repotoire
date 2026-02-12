#!/usr/bin/env python3
"""
Repotoire Fix Agent - Uses Claude Agent SDK to fix code findings.

Usage:
    python fix_agent.py --finding-json '<json>' --repo-path /path/to/repo

The agent will:
1. Read the affected file(s)
2. Analyze and fix the issue
3. Create a branch, commit, and open a PR
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions


def verify_finding_fixed(finding: dict, repo_path: str) -> bool:
    """Run repotoire analyze and check if the finding still exists."""
    title = finding.get("title", "")
    affected_files = finding.get("affected_files", [])
    target_file = affected_files[0] if affected_files else None
    
    print("\n🔍 Verifying fix...", flush=True)
    
    try:
        result = subprocess.run(
            ["repotoire", "analyze", "--json"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        
        if result.returncode != 0:
            print(f"⚠️ Could not run verification: {result.stderr}", flush=True)
            return False
        
        # Parse the analysis output
        try:
            analysis = json.loads(result.stdout)
            findings = analysis.get("findings", [])
        except json.JSONDecodeError:
            print("⚠️ Could not parse verification output", flush=True)
            return False
        
        # Check if the specific finding still exists
        for f in findings:
            f_title = f.get("title", "")
            f_files = f.get("affected_files", [])
            
            # Match by title and file
            if f_title == title and target_file and target_file in f_files:
                return False  # Finding still exists
        
        return True  # Finding not found = fixed
        
    except subprocess.TimeoutExpired:
        print("⚠️ Verification timed out", flush=True)
        return False
    except FileNotFoundError:
        print("⚠️ repotoire command not found", flush=True)
        return False


async def fix_finding(finding: dict, repo_path: str) -> None:
    """Run the agent to fix a finding."""
    
    file_path = finding.get("affected_files", ["unknown"])[0] if finding.get("affected_files") else "unknown"
    line_start = finding.get("line_start", 1)
    line_end = finding.get("line_end", line_start)
    finding_index = finding.get("index", 0)
    
    # Build the prompt
    prompt = f"""Fix this code issue in the repository at {repo_path}:

## Finding #{finding_index}
- **Title:** {finding.get("title", "Unknown")}
- **Severity:** {finding.get("severity", "Unknown")}
- **File:** {file_path}
- **Lines:** {line_start}-{line_end}

## Description
{finding.get("description", "No description provided.")}

## Suggested Fix
{finding.get("suggested_fix", "Apply an appropriate fix based on the description.")}

## Your Task
1. First, read the file to understand the context
2. Fix the issue at the specified lines
3. Create a new branch: `git checkout -b fix/finding-{finding_index}`
4. Commit with message: `fix: {finding.get("title", "code issue")}`
5. Push: `git push -u origin fix/finding-{finding_index}`
6. Create PR: `gh pr create --title "fix: {finding.get("title", "code issue")}" --body "Fixes finding #{finding_index}

**Issue:** {finding.get("title", "code issue")}
**Severity:** {finding.get("severity", "Unknown")}
**File:** {file_path}:{line_start}-{line_end}

{finding.get("description", "")}
"`

Be precise. Make minimal changes. Verify the fix compiles/passes tests if possible.
"""

    print(f"🚀 Starting agent to fix: {finding.get('title', 'Unknown')}", flush=True)
    print(f"📁 Repository: {repo_path}", flush=True)
    print(f"📄 File: {file_path}:{line_start}-{line_end}", flush=True)
    print("-" * 60, flush=True)

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                cwd=repo_path,
                max_turns=30,
            ),
        ):
            # Handle different message types
            if hasattr(message, "type"):
                if message.type == "assistant":
                    # Claude's response
                    if hasattr(message, "content"):
                        for block in message.content:
                            if hasattr(block, "text"):
                                print(f"💭 {block.text}", flush=True)
                            elif hasattr(block, "type") and block.type == "tool_use":
                                print(f"🔧 Using tool: {block.name}", flush=True)
                                
                elif message.type == "tool_result":
                    if hasattr(message, "content"):
                        content = message.content
                        if isinstance(content, str):
                            # Truncate long outputs
                            if len(content) > 500:
                                content = content[:500] + "... (truncated)"
                            print(f"📋 Result: {content}", flush=True)
                            
                elif message.type == "result":
                    print("-" * 60, flush=True)
                    print(f"✅ Agent completed!", flush=True)
                    if hasattr(message, "result"):
                        print(f"📝 Summary: {message.result}", flush=True)
                    
                    # Verify the fix
                    if verify_finding_fixed(finding, repo_path):
                        print("✅ Finding fixed!", flush=True)
                    else:
                        print("⚠️ Finding may still exist", flush=True)
                        
            elif hasattr(message, "result"):
                # Final result
                print("-" * 60, flush=True)
                print(f"✅ Done: {message.result}", flush=True)
                
                # Verify the fix
                if verify_finding_fixed(finding, repo_path):
                    print("✅ Finding fixed!", flush=True)
                else:
                    print("⚠️ Finding may still exist", flush=True)
                
    except Exception as e:
        print(f"❌ Error: {e}", flush=True)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Fix a code finding using Claude Agent SDK")
    parser.add_argument("--finding-json", required=True, help="Finding as JSON string")
    parser.add_argument("--repo-path", required=True, help="Path to the repository")
    args = parser.parse_args()
    
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
    
    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    
    # Run the agent
    asyncio.run(fix_finding(finding, args.repo_path))


if __name__ == "__main__":
    main()
