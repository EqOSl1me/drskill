"""Plugin/extension install stores: which suites a harness has installed
and where their ACTIVE skills live.

Each adapter reads only its harness's own state files and never globs for
active versions -- stores retain stale version dirs (observed live on
claude-code) and record enabled/disabled state that determines whether
skills load at all. Store facts are dated per adapter; see
docs/superpowers/specs/2026-08-14-plugin-stores-design.md.

Trust decision: installPath/cache_path values in state files are followed
verbatim after a string type check. A hostile state file could point one
at a huge tree and slow discovery to a crawl; drskill treats the user's
own harness state as trusted for WHERE to read (it validates shapes, not
intent), because the state files live beside the very configs every
harness already executes. Bounding roots to each store directory would
also break legitimate out-of-store installs (codex "local" version dirs).
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field


class InstalledPlugin(BaseModel):
    harness: str
    name: str
    marketplace: str | None = None
    version: str | None = None
    scope: Literal["user", "project"] = "user"
    project_path: Path | None = None
    skills_roots: list[Path] = Field(default_factory=list)
    enabled: bool = True
    recursive: bool = True
    evidence: Path

    @property
    def provenance_source(self) -> str:
        s = self.name
        if self.marketplace:
            s += f"@{self.marketplace}"
        if self.version:
            s += f"=={self.version}"
        return s


def _read_json(path: Path, unreadable: list[str]):
    """Parse a JSON file; None when missing, and None + an unreadable
    entry when present but unparseable."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        unreadable.append(str(path))
        return None


def _read_jsonc(path: Path, unreadable: list[str]):
    """JSON preceded by // comment lines (copilot's config.json style)."""
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        body = "\n".join(
            line for line in text.splitlines()
            if not line.lstrip().startswith("//")
        )
        return json.loads(body)
    except (OSError, ValueError):
        unreadable.append(str(path))
        return None


