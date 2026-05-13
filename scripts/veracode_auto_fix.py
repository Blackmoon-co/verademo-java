#!/usr/bin/env python3
"""
Veracode Auto-Fix Script
Parses pipeline-results.json, iterates all Java files with findings,
runs 'veracode fix' interactively and selects fix option 1 for each issue.
"""

import json
import os
import sys
import pexpect


RESULTS_FILE = os.environ.get("VERACODE_RESULTS_FILE", "pipeline-results.json")
WORKSPACE = os.environ.get("GITHUB_WORKSPACE", os.getcwd())
JAVA_SRC_BASE = os.path.join(WORKSPACE, "app", "src", "main", "java")
VERACODE_CLI = os.environ.get("VERACODE_CLI", "veracode")


def get_java_files_with_issues(results_file: str) -> dict:
    """
    Parses pipeline-results.json and returns a dict:
      { "relative/path/File.java": issue_count }
    Only includes .java source files (excludes .jsp, .js, etc.)
    Issue numbers shown by 'veracode fix' are sequential (1..N) per file.
    """
    with open(results_file, encoding="utf-8") as f:
        data = json.load(f)

    files_issues: dict = {}
    for finding in data.get("findings", []):
        source = finding.get("files", {}).get("source_file", {}).get("file", "")
        if not source.endswith(".java"):
            continue
        files_issues[source] = files_issues.get(source, 0) + 1

    return files_issues


def resolve_full_path(relative_java_path: str) -> str | None:
    """
    Maps 'com/veracode/.../File.java' -> full path under JAVA_SRC_BASE.
    Returns None if the file doesn't exist on disk.
    """
    full = os.path.join(JAVA_SRC_BASE, relative_java_path)
    return full if os.path.exists(full) else None


def fix_file(results_file: str, full_java_path: str, issue_count: int) -> int:
    """
    Spawns 'veracode fix' for a single file and automatically:
      - Enters each issue number (1..issue_count)
      - Selects fix option 1 when a fix is available
    Returns the number of fixes actually applied.
    """
    cmd = f"{VERACODE_CLI} fix --results {results_file} {full_java_path}"
    print(f"\n{'='*70}")
    print(f"Fixing: {os.path.basename(full_java_path)}  ({issue_count} issues)")
    print(f"CMD: {cmd}")
    print("=" * 70)

    fixes_applied = 0

    try:
        child = pexpect.spawn(cmd, timeout=120, encoding="utf-8")
        child.logfile_read = sys.stdout  # stream output to console

        current_issue = 1
        at_issue_prompt = False  # tracks if we are already sitting at the prompt

        while current_issue <= issue_count:
            if not at_issue_prompt:
                # Wait for the issue-selection prompt
                idx = child.expect(
                    ["Enter issue number:", pexpect.EOF, pexpect.TIMEOUT],
                    timeout=60,
                )
                if idx != 0:
                    print(f"\n[auto-fix] Process ended before issue {current_issue}")
                    break
                at_issue_prompt = True

            # Send the current issue number
            child.sendline(str(current_issue))
            at_issue_prompt = False

            # Wait for either: fix prompt, back to issue prompt, or EOF
            idx = child.expect(
                [
                    r"Enter the fix to apply\.",  # 0: fix available
                    "Enter issue number:",         # 1: no fix, back to prompt
                    pexpect.EOF,                  # 2: done
                    pexpect.TIMEOUT,              # 3: timeout
                ],
                timeout=60,
            )

            if idx == 0:
                # Fix is available – always choose option 1
                child.sendline("1")
                fixes_applied += 1
                current_issue += 1
                # After applying, we'll land back at "Enter issue number:"
                at_issue_prompt = False

            elif idx == 1:
                # No fix found for this issue, already back at issue prompt
                print(f"\n[auto-fix] No fix available for issue {current_issue}, skipping")
                current_issue += 1
                at_issue_prompt = True  # already at the prompt, don't wait again

            else:
                # EOF or timeout – process finished
                print(f"\n[auto-fix] Process ended (EOF/timeout) at issue {current_issue}")
                break

        # Exit gracefully: send empty input to leave issue selection
        if current_issue > issue_count:
            try:
                child.expect("Enter issue number:", timeout=10)
                child.sendline("")  # empty → exit
            except Exception:
                pass

        child.close()

    except pexpect.exceptions.ExceptionPexpect as e:
        print(f"\n[auto-fix] pexpect error for {full_java_path}: {e}")

    print(f"\n[auto-fix] Applied {fixes_applied}/{issue_count} fixes for {os.path.basename(full_java_path)}")
    return fixes_applied


def main() -> None:
    if not os.path.exists(RESULTS_FILE):
        print(f"ERROR: Results file not found: {RESULTS_FILE}")
        sys.exit(1)

    files_issues = get_java_files_with_issues(RESULTS_FILE)

    if not files_issues:
        print("No Java files with issues found in results. Nothing to fix.")
        sys.exit(0)

    print(f"\nJava files with issues ({len(files_issues)} total):")
    for path, count in files_issues.items():
        print(f"  [{count:3d} issues]  {path}")

    total_fixed = 0
    skipped = []

    for relative_path, issue_count in files_issues.items():
        full_path = resolve_full_path(relative_path)
        if full_path is None:
            print(f"\n[auto-fix] SKIP – file not found on disk: {relative_path}")
            skipped.append(relative_path)
            continue
        total_fixed += fix_file(RESULTS_FILE, full_path, issue_count)

    print("\n" + "=" * 70)
    print(f"SUMMARY: {total_fixed} fix(es) applied across {len(files_issues) - len(skipped)} file(s)")
    if skipped:
        print(f"Skipped (not on disk): {skipped}")
    print("=" * 70)


if __name__ == "__main__":
    main()
