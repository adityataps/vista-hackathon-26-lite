#!/usr/bin/env python3
"""
Hackathon Repo Scanner -- SELF-TEST  --  Vista P&T hackathon assessment
========================================================================

THIS IS NOT THE GRADED SCAN. It is a mechanics-only smoke test, safe to send
to teams EARLY (before the real tool is distributed) so they can confirm:

  * their machine can run it (Python version, no pip install, no network)
  * git is detected correctly (including worktree / submodule checkouts)
  * the file walk works and produces a sane file/language count
  * a well-formed JSON gets written to disk

It deliberately does NOT contain, and does not run, any of the scored
detection logic from hackathon_scan.py: no secret scan, no dangerous-pattern
scan, no stub/TODO/completeness scan, no agentic-design or AI-evidence scan,
no cadence-flag heuristics, and NO computed 0-5 score of any kind. Those
dimensions appear in the output only as `{"available": false}` placeholders
so the JSON *shape* previews correctly without previewing any of the values
or the logic that produces them.

This file is intentionally standalone (no `import hackathon_scan`) -- it
must be distributable on its own, before the real scan tool goes out, so it
cannot depend on that file being present.

    python hackathon_scan_selftest.py --team "Team Rocket"

The event window is baked in by default (see DEFAULT_EVENT_START/END below) -- override with
--start/--end only if you need to check against a different window.

Writes `<team>_selftest.json` (never `_assessment.json` -- this output is
NOT uploaded anywhere; it's for the team's own sanity check only).
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

SELFTEST_VERSION = "selftest-1.0"  # distinct from hackathon_scan.py's TOOL_VERSION ("2.0") on
# purpose -- this payload must never be mistaken for a real
# submission's schema.
DEFAULT_WINDOW_THRESHOLD = 80.0

# Same official event window as hackathon_scan.py, baked in here too so `--team "Name"` alone
# is enough. Keep these two constants in sync with hackathon_scan.py's DEFAULT_EVENT_START/END.
DEFAULT_EVENT_START = "2026-07-14T08:00:00-07:00"
DEFAULT_EVENT_END = "2026-07-15T08:00:00-07:00"

# ---------------------------------------------------------------------------
# SAFE, structural-only data. No entrypoint/stub regexes, no security or
# secret patterns, no AI/agentic token lists -- those are the scored signals
# and live only in hackathon_scan.py, which this file must not expose early.
# ---------------------------------------------------------------------------

LANGUAGE_INFO = [
    {"name": "Python", "exts": [".py"],
     "manifests": ["requirements.txt", "pyproject.toml", "Pipfile", "setup.py", "setup.cfg",
                   "poetry.lock", "environment.yml"]},
    {"name": "JavaScript", "exts": [".js", ".jsx", ".mjs", ".cjs"],
     "manifests": ["package.json", "yarn.lock", "pnpm-lock.yaml"]},
    {"name": "TypeScript", "exts": [".ts", ".tsx"], "manifests": ["package.json", "tsconfig.json"]},
    {"name": "Go", "exts": [".go"], "manifests": ["go.mod", "go.sum"]},
    {"name": "Rust", "exts": [".rs"], "manifests": ["Cargo.toml", "Cargo.lock"]},
    {"name": "Java", "exts": [".java"], "manifests": ["pom.xml", "build.gradle", "build.gradle.kts"]},
    {"name": "C#", "exts": [".cs"], "manifests": ["*.csproj", "*.sln", "packages.config"]},
    {"name": "Ruby", "exts": [".rb"], "manifests": ["Gemfile", "Gemfile.lock", "*.gemspec"]},
    {"name": "PHP", "exts": [".php"], "manifests": ["composer.json", "composer.lock"]},
    {"name": "C++", "exts": [".cpp", ".cc", ".cxx", ".hpp"],
     "manifests": ["CMakeLists.txt", "Makefile", "conanfile.txt"]},
    {"name": "C", "exts": [".c", ".h"], "manifests": ["Makefile", "CMakeLists.txt"]},
    {"name": "Swift", "exts": [".swift"], "manifests": ["Package.swift", "Podfile"]},
    {"name": "Kotlin", "exts": [".kt", ".kts"], "manifests": ["build.gradle", "build.gradle.kts"]},
    {"name": "Shell", "exts": [".sh", ".bash"], "manifests": []},
    {"name": "SQL", "exts": [".sql"], "manifests": []},
    {"name": "HTML", "exts": [".html", ".htm"], "manifests": []},
    {"name": "CSS", "exts": [".css", ".scss", ".sass"], "manifests": []},
    {"name": "Notebook", "exts": [".ipynb"], "manifests": []},
    {"name": "Scala", "exts": [".scala"], "manifests": ["build.sbt"]},
    {"name": "R", "exts": [".r"], "manifests": ["DESCRIPTION"]},
]
EXT_LANG = {ext: p["name"] for p in LANGUAGE_INFO for ext in p["exts"]}
DEP_MANIFESTS = sorted({m for p in LANGUAGE_INFO for m in p["manifests"]})

LICENSE_FILES = ["license", "license.md", "license.txt", "copying"]
README_NAMES = ["readme.md", "readme.rst", "readme.txt", "readme"]

# Directories that should be gitignored, not committed -- same list as the real tool. Filtering
# these out of the fallback walk (and flagging if they ARE committed) is structural housekeeping,
# not a scored detection signature.
HEAVY_DIRS = ["node_modules", "venv", ".venv", "env", "__pycache__",
              "dist", "build", ".next", "target", "vendor", ".gradle"]

SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".tar",
            ".mp4", ".mov", ".ico", ".woff", ".woff2", ".ttf", ".eot",
            ".lock", ".min.js", ".map", ".bin", ".exe", ".so", ".dll",
            ".pyc", ".pyo", ".class", ".o", ".obj"}
MAX_FILE_BYTES = 1_500_000


# ---------------------------------------------------------------------------
# Small helpers (duplicated from hackathon_scan.py, not imported -- this file
# must stand alone since it ships before the real tool does).
# ---------------------------------------------------------------------------

def _git(root, *args):
    try:
        out = subprocess.run(["git", "-C", root, *args],
                             capture_output=True, text=True, timeout=60)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _parse_iso(s):
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _collect_files(root, is_git):
    """Prefer git-tracked files (respects .gitignore); fall back to a walk that
    also excludes HEAVY_DIRS, so a non-git submission isn't swamped by vendor dirs."""
    if is_git:
        raw = _git(root, "-c", "core.quotePath=false", "ls-files", "-z")
        tracked = [f for f in raw.split("\0") if f]
        if tracked:
            return [os.path.join(root, f) for f in tracked]
    skip_dirs = {".git"} | set(HEAVY_DIRS)
    files = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in skip_dirs]
        for fn in fns:
            files.append(os.path.join(dp, fn))
    return files