# --- claude-code -----------------------------------------------------------
# Observed live 2026-08-14: cache/<marketplace>/<plugin>/<version>/skills
# with STALE version dirs retained; ~/.claude/plugins/installed_plugins.json
# maps "name@marketplace" -> a list of installs, each {scope: "user" |
# "local"+projectPath, installPath, version}; ~/.claude/settings.json
# enabledPlugins maps the same key -> bool. A missing enabledPlugins key
# counts as enabled (installed implies usable; for a scanner, over-scanning
# is safer than missing surface). Claude Code namespaces plugin skills
# ("plugin:skill"), which resolution accounts for separately.
def _claude_code(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    state_path = home / ".claude" / "plugins" / "installed_plugins.json"
    data = _read_json(state_path, unreadable)
    if not isinstance(data, dict):
        if data is not None and str(state_path) not in unreadable:
            unreadable.append(str(state_path))
        return [], unreadable
    enabled_map: dict = {}
    settings = _read_json(home / ".claude" / "settings.json", unreadable)
    if isinstance(settings, dict) and isinstance(settings.get("enabledPlugins"), dict):
        enabled_map = settings["enabledPlugins"]
    out: list[InstalledPlugin] = []
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        if str(state_path) not in unreadable:
            unreadable.append(str(state_path))
        return [], unreadable
    for key, installs in sorted(plugins.items()):
        if not isinstance(installs, list):
            continue
        name, _, marketplace = key.partition("@")
        if not name:
            continue  # e.g. key "@mkt": empty name would defeat suite attribution
        for inst in installs:
            if not isinstance(inst, dict):
                continue
            install_path = inst.get("installPath")
            if install_path is not None and not isinstance(install_path, str):
                if str(state_path) not in unreadable:
                    unreadable.append(str(state_path))
                continue
            if not install_path:
                continue
            scope = inst.get("scope")
            if scope == "user":
                pscope, ppath = "user", None
            elif scope == "local":
                pp = inst.get("projectPath")
                if pp is not None and not isinstance(pp, str):
                    if str(state_path) not in unreadable:
                        unreadable.append(str(state_path))
                    continue
                if not pp:
                    continue
                try:
                    if Path(pp).resolve() != project_root.resolve():
                        continue
                except (ValueError, OSError):
                    if str(state_path) not in unreadable:
                        unreadable.append(str(state_path))
                    continue
                pscope, ppath = "project", Path(pp)
            else:
                continue
            version = inst.get("version")
            out.append(InstalledPlugin(
                harness="claude-code",
                name=name,
                marketplace=marketplace or None,
                version=str(version) if version is not None else None,
                scope=pscope,
                project_path=ppath,
                skills_roots=[Path(install_path) / "skills"],
                enabled=enabled_map.get(key) is not False,
                recursive=True,
                evidence=state_path,
            ))
    return out, unreadable


# --- codex ------------------------------------------------------------------
# Source-verified 2026-08-14 (openai/codex commit 5bc8da6, current main):
# cache at ~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/
# (core-plugins/src/store.rs:26-27,131-139); enablement in config.toml
# [plugins."name@marketplace"] enabled (config/src/plugin_edit.rs:21-34),
# disabled plugins' skills never load (core-plugins/src/loader.rs:843-845);
# active version prefers the "local" dir, else highest (store.rs:168-189);
# skills roots from the manifest's paths.skills, default skills/, plus
# .codex-plugin/migrated-command-skills (loader.rs:1059-1087). Legacy
# manifests scan recursively (depth <= 6), agent-plugins root plugin.json
# scans direct children only -- approximated here as recursive True/False.
# The $CODEX_HOME env override is ignored, matching how drskill already
# reads ~/.codex/config.toml for MCP. drskill's no-shadowing handling for
# codex (search_order "none") extends to plugin skills, matching source
# (ext/skills/src/loader/host_merge.rs:232-233).
_CODEX_LEGACY_MANIFESTS = (
    ".codex-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    ".cursor-plugin/plugin.json",
)


def _codex_version_key(v: str):
    # Numeric-aware ordering approximating codex's compare; "1.10.0" beats
    # "1.9.0". Non-numeric parts rank as 0 with the raw string as tiebreak.
    # isascii() guards against non-ASCII digits (e.g. "²" superscript
    # two) that satisfy str.isdigit() but make int() raise ValueError.
    return [int(p) if p.isascii() and p.isdigit() else 0 for p in v.split(".")], v


def _codex(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    config_path = home / ".codex" / "config.toml"
    if not config_path.is_file():
        return [], unreadable
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], [str(config_path)]
    plugins_tbl = config.get("plugins")
    if plugins_tbl is None:
        return [], unreadable  # no [plugins] table is normal (config.toml
        # is also used for MCP/model settings); only present-but-wrong-shape
        # counts as unreadable.
    if not isinstance(plugins_tbl, dict):
        if str(config_path) not in unreadable:
            unreadable.append(str(config_path))
        return [], unreadable
    out: list[InstalledPlugin] = []
    cache = home / ".codex" / "plugins" / "cache"
    for key, entry in sorted(plugins_tbl.items()):
        name, _, marketplace = key.partition("@")
        base = cache / (marketplace or "") / name
        if not base.is_dir():
            continue  # stale config entry, nothing installed
        versions = sorted(d.name for d in base.iterdir() if d.is_dir())
        if not versions:
            continue
        version = ("local" if "local" in versions
                   else max(versions, key=_codex_version_key))
        root = base / version
        manifest_path = root / "plugin.json"
        recursive = False  # agent-plugins root manifest: shallow scan
        if not manifest_path.is_file():
            recursive = True  # legacy manifest flavors scan recursively
            for rel in _CODEX_LEGACY_MANIFESTS:
                if (root / rel).is_file():
                    manifest_path = root / rel
                    break
            else:
                manifest_path = None
        skills_rel = ["skills"]
        if manifest_path is not None:
            manifest = _read_json(manifest_path, unreadable)
            if isinstance(manifest, dict):
                paths_val = manifest.get("paths")
                declared = paths_val.get("skills") if isinstance(paths_val, dict) else None
                if isinstance(declared, list) and declared:
                    skills_rel = [s for s in declared if isinstance(s, str)]
        roots = [root / rel for rel in skills_rel]
        migrated = root / ".codex-plugin" / "migrated-command-skills"
        if migrated.is_dir():
            roots.append(migrated)
        enabled = entry.get("enabled") is not False if isinstance(entry, dict) else True
        out.append(InstalledPlugin(
            harness="codex",
            name=name,
            marketplace=marketplace or None,
            version=version,
            scope="user",
            skills_roots=roots,
            enabled=enabled,
            recursive=recursive,
            evidence=config_path,
        ))
    return out, unreadable


# --- gemini-cli -------------------------------------------------------------
# Source-verified 2026-08-14 (google-gemini/gemini-cli commit c0d1924):
# store at ~/.gemini/extensions/<name>/ with identity in
# gemini-extension.json (required name, version; extensions/storage.ts:23-39,
# extension.ts:24-26); skills in a FIXED skills/ subdir loaded
# NON-recursively -- glob exactly SKILL.md + */SKILL.md
# (extension-manager.ts:921-922, skillLoader.ts:127). Enablement in
# extension-enablement.json: {name: {overrides: [rule, ...]}}; default
# enabled, rules iterate in file order and each MATCHING rule sets
# enabled = not-disable; "!" prefix = disable, trailing "*" = include
# subdirs, base rule is slash-wrapped; exact rules match only that dir
# (extensionEnablement.ts:146-178, 35-43, 102-108). Missing or corrupt
# file = everything enabled (:189-202). Extension skills rank below user
# and workspace skills (skillManager.ts:54-99), which discovery encodes
# by appending plugin roots after native paths.
def _gemini_enabled(name: str, config: dict, project_root: Path) -> bool:
    entry = config.get(name)
    overrides = entry.get("overrides") if isinstance(entry, dict) else None
    if not isinstance(overrides, list):
        return True
    cwd = "/" + str(project_root.resolve()).replace("\\", "/").strip("/") + "/"
    enabled = True
    for rule in overrides:
        if not isinstance(rule, str) or not rule:
            continue
        disable = rule.startswith("!")
        r = rule[1:] if disable else rule
        subdirs = r.endswith("*")
        base = r[:-1] if subdirs else r
        if cwd == base or (subdirs and cwd.startswith(base)):
            enabled = not disable
    return enabled


def _gemini_cli(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    ext_root = home / ".gemini" / "extensions"
    if not ext_root.is_dir():
        return [], unreadable
    enablement_path = ext_root / "extension-enablement.json"
    enablement = _read_json(enablement_path, unreadable)
    if not isinstance(enablement, dict):
        if enablement is not None and str(enablement_path) not in unreadable:
            unreadable.append(str(enablement_path))
        enablement = {}
    out: list[InstalledPlugin] = []
    for ext_dir in sorted(p for p in ext_root.iterdir() if p.is_dir()):
        manifest_path = ext_dir / "gemini-extension.json"
        manifest = _read_json(manifest_path, unreadable)
        if not isinstance(manifest, dict) or not manifest.get("name"):
            if manifest is not None and str(manifest_path) not in unreadable:
                unreadable.append(str(manifest_path))
            continue
        name = str(manifest["name"])
        out.append(InstalledPlugin(
            harness="gemini-cli",
            name=name,
            marketplace=None,
            version=str(manifest["version"]) if manifest.get("version") else None,
            scope="user",
            skills_roots=[ext_dir / "skills"],
            enabled=_gemini_enabled(name, enablement, project_root),
            recursive=False,
            evidence=manifest_path,
        ))
    return out, unreadable


# --- copilot ----------------------------------------------------------------
# Empirical 2026-08-14 (GitHub Copilot CLI 1.0.80, sandboxed install probe):
# store at ~/.copilot/installed-plugins/<marketplace>/<plugin>/ (whole
# plugin copied once, unversioned dirs) with skills under skills/; state in
# ~/.copilot/config.json (JSONC: comment lines precede the JSON) --
# installedPlugins[] with name/marketplace/version/cache_path -- and
# ~/.copilot/settings.json enabledPlugins keyed "name@marketplace".
# Collision rank probed stable: project > personal > plugin, which
# discovery encodes by appending plugin roots after native paths.
# Recursion inside a plugin's skills dir was not probed; harness default
# (recursive) assumed.
def _copilot(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    config_path = home / ".copilot" / "config.json"
    config = _read_jsonc(config_path, unreadable)
    if not isinstance(config, dict):
        if config is not None and str(config_path) not in unreadable:
            unreadable.append(str(config_path))
        return [], unreadable
    installed = config.get("installedPlugins")
    if not isinstance(installed, list):
        if installed is not None:
            if str(config_path) not in unreadable:
                unreadable.append(str(config_path))
        return [], unreadable
    enabled_map: dict = {}
    settings = _read_json(home / ".copilot" / "settings.json", unreadable)
    if isinstance(settings, dict) and isinstance(settings.get("enabledPlugins"), dict):
        enabled_map = settings["enabledPlugins"]
    out: list[InstalledPlugin] = []
    for entry in installed:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        name = str(entry["name"])
        marketplace = entry.get("marketplace")
        cache_path = entry.get("cache_path")
        if cache_path is not None and not isinstance(cache_path, str):
            if str(config_path) not in unreadable:
                unreadable.append(str(config_path))
            continue
        root = (Path(cache_path) if cache_path
                else home / ".copilot" / "installed-plugins" / str(marketplace or "") / name)
        key = f"{name}@{marketplace}" if marketplace else name
        out.append(InstalledPlugin(
            harness="copilot",
            name=name,
            marketplace=str(marketplace) if marketplace else None,
            version=str(entry["version"]) if entry.get("version") else None,
            scope="user",
            skills_roots=[root / "skills"],
            enabled=enabled_map.get(key) is not False,
            recursive=True,
            evidence=config_path,
        ))
    return out, unreadable


# --- droid ------------------------------------------------------------------
# Empirical 2026-08-14 (@factory/cli 0.196.0, sandboxed install probe):
# store at ~/.factory/plugins/cache/<mkt>-<hash>/<plugin>-<hash>/<installId>/
# with skills under skills/; one JSON per install under
# ~/.factory/plugins/installed_plugins/ ({schemaVersion, pluginId:
# "name@marketplace", entry: {scope, installPath, version}}). droid has no
# skill-enumeration verb, so THAT these cached skills load (and at what
# rank) is BEST-EFFORT, resting on prime-radiant-inc/everyharness's
# container install checks. Scopes other than "user" are skipped (only
# "user" observed).
def _droid(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    if not state_dir.is_dir():
        return [], unreadable
    out: list[InstalledPlugin] = []
    for state_path in sorted(state_dir.glob("*.json")):
        data = _read_json(state_path, unreadable)
        if not isinstance(data, dict):
            if data is not None and str(state_path) not in unreadable:
                unreadable.append(str(state_path))
            continue
        plugin_id = data.get("pluginId")
        entry = data.get("entry")
        if not isinstance(plugin_id, str) or not isinstance(entry, dict):
            if str(state_path) not in unreadable:
                unreadable.append(str(state_path))
            continue
        if entry.get("scope") != "user":
            continue
        install_path = entry.get("installPath")
        if install_path is not None and not isinstance(install_path, str):
            if str(state_path) not in unreadable:
                unreadable.append(str(state_path))
            continue
        if not install_path:
            continue
        name, _, marketplace = plugin_id.partition("@")
        out.append(InstalledPlugin(
            harness="droid",
            name=name,
            marketplace=marketplace or None,
            version=str(entry["version"]) if entry.get("version") else None,
            scope="user",
            skills_roots=[Path(install_path) / "skills"],
            enabled=entry.get("enabled") is not False,
            recursive=True,
            evidence=state_path,
        ))
    return out, unreadable


# pi (pi coding agent): packages are declared in a settings.json `packages`
# array -- user scope at ~/.pi/agent/settings.json, project scope at
# <cwd>/.pi/settings.json, read only after project trust (granted per dir in
# ~/.pi/agent/trust.json; findNearestTrustEntry walks cwd upward and the
# nearest boolean entry wins, trust-manager.js:21-33, settings-manager.js
# 257-260). Entries are source strings or {source, autoload} objects
# (package-manager.js resolvePackageSources: pkg may be either).
# Install roots (package-manager.js:1771-1812, getBaseDirForScope:1826):
# npm:<spec> -> <base>/npm/node_modules/<name>, with the @version stripped
# by parseNpmSpec's regex (scoped @names stay intact); git sources ->
# <base>/git/<host>/<owner>/<repo>, ref dropped from the path; local paths
# resolve against the scope's base dir (user: the agent dir; project:
# <cwd>/.pi). base = <home>/.pi/agent for user, <cwd>/.pi for project.
# NOT modeled (skipped, never guessed): legacy global-npm installs (npm
# root -g / pnpm global; resolving them means spawning a package manager,
# which drskill never does), PI_CODING_AGENT_DIR overrides (config.js:491;
# the agent dir is fixed to <home>/.pi/agent, matching harnesses.toml),
# per-resource pattern filters ({source, skills: [patterns]}) and
# standalone .md file entries (InstalledPlugin carries skill directories
# only), and exotic hosted-git spellings pi accepts via hosted-git-info.
# Skills (collectPackageResources + readPiManifest): the package.json `pi`
# manifest's skills entries win -- literal paths resolve against the
# package root (verbatim, like pi's resolve(root, entry)), globs expand
# with hidden segments filtered (expandPackageGlob), and entries starting
# with !/+/- are override patterns, not roots. With NO `pi` manifest at
# all (missing/unparseable package.json, or no pi key) the conventional
# skills/ dir loads; a pi manifest without a valid skills field contributes
# nothing (readPiManifest returns {}, truthy, so there is no fallback).
# Precedence (package-manager.js resolve():707-712, dedupePackages): project
# packages resolve FIRST so cwd resources win collisions; equal identities
# (npm:<name> / git:<host>/<path> / local resolved path) dedupe with project
# winning -- except a project entry with autoload:false, which is a delta
# over the user-scope copy and keeps BOTH. autoload:false (exact) disables.
_PI_AGENT_SUBDIR = Path(".pi") / "agent"
_PI_NPM_SPEC_RE = re.compile(r"(@?[^@]+(?:/[^@]+)?)(?:@(.+))?")
_PI_SCP_RE = re.compile(r"git@([^:]+):(.+)")


def _pi_read_json(path: Path, unreadable: list[str]):
    """JSON with BOM tolerated (pi stripBom); None + unreadable when bad."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        unreadable.append(str(path))
        return None


def _pi_unreadable(path: Path, unreadable: list[str]):
    if str(path) not in unreadable:
        unreadable.append(str(path))


def _pi_parse_npm_spec(spec: str) -> tuple[str, str | None]:
    m = _PI_NPM_SPEC_RE.fullmatch(spec)
    if not m:
        return spec, None
    return m.group(1), m.group(2)


def _pi_unsafe_git_part(value: str, *, allow_slash: bool) -> bool:
    """Mirror pi's hasUnsafeGitInstallPart (utils/git.ts): reject NUL,
    backslash, absolute paths, traversal segments, and (for the host)
    slashes. Percent-encoded forms are decoded first, like pi."""
    decoded = unquote(value)
    for cand in (value, decoded):
        if "\0" in cand or "\\" in cand or cand.startswith("/"):
            return True
        if not allow_slash and "/" in cand:
            return True
        if ".." in cand.split("/"):
            return True
    return False


def _pi_parse_git(source: str) -> tuple[str, str] | None:
    """(host, owner/repo) for the source shapes pi accepts; None when pi
    would treat the source as a local path or the shape is unrecognized
    (skipped rather than guessed). Mirrors parseSource + parseGitUrl +
    splitRef (package-manager.js:1170, utils/git.js:150)."""
    s = source.strip()
    if s.startswith("github:"):
        s = "github.com/" + s[len("github:"):].lstrip("/")
    elif s.startswith("git:"):
        s = s[len("git:"):].strip()
    elif not (_PI_SCP_RE.match(s) or re.match(r"^(https?|ssh|git)://", s)):
        return None  # anything else is a local path in pi
    repo, _, _frag_ref = s.partition("#")  # "#ref" suffixes split first
    if not repo:
        return None
    m = _PI_SCP_RE.fullmatch(repo)
    if m:
        host, path = m.group(1), m.group(2)
        if "@" in path:  # scp-like: first "@" in the path splits the ref
            path = path.split("@", 1)[0]
    elif "://" in repo:
        try:
            parsed = urlparse(repo)
        except ValueError:
            return None
        host = parsed.netloc.rsplit("@", 1)[-1]  # drop ssh user
        path = parsed.path.lstrip("/")
        if "@" in path:
            path = path.split("@", 1)[0]
    else:
        # bare host/owner/repo[@ref]: host up to the first "/", ref at "@"
        slash = repo.find("/")
        if slash < 0:
            return None
        host = repo[:slash]
        path = repo[slash + 1:]
        if "@" in path:
            path = path.split("@", 1)[0]
    raw_path = path
    path = path.removesuffix(".git").strip("/")
    parts = path.split("/")
    # pi requires at least two path components (host/<path>), so nested
    # hosts like gitlab subgroups are valid; it separately rejects unsafe
    # parts (traversal, absolute, backslash) when building the source.
    if not host or len(parts) < 2 or not all(parts):
        return None
    if raw_path.startswith("/") or path.startswith("/"):
        return None
    if _pi_unsafe_git_part(host, allow_slash=False) or _pi_unsafe_git_part(path, allow_slash=True):
        return None
    return host, path


def _pi_read_settings(path: Path, unreadable: list[str]) -> list[tuple[str, bool]] | None:
    """Declarations from a settings.json: (source, enabled) tuples; None
    when the file exists but cannot be honored (surfaced as unreadable,
    matching the claude-code adapter's whole-file contract)."""
    if not path.is_file():
        return []
    data = _pi_read_json(path, unreadable)
    if data is None:
        return None
    if not isinstance(data, dict):
        _pi_unreadable(path, unreadable)
        return None
    if "packages" not in data:
        return []  # no packages key is the normal case
    pkgs = data["packages"]
    if not isinstance(pkgs, list):
        _pi_unreadable(path, unreadable)
        return None
    out: list[tuple[str, bool]] = []
    for entry in pkgs:
        if isinstance(entry, str):
            out.append((entry, True))
        elif isinstance(entry, dict) and isinstance(entry.get("source"), str):
            out.append((entry["source"], entry.get("autoload") is not False))
        else:
            _pi_unreadable(path, unreadable)
            return None
    return out


def project_trusted(
    trust_path: Path, project_root: Path, unreadable: list[str] | None = None
) -> bool:
    """pi's ProjectTrustStore: trust.json maps canonical dirs to boolean
    decisions; the nearest ancestor entry of cwd wins, and only an exact
    true grants trust (trust-manager.js:21-33)."""
    if not trust_path.is_file():
        return False
    data = _pi_read_json(trust_path, unreadable if unreadable is not None else [])
    if not isinstance(data, dict):
        return False  # corrupt -> pi reads {} -> untrusted (surfaced above)
    cand = project_root.resolve()
    while True:
        v = data.get(str(cand))
        if isinstance(v, bool):
            return v
        if cand.parent == cand:
            return False
        cand = cand.parent


def _pi_identity(source: str, scope: str, agent: Path, proj_pi: Path) -> str | None:
    """pi's getPackageIdentity: remote sources are scope-independent,
    local paths resolve against the scope's base dir."""
    if source.startswith("npm:"):
        name, _ = _pi_parse_npm_spec(source[len("npm:"):].strip())
        return f"npm:{name}"
    git = _pi_parse_git(source)
    if git is not None:
        return f"git:{git[0]}/{git[1]}"
    p = Path(source).expanduser()
    base = agent if scope == "user" else proj_pi
    return "local:" + str(p if p.is_absolute() else (base / p).resolve())


def _pi_install_root(source: str, scope: str, agent: Path, proj_pi: Path):
    """-> (package root dir, fallback identity name) or None when the
    package is not installed (or is a local file: those load as
    extensions, which carry no skills)."""
    base = agent if scope == "user" else proj_pi
    if source.startswith("npm:"):
        name, _ = _pi_parse_npm_spec(source[len("npm:"):].strip())
        return base / "npm" / "node_modules" / name, name
    git = _pi_parse_git(source)
    if git is not None:
        host, path = git
        return base / "git" / host / path, path.rsplit("/", 1)[-1]
    p = Path(source).expanduser()
    p = p if p.is_absolute() else base / p
    if not p.exists() or p.is_file():
        return None
    return p, p.name


def _pi_skills_roots(root: Path) -> list[Path]:
    """Skill dirs a package contributes, per collectPackageResources:
    pi-manifest skills entries when a `pi` manifest exists, else the
    conventional skills/ dir."""
    data = _pi_read_json(root / "package.json", [])
    pi = data.get("pi") if isinstance(data, dict) else None
    if not isinstance(pi, dict):
        conv = root / "skills"
        return [conv] if conv.is_dir() else []
    entries = pi.get("skills")
    if not (isinstance(entries, list)
            and all(isinstance(e, str) for e in entries)):
        return []  # manifest exists but no valid skills field: no fallback
    roots: list[Path] = []
    for e in entries:
        if e.startswith(("!", "+", "-")):
            continue  # override patterns filter files; not roots
        if "*" in e or "?" in e:
            for m in sorted(root.glob(e.removeprefix("./"))):
                try:
                    rel = m.relative_to(root)
                except ValueError:
                    continue
                if any(p != ".." and p.startswith(".") for p in rel.parts):
                    continue  # expandPackageGlob drops hidden segments
                if m.is_dir():
                    roots.append(m)
        else:
            p = root / e  # resolved verbatim, like pi's resolve(root, entry)
            if p.is_dir():
                roots.append(p)
    out: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        if str(r) not in seen:
            seen.add(str(r))
            out.append(r)
    return out


def _pi_pkg_fields(root: Path, fallback_name: str) -> tuple[str, str | None]:
    """Name/version from package.json when readable; the checkout-derived
    fallback otherwise (other adapters str()-wrap versions too)."""
    data = _pi_read_json(root / "package.json", [])
    name, version = fallback_name, None
    if isinstance(data, dict):
        if isinstance(data.get("name"), str):
            name = data["name"]
        v = data.get("version")
        if isinstance(v, (str, int, float)) and not isinstance(v, bool):
            version = str(v)
    return name, version


def _pi(home: Path, project_root: Path) -> tuple[list[InstalledPlugin], list[str]]:
    unreadable: list[str] = []
    agent = home / _PI_AGENT_SUBDIR
    proj_pi = project_root / ".pi"
    user_path = agent / "settings.json"
    user_entries = _pi_read_settings(user_path, unreadable)
    project_entries: list[tuple[str, bool]] = []
    proj_path = proj_pi / "settings.json"
    if project_trusted(agent / "trust.json", project_root, unreadable):
        project_entries = _pi_read_settings(proj_path, unreadable) or []
    # project first so cwd resources win collisions; dedupe by identity
    ordered = [("project", s, en, proj_path) for s, en in project_entries]
    ordered += [("user", s, en, user_path) for s, en in user_entries or []]
    result: list[tuple[str, str, bool, Path]] = []
    seen: dict[str, int] = {}
    for scope, source, enabled, evidence in ordered:
        ident = _pi_identity(source, scope, agent, proj_pi)
        if ident is None:
            continue
        idx = seen.get(ident)
        if idx is None:
            seen[ident] = len(result)
            result.append((scope, source, enabled, evidence))
        elif result[idx][0] == "project" and scope == "user":
            if result[idx][2] is False:  # project autoload:false delta keeps both
                result.append((scope, source, enabled, evidence))
        elif scope == "project":
            result[idx] = (scope, source, enabled, evidence)  # project wins
    out: list[InstalledPlugin] = []
    for scope, source, enabled, evidence in result:
        root = _pi_install_root(source, scope, agent, proj_pi)
        if root is None:
            continue
        pkg_root, fallback = root
        skills_roots = _pi_skills_roots(pkg_root)
        if not skills_roots:
            continue
        name, version = _pi_pkg_fields(pkg_root, fallback)
        out.append(InstalledPlugin(
            harness="pi",
            name=name,
            version=version,
            scope=scope,
            project_path=project_root if scope == "project" else None,
            skills_roots=skills_roots,
            enabled=enabled,
            recursive=True,
            evidence=evidence,
        ))
    return out, unreadable


ADAPTERS: dict[str, Callable[[Path, Path], tuple[list[InstalledPlugin], list[str]]]] = {
    "claude-code": _claude_code,
    "codex": _codex,
    "gemini-cli": _gemini_cli,
    "copilot": _copilot,
    "droid": _droid,
    "pi": _pi,
}

# Best-known primary state path per harness, used only by the belt-and-braces
# fallback below when an adapter raises something its own guards missed.
_PRIMARY_STATE_PATH: dict[str, Callable[[Path], Path]] = {
    "claude-code": lambda home: home / ".claude" / "plugins" / "installed_plugins.json",
    "codex": lambda home: home / ".codex" / "config.toml",
    "gemini-cli": lambda home: home / ".gemini" / "extensions" / "extension-enablement.json",
    "copilot": lambda home: home / ".copilot" / "config.json",
    "droid": lambda home: home / ".factory" / "plugins" / "installed_plugins",
    "pi": lambda home: home / ".pi" / "agent" / "settings.json",
}


def discover_plugins(
    harness_id: str, home: Path, project_root: Path
) -> tuple[list[InstalledPlugin], list[str]]:
    adapter = ADAPTERS.get(harness_id)
    if adapter is None:
        return [], []
    try:
        return adapter(home, project_root)
    except Exception:
        # Never let an adapter exception escape scanning ("never a crash,
        # never a guess"): degrade to unreadable rather than propagating.
        path_fn = _PRIMARY_STATE_PATH.get(harness_id)
        if path_fn is not None:
            try:
                return [], [str(path_fn(home))]
            except Exception:
                return [], []
        return [], []
