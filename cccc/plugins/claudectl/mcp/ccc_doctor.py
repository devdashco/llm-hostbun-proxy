#!/usr/bin/env python3
"""cccc doctor — Claude Code LSP doctor for cmux workspaces.

Claude Code consumes Language Servers through the official `*-lsp` plugins
(`claude-plugins-official`). Those plugins are **global, one-per-language** —
you enable `typescript-lsp` ONCE and it serves every TS project. There is no
per-workspace LSP; installing one per repo is the wrong model (and Claude even
does it by accident when you accept its per-project recommendation).

This tool audits that: it walks your cmux workspaces, detects the language of
each, maps language -> required LSP plugin, and checks the plugin is enabled at
**user** scope in ~/.claude/settings.json. Gaps are reported; --fix enables the
missing ones globally. Per-project (scope:project) LSP installs are flagged as
an anti-pattern.

  cccc doctor          # audit + report
  cccc doctor --fix    # enable every missing LSP plugin at user scope
  cccc doctor --json   # machine-readable
  cccc doctor --base DIR  # scan a folder tree instead of cmux workspaces

Pure stdlib. `claude` CLI is only needed for --fix.
"""
# REQUIRED: cron and the cmux Dock run this via the SYSTEM python3 (3.9 on
# macOS), which cannot evaluate `str | None` annotations at import time.
from __future__ import annotations
import json
import os
import subprocess
import sys

HOME = os.path.expanduser("~")
SETTINGS = os.path.join(HOME, ".claude", "settings.json")
INSTALLED = os.path.join(HOME, ".claude", "plugins", "installed_plugins.json")
MARKETPLACE = "claude-plugins-official"

# language -> the LSP plugin that serves it (bare name; @marketplace appended).
LANG_LSP = {
    "TS/JS": "typescript-lsp",
    "Python": "pyright-lsp",
    "Go": "gopls-lsp",
    "Rust": "rust-analyzer-lsp",
    "Ruby": "ruby-lsp",
    "PHP": "php-lsp",
    "Lua": "lua-lsp",
    "C#": "csharp-lsp",
    "Java": "jdtls-lsp",
    "Kotlin": "kotlin-lsp",
    "Swift": "swift-lsp",
    "C/C++": "clangd-lsp",
}

# dirs we never descend into when sniffing a project's languages.
PRUNE = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist",
         "build", ".next", "target", "vendor", ".cache", "coverage"}

# ---- terminal colour (no-op when not a tty) -------------------------------
_TTY = sys.stdout.isatty()
def c(s, code): return f"\033[{code}m{s}\033[0m" if _TTY else s
def green(s):  return c(s, "32")
def red(s):    return c(s, "31")
def yellow(s): return c(s, "33")
def dim(s):    return c(s, "2")
def bold(s):   return c(s, "1")


def detect_langs(root: str) -> set[str]:
    """Best-effort language set for a project dir. Marker files first (cheap,
    authoritative), then a shallow bounded file-extension sniff."""
    langs: set[str] = set()
    if os.path.isfile(os.path.join(root, "package.json")):
        langs.add("TS/JS")
    if os.path.isfile(os.path.join(root, "go.mod")):
        langs.add("Go")
    if os.path.isfile(os.path.join(root, "Cargo.toml")):
        langs.add("Rust")
    if os.path.isfile(os.path.join(root, "Gemfile")):
        langs.add("Ruby")
    if os.path.isfile(os.path.join(root, "composer.json")):
        langs.add("PHP")
    ext_lang = {
        ".py": "Python", ".rb": "Ruby", ".go": "Go", ".rs": "Rust",
        ".php": "PHP", ".lua": "Lua", ".cs": "C#", ".java": "Java",
        ".kt": "Kotlin", ".swift": "Swift", ".c": "C/C++", ".cpp": "C/C++",
        ".ts": "TS/JS", ".tsx": "TS/JS", ".js": "TS/JS", ".jsx": "TS/JS",
    }
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # bound the walk: prune noise, cap depth at 3, cap files scanned.
        dirnames[:] = [d for d in dirnames if d not in PRUNE
                       and not d.startswith(".")]
        depth = os.path.relpath(dirpath, root).count(os.sep)
        if depth >= 3:
            dirnames[:] = []
        for f in filenames:
            _, e = os.path.splitext(f)
            lang = ext_lang.get(e)
            if lang:
                langs.add(lang)
            seen += 1
            if seen > 4000:
                return langs
    return langs