# ---------------------------------------------------------------------------
# Git descriptive stats -- counts, dates, window check ONLY. No cadence-flag
# heuristics (bulk-paste share, compressed-history window): those thresholds
# aren't disclosed anywhere today and stay exclusive to the real scan.
# ---------------------------------------------------------------------------

def _git_stats(root, is_git, window_start, window_end, threshold):
    git = {"is_git_repo": is_git}
    if not is_git:
        return git

    log = _git(root, "log", "--pretty=%H|%aI|%an")
    lines = [l for l in log.splitlines() if "|" in l]
    commits = [dict(zip(("hash", "iso", "author"), (l.split("|", 2) + ["", "", ""])[:3]))
               for l in lines]
    git["total_commits"] = len(commits)
    git["contributors"] = sorted({c["author"] for c in commits if c["author"]})
    git["contributor_count"] = len(git["contributors"])

    dts = sorted(d for d in (_parse_iso(c["iso"]) for c in commits) if d)
    if dts:
        git["first_commit"] = dts[0].isoformat()
        git["last_commit"] = dts[-1].isoformat()
        git["active_days"] = len({d.date().isoformat() for d in dts})
        git["commit_span_hours"] = round((dts[-1] - dts[0]).total_seconds() / 3600.0, 1)

    ws, we = _parse_iso(window_start), _parse_iso(window_end)
    if ws and we and dts:
        in_win = [d for d in dts if ws <= d <= we]
        git["commits_in_window"] = len(in_win)
        git["pct_commits_in_window"] = round(100 * len(in_win) / len(dts), 1)
        git["worked_in_window"] = git["pct_commits_in_window"] >= threshold
        git["window_threshold"] = threshold
        git["window_checked"] = True
    else:
        git["window_checked"] = False

    git["note"] = ("Descriptive stats only. The real scan additionally computes cadence/integrity "
                   "flags (e.g. bulk-paste and compressed-history detection) not run here.")
    return git