def load_enabled() -> dict:
    """map plugin-id -> bool from ~/.claude/settings.json enabledPlugins."""
    try:
        with open(SETTINGS) as fh:
            return json.load(fh).get("enabledPlugins", {})
    except Exception:
        return {}


def per_project_lsp() -> list[tuple[str, str]]:
    """(plugin, projectPath) for any *-lsp installed at scope=project — the
    anti-pattern we want to surface."""
    out = []
    try:
        with open(INSTALLED) as fh:
            plugins = json.load(fh).get("plugins", {})
    except Exception:
        return out
    for pid, entries in plugins.items():
        if "lsp" not in pid.lower():
            continue
        for e in entries if isinstance(entries, list) else []:
            if e.get("scope") == "project":
                out.append((pid, e.get("projectPath", "?")))
    return out


def cmux_workspaces() -> list[tuple[str, str]] | None:
    """[(name, cwd)] from cmux, or None if cmux isn't reachable."""
    env = {**os.environ, "CMUX_QUIET": "1"}
    try:
        raw = subprocess.run(["cmux", "workspace", "list", "--json"],
                             capture_output=True, text=True, env=env).stdout
        ws = json.loads(raw)["workspaces"]
    except Exception:
        return None
    out = []
    for w in ws:
        cd = (w.get("current_directory") or "").rstrip("/")
        if cd:
            name = w.get("custom_title") or os.path.basename(cd)
            out.append((name, cd))
    return out


def scan_base(base: str) -> list[tuple[str, str]]:
    base = os.path.expanduser(base)
    out = []
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name)
        if os.path.isdir(p) and not name.startswith("."):
            out.append((name, p))
    return out