# ---------------------------------------------------------------------------
# Structure + hygiene -- filename/dir presence only. No file content is read
# except the single README file (for the section/keyword check below).
# ---------------------------------------------------------------------------

def _structure_and_hygiene(root, files):
    lang_counts = {}
    dirs, heavy_committed = set(), []
    tests_present = False
    rels, lower = [], []

    for full in files:
        rel = os.path.relpath(full, root)
        low = rel.lower()
        rels.append(rel)
        lower.append(low)
        if os.sep in rel:
            dirs.add(rel.split(os.sep)[0])
        for hd in HEAVY_DIRS:
            if low.startswith(hd + "/") or f"/{hd}/" in "/" + low:
                heavy_committed.append(hd)
        if re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|test_.*\.|_test\.|\.test\.|\.spec\.", low):
            tests_present = True
        ext = os.path.splitext(low)[1]
        if ext in EXT_LANG:
            lang_counts[EXT_LANG[ext]] = lang_counts.get(EXT_LANG[ext], 0) + 1

    has = lambda names: next((r for r, l in zip(rels, lower)
                              if l in names or os.path.basename(l) in names), None)
    readme = has(README_NAMES)
    license_f = has(LICENSE_FILES)
    gitignore = ".gitignore" in lower

    dep_found = []
    for m in DEP_MANIFESTS:
        if m.startswith("*"):
            if any(l.endswith(m[1:]) for l in lower):
                dep_found.append(m)
        elif any(os.path.basename(l) == m.lower() for l in lower):
            dep_found.append(m)

    langs = sorted(lang_counts.items(), key=lambda kv: -kv[1])
    return {
        "languages": [{"language": k, "files": v} for k, v in langs[:8]],
        "file_count": len(files),
        "structure": {"top_level_dirs": sorted(dirs)[:20], "dir_count": len(dirs)},
        "hygiene": {
            "dependency_manifests": sorted(set(dep_found)),
            "has_dependency_manifest": bool(dep_found),
            "has_license": bool(license_f),
            "has_gitignore": gitignore,
            "heavy_dirs_committed": sorted(set(heavy_committed)),
            "tests_present": tests_present,
        },
        "readme": _readme_check(root, readme),
    }


def _readme_check(root, readme_rel):
    info = {"has_readme": bool(readme_rel), "readme_file": readme_rel, "readme_lines": 0,
            "sections": [], "has_setup_instructions": False, "has_usage": False,
            "has_overview": False}
    if not readme_rel:
        return info
    try:
        with open(os.path.join(root, readme_rel), "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return info
    low = text.lower()
    info["readme_lines"] = text.count("\n") + 1
    info["sections"] = re.findall(r"(?m)^#{1,3}\s+(.+)$", text)[:20]
    info["has_setup_instructions"] = bool(
        re.search(r"install|setup|getting started|prerequisite|`pip |`npm |`docker", low))
    info["has_usage"] = bool(re.search(r"usage|how to|run |example|quickstart", low))
    info["has_overview"] = bool(re.search(r"overview|about|what (it|this)|problem", low))
    return info


def _omitted(note):
    return {"available": False, "note": note}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def run_selftest(root, window_start, window_end, threshold):
    root = os.path.abspath(root)
    is_git = os.path.exists(os.path.join(root, ".git"))  # worktree/submodule-safe, see note above
    files = _collect_files(root, is_git)

    result = {
        "self_test": True,
        "graded": False,
        "message": "This is NOT your graded scan. Run hackathon_scan.py before the deadline "
                   "for the real, required submission.",
        "repo": {"mode": "local", "path": root},
        "git": _git_stats(root, is_git, window_start, window_end, threshold),
    }
    result.update(_structure_and_hygiene(root, files))
    result["completeness"] = _omitted("Stub/TODO/entrypoint scan runs only in the real scan.")
    result["security"] = _omitted("Secret + dangerous-pattern scan runs only in the real scan.")
    result["agentic_design"] = _omitted("Agent/tool/guardrail characterization runs only in the real scan.")
    result["ai_evidence"] = _omitted("AI SDK/MCP/eval evidence runs only in the real scan.")
    result["git"]["cadence_flags"] = _omitted("Cadence/integrity flags run only in the real scan.")
    return result


def print_summary(team, data):
    g = data["git"]
    h = data["hygiene"]
    print(f"\n{'=' * 58}\n  {team}  (SELF-TEST -- not graded)\n{'=' * 58}")
    print(f"  Python OK, stdlib-only, no network used.")
    print(f"  Git repo detected: {'yes' if g.get('is_git_repo') else 'no'}")
    if g.get("is_git_repo"):
        print(f"  Commits: {g.get('total_commits', '?')}   Contributors: {g.get('contributor_count', '?')}")
        if g.get("window_checked"):
            flag = "OK" if g.get("worked_in_window") else "REVIEW - most work predates event"
            print(f"  In event window: {g.get('pct_commits_in_window')}%  [{flag}]")
    print(f"  Files scanned: {data['file_count']}")
    print(f"  Languages: " + (", ".join(l['language'] for l in data['languages'][:4]) or "none detected"))
    print("  --- Structural hygiene (informational only, no score) ---")
    # LICENSE presence isn't shown here (or scored in the real scan): hackathon submissions
    # are commercial/private code shipped early under time pressure, not a real quality signal.
    for label, ok in (
            ("dependency manifest", h["has_dependency_manifest"]),
            (".gitignore", h["has_gitignore"]),
            ("no build/dep dirs committed", not h["heavy_dirs_committed"]),
            ("tests present", h["tests_present"]),
            ("README present", data["readme"]["has_readme"]),
    ):
        print(f"    [{'PASS' if ok else 'MISS'}] {label}")
    print("  --- Not run in self-test (real scan only) ---")
    print("    completeness, security, agentic design, AI evidence, cadence/integrity flags")
    print(f"{'=' * 58}")
    print("  This tool computes NO score. It only confirms the scanner runs on your machine.")


def main():
    ap = argparse.ArgumentParser(
        description="Vista hackathon self-test (mechanics only -- NOT the graded scan)")
    ap.add_argument("--team", required=True, help="Team name (goes in the report)")
    ap.add_argument("--path", default=".", help="Repo path (default: .)")
    ap.add_argument("--start", default=DEFAULT_EVENT_START,
                    help=f"Event window start, ISO incl. UTC offset (default: {DEFAULT_EVENT_START})")
    ap.add_argument("--end", default=DEFAULT_EVENT_END,
                    help=f"Event window end, ISO incl. UTC offset (default: {DEFAULT_EVENT_END})")
    ap.add_argument("--threshold", type=float, default=DEFAULT_WINDOW_THRESHOLD,
                    help=f"%% of commits that must fall in-window (default {DEFAULT_WINDOW_THRESHOLD})")
    ap.add_argument("--out", help="Output JSON path (default: <team>_selftest.json)")
    args = ap.parse_args()

    print(f"  Event window: {args.start} -> {args.end}"
          + ("  (default)" if args.start == DEFAULT_EVENT_START and args.end == DEFAULT_EVENT_END
             else "  (overridden via --start/--end)"))

    try:
        data = run_selftest(args.path, args.start, args.end, args.threshold)
        payload = {
            "schema_version": SELFTEST_VERSION,
            "team": args.team,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "event_window": {"start": args.start, "end": args.end},
            **data,
        }
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", args.team).strip("_") or "team"
        out = args.out or f"{safe}_selftest.json"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

        print_summary(args.team, data)
        print(f"\n  -> wrote {out}")
        print(f"  -> this file is for YOUR reference only -- do not upload it anywhere.")
        print(f"  -> before the deadline, run hackathon_scan.py for the real, required scan.\n")
    except Exception as e:
        print(f"Self-test failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