def main() -> int:
    argv = sys.argv[1:]
    do_fix = "--fix" in argv
    as_json = "--json" in argv
    open_only = "--open" in argv
    base = None
    if "--base" in argv:
        base = argv[argv.index("--base") + 1]

    # LSP plugins are GLOBAL, so coverage is a fleet-wide question, not a
    # "which workspaces are open right now" question. Default therefore audits
    # the whole fleet (the common parent of your cmux workspaces, or
    # ~/Documents/GitHub). `--open` restricts to currently-open cmux workspaces;
    # `--base DIR` scans an explicit tree.
    if base:
        projects = scan_base(base)
        source = f"folder tree {base}"
    elif open_only:
        projects = cmux_workspaces() or []
        source = f"{len(projects)} open cmux workspaces (subset — use no flag for full fleet)"
    else:
        ws = cmux_workspaces() or []
        # derive the fleet root from where the open workspaces actually live.
        parents = {os.path.dirname(os.path.realpath(cwd)) for _, cwd in ws}
        fleet = None
        if len(parents) == 1:
            fleet = parents.pop()
        for cand in ([fleet] if fleet else []) + [os.path.join(HOME, "Documents", "GitHub")]:
            if cand and os.path.isdir(cand):
                fleet = cand
                break
        projects = scan_base(fleet)
        source = f"full fleet {fleet.replace(HOME, '~')} ({len(projects)} repos)"

    # dedup by resolved path (cmux often has several workspaces per repo).
    seen_paths, uniq = set(), []
    for name, cwd in projects:
        rp = os.path.realpath(cwd)
        if rp in seen_paths:
            continue
        seen_paths.add(rp)
        uniq.append((name, cwd))

    enabled = load_enabled()
    def is_on(plugin):
        return enabled.get(f"{plugin}@{MARKETPLACE}", False) is True

    # per-project language detection + fleet-wide language tally.
    rows = []          # (name, cwd, langs, missing_plugins)
    lang_repos: dict[str, int] = {}
    needed_plugins: dict[str, set[str]] = {}   # plugin -> {langs}
    for name, cwd in uniq:
        langs = detect_langs(cwd)
        for lang in langs:
            lang_repos[lang] = lang_repos.get(lang, 0) + 1
        missing = []
        for lang in langs:
            plugin = LANG_LSP.get(lang)
            if not plugin:
                continue
            needed_plugins.setdefault(plugin, set()).add(lang)
            if not is_on(plugin):
                missing.append(plugin)
        rows.append((name, cwd, sorted(langs), sorted(set(missing))))

    # which needed plugins are off, and how many repos each covers.
    plugin_repo_count: dict[str, int] = {}
    for lang, n in lang_repos.items():
        pl = LANG_LSP.get(lang)
        if pl:
            plugin_repo_count[pl] = plugin_repo_count.get(pl, 0) + n
    missing_plugins = sorted(p for p in needed_plugins if not is_on(p))
    orphans = per_project_lsp()

    if as_json:
        print(json.dumps({
            "source": source,
            "projects": len(uniq),
            "languages": lang_repos,
            "enabled_lsp": sorted(p for p in LANG_LSP.values() if is_on(p)),
            "missing_lsp": {p: sorted(needed_plugins[p]) for p in missing_plugins},
            "plugin_repo_count": plugin_repo_count,
            "per_project_installs": orphans,
        }, indent=2))
        return 0

    # ---- human report ------------------------------------------------------
    print(bold(f"cccc doctor — LSP doctor  ({source}, {len(uniq)} unique projects)\n"))

    print(bold("Languages in your fleet:"))
    for lang, n in sorted(lang_repos.items(), key=lambda kv: -kv[1]):
        pl = LANG_LSP.get(lang, "—")
        state = green("on ") if (pl != "—" and is_on(pl)) else red("OFF")
        note = "" if pl == "—" else f"  {dim('via ' + pl)}  [{state}]"
        print(f"  {lang:<8} {n:>3} repos{note}")

    print(bold("\nLSP coverage:"))
    covered = [p for p in needed_plugins if is_on(p)]
    for p in sorted(covered):
        print(f"  {green('✓')} {p:<20} {dim(str(plugin_repo_count.get(p,0)) + ' repos')}")
    for p in missing_plugins:
        n = plugin_repo_count.get(p, 0)
        tag = yellow("optional") if n <= 2 else red("MISSING")
        langs = "/".join(sorted(needed_plugins[p]))
        print(f"  {red('✗')} {p:<20} {dim(str(n)+' repos ('+langs+')'):<24} {tag}")

    if orphans:
        print(bold(f"\n⚠  {len(orphans)} per-project LSP install(s) "
                   f"(anti-pattern — LSP should be user-scope):"))
        for pid, path in orphans:
            print(f"  {yellow('•')} {pid.split('@')[0]:<18} {dim(path)}")

    # ---- verdict / fix -----------------------------------------------------
    real_missing = [p for p in missing_plugins if plugin_repo_count.get(p, 0) > 2]
    print()
    if not real_missing:
        print(green("✓ Every language with real coverage in your fleet has its "
                    "LSP enabled at user scope."))
    else:
        print(red(f"✗ {len(real_missing)} LSP plugin(s) missing for languages "
                  f"you actually use: ") + ", ".join(real_missing))

    if do_fix:
        to_enable = missing_plugins  # enable everything a repo needs
        if not to_enable:
            print(green("\nNothing to fix."))
            return 0
        print(bold(f"\nEnabling {len(to_enable)} plugin(s) at user scope:"))
        rc = 0
        for p in to_enable:
            pid = f"{p}@{MARKETPLACE}"
            r = subprocess.run(["claude", "plugin", "enable", pid],
                               capture_output=True, text=True)
            ok = r.returncode == 0
            rc |= 0 if ok else 1
            msg = (r.stdout + r.stderr).strip().splitlines()
            tail = msg[-1] if msg else ""
            print(f"  {green('✓') if ok else red('✗')} {pid}   {dim(tail[:70])}")
        print(dim("\nRestart claude sessions so the servers spin up."))
        return rc

    if real_missing or orphans:
        print(dim("\nrun `cccc doctor --fix` to enable missing LSP plugins at user scope."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
