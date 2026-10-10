import json
from pathlib import Path

from drskill.stores import InstalledPlugin, discover_plugins


def _skill(root: Path, name: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nbody\n", encoding="utf-8"
    )


def _cc_state(home: Path, plugins: dict) -> Path:
    p = home / ".claude" / "plugins" / "installed_plugins.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "plugins": plugins}), encoding="utf-8")
    return p


def _cc_cache(home: Path, marketplace: str, plugin: str, version: str) -> Path:
    d = home / ".claude" / "plugins" / "cache" / marketplace / plugin / version
    _skill(d / "skills", f"{plugin}-skill")
    return d


def test_claude_code_active_user_install(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    active = _cc_cache(home, "mkt", "sp", "6.0.0")
    _cc_cache(home, "mkt", "sp", "4.0.0")  # stale: must NOT be returned
    _cc_state(home, {"sp@mkt": [
        {"scope": "user", "installPath": str(active), "version": "6.0.0"},
    ]})
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "sp" and p.marketplace == "mkt" and p.version == "6.0.0"
    assert p.scope == "user" and p.enabled and p.recursive
    assert p.skills_roots == [active / "skills"]
    assert p.provenance_source == "sp@mkt==6.0.0"


def test_claude_code_local_scope_only_matches_its_project(tmp_path):
    home = tmp_path / "home"
    mine, other = tmp_path / "mine", tmp_path / "other"
    mine.mkdir(); other.mkdir()
    active = _cc_cache(home, "mkt", "sp", "4.0.0")
    _cc_state(home, {"sp@mkt": [
        {"scope": "local", "projectPath": str(mine),
         "installPath": str(active), "version": "4.0.0"},
    ]})
    plugins, _ = discover_plugins("claude-code", home, mine)
    (p,) = plugins
    assert p.scope == "project" and p.project_path == mine
    assert discover_plugins("claude-code", home, other)[0] == []


def test_claude_code_enabled_plugins(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    a = _cc_cache(home, "mkt", "a", "1.0.0")
    b = _cc_cache(home, "mkt", "b", "1.0.0")
    _cc_state(home, {
        "a@mkt": [{"scope": "user", "installPath": str(a), "version": "1.0.0"}],
        "b@mkt": [{"scope": "user", "installPath": str(b), "version": "1.0.0"}],
    })
    settings = home / ".claude" / "settings.json"
    # explicit false disables; a MISSING key counts as enabled
    settings.write_text(json.dumps({"enabledPlugins": {"a@mkt": False}}))
    plugins, _ = discover_plugins("claude-code", home, proj)
    by_name = {p.name: p for p in plugins}
    assert by_name["a"].enabled is False
    assert by_name["b"].enabled is True


def test_claude_code_malformed_state_is_unreadable_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = home / ".claude" / "plugins" / "installed_plugins.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_missing_state_and_unknown_harness_are_empty(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    home.mkdir(); proj.mkdir()
    assert discover_plugins("claude-code", home, proj) == ([], [])
    assert discover_plugins("pi", home, proj) == ([], [])


def test_claude_code_valid_json_wrong_shape_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = home / ".claude" / "plugins" / "installed_plugins.json"
    p.parent.mkdir(parents=True)
    p.write_text("[]", encoding="utf-8")
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_claude_code_missing_plugins_key_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = home / ".claude" / "plugins" / "installed_plugins.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"version": 1}), encoding="utf-8")
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def _codex_config(home: Path, text: str) -> Path:
    p = home / ".codex" / "config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _codex_cache(home: Path, marketplace: str, plugin: str, version: str,
                 manifest: str = "plugin.json") -> Path:
    d = home / ".codex" / "plugins" / "cache" / marketplace / plugin / version
    _skill(d / "skills", f"{plugin}-skill")
    (d / manifest).parent.mkdir(parents=True, exist_ok=True)
    (d / manifest).write_text(
        json.dumps({"name": plugin, "version": version}), encoding="utf-8"
    )
    return d


def test_codex_prefers_local_version_dir(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_cache(home, "mkt", "sp", "1.0.0")
    local = _codex_cache(home, "mkt", "sp", "local")
    _codex_config(home, '[plugins."sp@mkt"]\nenabled = true\n')
    plugins, _ = discover_plugins("codex", home, proj)
    (p,) = plugins
    assert p.skills_roots == [local / "skills"]
    assert p.version == "local"


def test_codex_highest_version_when_no_local(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_cache(home, "mkt", "sp", "1.9.0")
    high = _codex_cache(home, "mkt", "sp", "1.10.0")  # numeric, not lexicographic
    _codex_config(home, '[plugins."sp@mkt"]\nenabled = true\n')
    plugins, _ = discover_plugins("codex", home, proj)
    assert plugins[0].skills_roots == [high / "skills"]


def test_codex_disabled_and_missing_cache(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_cache(home, "mkt", "off", "1.0.0")
    _codex_config(home, (
        '[plugins."off@mkt"]\nenabled = false\n'
        '[plugins."gone@mkt"]\nenabled = true\n'  # no cache dir: skipped
    ))
    plugins, _ = discover_plugins("codex", home, proj)
    (p,) = plugins
    assert p.name == "off" and p.enabled is False


def test_codex_agent_plugin_manifest_is_shallow_legacy_recursive(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_cache(home, "mkt", "ap", "1.0.0", manifest="plugin.json")
    _codex_cache(home, "mkt", "leg", "1.0.0",
                 manifest=".codex-plugin/plugin.json")
    _codex_config(home, (
        '[plugins."ap@mkt"]\nenabled = true\n'
        '[plugins."leg@mkt"]\nenabled = true\n'
    ))
    plugins, _ = discover_plugins("codex", home, proj)
    by_name = {p.name: p for p in plugins}
    assert by_name["ap"].recursive is False
    assert by_name["leg"].recursive is True


def test_codex_manifest_skills_paths_and_migrated_dir(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    d = home / ".codex" / "plugins" / "cache" / "mkt" / "sp" / "1.0.0"
    (d / ".codex-plugin" / "migrated-command-skills").mkdir(parents=True)
    (d / "myskills").mkdir()
    (d / ".codex-plugin" / "plugin.json").write_text(json.dumps(
        {"name": "sp", "version": "1.0.0", "paths": {"skills": ["myskills"]}}
    ))
    _codex_config(home, '[plugins."sp@mkt"]\nenabled = true\n')
    plugins, _ = discover_plugins("codex", home, proj)
    (p,) = plugins
    assert p.skills_roots == [
        d / "myskills", d / ".codex-plugin" / "migrated-command-skills"
    ]


def test_codex_valid_toml_wrong_shaped_plugins_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = _codex_config(home, 'plugins = "not-a-table"\n')
    plugins, unreadable = discover_plugins("codex", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def _gemini_ext(home: Path, name: str, version: str = "1.0.0") -> Path:
    d = home / ".gemini" / "extensions" / name
    _skill(d / "skills", f"{name}-skill")
    (d / "gemini-extension.json").write_text(
        json.dumps({"name": name, "version": version}), encoding="utf-8"
    )
    return d


def test_gemini_extension_discovered_nonrecursive(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    d = _gemini_ext(home, "ext-a")
    plugins, unreadable = discover_plugins("gemini-cli", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "ext-a" and p.marketplace is None
    assert p.version == "1.0.0" and p.enabled
    assert p.recursive is False  # loader globs SKILL.md + */SKILL.md only
    assert p.skills_roots == [d / "skills"]
    assert p.provenance_source == "ext-a==1.0.0"


def test_gemini_enablement_rules(tmp_path):
    # Semantics from extensionEnablement.ts (commit c0d1924): default
    # enabled; overrides iterate in file order, each matching rule sets
    # enabled = not-disable; "!" = disable; trailing "*" = include
    # subdirs; exact rule matches only that dir.
    home = tmp_path / "home"
    proj = tmp_path / "work" / "sub"
    proj.mkdir(parents=True)
    _gemini_ext(home, "ext-a")
    enablement = home / ".gemini" / "extensions" / "extension-enablement.json"
    work = str((tmp_path / "work").resolve())
    enablement.write_text(json.dumps({
        "ext-a": {"overrides": [f"!{work}/*", f"{str(proj.resolve())}/"]}
    }), encoding="utf-8")
    # disabled under work/*, but the later exact rule re-enables at proj
    plugins, _ = discover_plugins("gemini-cli", home, proj)
    assert plugins[0].enabled is True
    # a sibling dir under work/ only matches the disable rule
    sib = tmp_path / "work" / "other"
    sib.mkdir()
    plugins, _ = discover_plugins("gemini-cli", home, sib)
    assert plugins[0].enabled is False


def test_gemini_exact_rule_does_not_match_subdir(tmp_path):
    home = tmp_path / "home"
    proj = tmp_path / "work" / "sub"
    proj.mkdir(parents=True)
    _gemini_ext(home, "ext-a")
    enablement = home / ".gemini" / "extensions" / "extension-enablement.json"
    enablement.write_text(json.dumps({
        "ext-a": {"overrides": [f"!{str((tmp_path / 'work').resolve())}/"]}
    }), encoding="utf-8")
    plugins, _ = discover_plugins("gemini-cli", home, proj)
    assert plugins[0].enabled is True  # exact rule matches work/ only


def test_gemini_corrupt_enablement_defaults_enabled(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _gemini_ext(home, "ext-a")
    enablement = home / ".gemini" / "extensions" / "extension-enablement.json"
    enablement.write_text("{broken", encoding="utf-8")
    plugins, unreadable = discover_plugins("gemini-cli", home, proj)
    # matches gemini's own behavior: corrupt file -> everything enabled;
    # drskill additionally surfaces the file as unreadable
    assert plugins[0].enabled is True
    assert unreadable == [str(enablement)]


def test_gemini_wrong_shaped_enablement_defaults_enabled_and_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _gemini_ext(home, "ext-a")
    enablement = home / ".gemini" / "extensions" / "extension-enablement.json"
    enablement.write_text("[]", encoding="utf-8")
    plugins, unreadable = discover_plugins("gemini-cli", home, proj)
    # valid JSON but wrong shape (array instead of dict) -> extension still enabled;
    # file surfaces as unreadable
    assert plugins[0].enabled is True
    assert unreadable == [str(enablement)]


def test_copilot_installed_plugin_with_jsonc_header(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    store = home / ".copilot" / "installed-plugins" / "mkt" / "sp"
    _skill(store / "skills", "sp-skill")
    cfg = home / ".copilot" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # copilot writes comment lines BEFORE the JSON body (observed 1.0.80)
    cfg.write_text(
        "// User settings belong in settings.json.\n"
        "// This file is managed automatically.\n"
        + json.dumps({"installedPlugins": [
            {"name": "sp", "marketplace": "mkt", "version": "1.2.3",
             "cache_path": str(store)},
        ]}),
        encoding="utf-8",
    )
    (home / ".copilot" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"sp@mkt": True}}), encoding="utf-8"
    )
    plugins, unreadable = discover_plugins("copilot", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "sp" and p.marketplace == "mkt" and p.version == "1.2.3"
    assert p.skills_roots == [store / "skills"]
    assert p.enabled and p.scope == "user"


def test_copilot_disabled_and_default_store_path(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    store = home / ".copilot" / "installed-plugins" / "mkt" / "sp"
    _skill(store / "skills", "sp-skill")
    cfg = home / ".copilot" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # no cache_path: fall back to installed-plugins/<mkt>/<name>
    cfg.write_text(json.dumps({"installedPlugins": [
        {"name": "sp", "marketplace": "mkt", "version": "1.2.3"},
    ]}), encoding="utf-8")
    (home / ".copilot" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"sp@mkt": False}}), encoding="utf-8"
    )
    plugins, _ = discover_plugins("copilot", home, proj)
    (p,) = plugins
    assert p.skills_roots == [store / "skills"]
    assert p.enabled is False


def test_copilot_config_json_valid_but_wrong_shape_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    cfg = home / ".copilot" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("[]", encoding="utf-8")
    plugins, unreadable = discover_plugins("copilot", home, proj)
    assert plugins == [] and unreadable == [str(cfg)]


def test_copilot_installed_plugins_present_but_not_list_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    cfg = home / ".copilot" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"installedPlugins": "not-a-list"}), encoding="utf-8")
    plugins, unreadable = discover_plugins("copilot", home, proj)
    assert plugins == [] and unreadable == [str(cfg)]


def test_droid_installed_plugins(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    install = home / ".factory" / "plugins" / "cache" / "mkt-ab" / "sp-cd" / "v1"
    _skill(install / "skills", "sp-skill")
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    (state_dir / "sp-mkt-user-1234.json").write_text(json.dumps({
        "schemaVersion": 1,
        "pluginId": "sp@mkt",
        "entry": {"scope": "user", "installPath": str(install), "version": "v1"},
    }), encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "sp" and p.marketplace == "mkt" and p.version == "v1"
    assert p.skills_roots == [install / "skills"]
    assert p.scope == "user" and p.enabled


def test_droid_malformed_entry_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    bad = state_dir / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert plugins == [] and unreadable == [str(bad)]


def test_droid_per_install_json_valid_but_wrong_shape_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    bad = state_dir / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert plugins == [] and unreadable == [str(bad)]


def test_droid_entry_missing_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    bad = state_dir / "missing_entry.json"
    bad.write_text(json.dumps({"schemaVersion": 1, "pluginId": "sp@mkt"}), encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert plugins == [] and unreadable == [str(bad)]


def test_droid_scope_not_user_is_skipped_not_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    install = home / ".factory" / "plugins" / "cache" / "mkt-ab" / "sp-cd" / "v1"
    _skill(install / "skills", "sp-skill")
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    (state_dir / "sp-mkt-org-1234.json").write_text(json.dumps({
        "schemaVersion": 1,
        "pluginId": "sp@mkt",
        "entry": {"scope": "organization", "installPath": str(install), "version": "v1"},
    }), encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert plugins == [] and unreadable == []


# --- adversarial / corrupt state (final review, critical 1 & minor 6) -----

def test_claude_code_int_install_path_is_unreadable_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = _cc_state(home, {"sp@mkt": [
        {"scope": "user", "installPath": 123, "version": "1.0.0"},
    ]})
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_claude_code_int_project_path_is_unreadable_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    active = _cc_cache(home, "mkt", "sp", "1.0.0")
    p = _cc_state(home, {"sp@mkt": [
        {"scope": "local", "projectPath": 5,
         "installPath": str(active), "version": "1.0.0"},
    ]})
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_claude_code_nul_byte_project_path_is_unreadable_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    active = _cc_cache(home, "mkt", "sp", "1.0.0")
    p = _cc_state(home, {"sp@mkt": [
        {"scope": "local", "projectPath": "bad\x00path",
         "installPath": str(active), "version": "1.0.0"},
    ]})
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_claude_code_int_version_is_stringified_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    active = _cc_cache(home, "mkt", "sp", "6.0.0")
    _cc_state(home, {"sp@mkt": [
        {"scope": "user", "installPath": str(active), "version": 6},
    ]})
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.version == "6"  # other adapters str()-wrap version; match them


def test_claude_code_empty_plugin_name_is_skipped(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    active = _cc_cache(home, "mkt", "sp", "1.0.0")
    _cc_state(home, {"@mkt": [
        {"scope": "user", "installPath": str(active), "version": "1.0.0"},
    ]})
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    # key "@mkt" partitions to name "" -- an empty name silently defeats
    # suites' pre-attribution skip, so it must be dropped entirely
    assert plugins == [] and unreadable == []


def test_codex_non_dict_manifest_paths_no_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    d = home / ".codex" / "plugins" / "cache" / "mkt" / "sp" / "1.0.0"
    _skill(d / "skills", "sp-skill")
    # "paths" is truthy but not a dict/table -- (manifest.get("paths") or {})
    # would previously call .get() on the string and raise AttributeError
    (d / "plugin.json").write_text(
        json.dumps({"name": "sp", "version": "1.0.0", "paths": "skills"})
    )
    _codex_config(home, '[plugins."sp@mkt"]\nenabled = true\n')
    plugins, unreadable = discover_plugins("codex", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.skills_roots == [d / "skills"]  # falls back to the default


def test_codex_non_ascii_digit_version_dir_no_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    good = _codex_cache(home, "mkt", "sp", "1.0.0")
    # "²" (superscript two) satisfies str.isdigit() but int() raises
    # ValueError on it -- a directory named this way must not crash sorting
    bad_dir = home / ".codex" / "plugins" / "cache" / "mkt" / "sp" / "².0"
    bad_dir.mkdir(parents=True)
    _codex_config(home, '[plugins."sp@mkt"]\nenabled = true\n')
    plugins, unreadable = discover_plugins("codex", home, proj)
    (p,) = plugins
    assert p.skills_roots == [good / "skills"]
    assert p.version == "1.0.0"


def test_codex_missing_plugins_table_is_normal_not_unreadable(tmp_path):
    # config.toml exists for MCP/model settings with no [plugins] table at
    # all -- this is the NORMAL case, not unreadable state (critical 2).
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _codex_config(home, '[model]\nname = "x"\n')
    plugins, unreadable = discover_plugins("codex", home, proj)
    assert plugins == [] and unreadable == []


def test_codex_invalid_toml_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = _codex_config(home, 'not = valid = toml =\n')
    plugins, unreadable = discover_plugins("codex", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_copilot_int_cache_path_is_unreadable_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    cfg = home / ".copilot" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"installedPlugins": [
        {"name": "sp", "marketplace": "mkt", "version": "1.2.3", "cache_path": 123},
    ]}), encoding="utf-8")
    plugins, unreadable = discover_plugins("copilot", home, proj)
    assert plugins == [] and unreadable == [str(cfg)]


def test_droid_int_install_path_is_unreadable_not_crash(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    state_dir = home / ".factory" / "plugins" / "installed_plugins"
    state_dir.mkdir(parents=True)
    bad = state_dir / "sp-mkt-user-1234.json"
    bad.write_text(json.dumps({
        "schemaVersion": 1,
        "pluginId": "sp@mkt",
        "entry": {"scope": "user", "installPath": 123, "version": "v1"},
    }), encoding="utf-8")
    plugins, unreadable = discover_plugins("droid", home, proj)
    assert plugins == [] and unreadable == [str(bad)]


def test_discover_plugins_never_crashes_on_unexpected_adapter_exception(tmp_path, monkeypatch):
    from drskill import stores as stores_mod

    def boom(home, project_root):
        raise RuntimeError("simulated adapter bug, unforeseen by per-field guards")

    monkeypatch.setitem(stores_mod.ADAPTERS, "claude-code", boom)
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == []
    assert unreadable == [str(home / ".claude" / "plugins" / "installed_plugins.json")]


def test_claude_code_multiple_bad_installs_single_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    p = _cc_state(home, {
        "a@m": [{"scope": "user", "installPath": 123, "version": "1"}],
        "b@m": [{"scope": "user", "installPath": 456, "version": "1"}],
    })
    plugins, unreadable = discover_plugins("claude-code", home, proj)
    assert plugins == [] and unreadable == [str(p)]


# --- pi packages (settings.json `packages` + package install store) -----
# Semantics verified against pi 1.1.0 source (dist/core/package-manager.js,
# pi-manifest.js, trust-manager.js, utils/git.js):
#   * packages are declared in ~/.pi/agent/settings.json (user) and
#     <cwd>/.pi/settings.json (project; only after project trust);
#     entries are source strings or {source, autoload, ...} objects
#   * git installs live at <agent>/git/<host>/<owner>/<repo> (ref dropped);
#     npm at <agent>/npm/node_modules/<name> (project scope: <cwd>/.pi/...)
#   * skills come from the package.json `pi.skills` entries (override
#     patterns !/+/- are not roots; globs expand) -- OR the conventional
#     skills/ dir, but only when there is no `pi` manifest at all
#   * project packages resolve first and win identity dedupe
#     (npm:<name> / git:<host>/<path> / local resolved path); a project
#     entry with autoload:false is a delta that keeps the user copy

def _pi_settings(home: Path, packages) -> Path:
    agent = home / ".pi" / "agent"
    agent.mkdir(parents=True, exist_ok=True)
    p = agent / "settings.json"
    p.write_text(json.dumps({"packages": packages}), encoding="utf-8")
    return p


def _pi_trust(home: Path, mapping: dict) -> Path:
    agent = home / ".pi" / "agent"
    agent.mkdir(parents=True, exist_ok=True)
    p = agent / "trust.json"
    p.write_text(json.dumps(mapping), encoding="utf-8")
    return p


def _pi_pkg(root: Path, *, name=None, version=None, pi=None, raw=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if raw is not None or name is not None or version is not None or pi is not None:
        m = dict(raw or {})
        if name is not None:
            m["name"] = name
        if version is not None:
            m["version"] = version
        if pi is not None:
            m["pi"] = pi
        (root / "package.json").write_text(json.dumps(m), encoding="utf-8")
    return root


def test_pi_git_package_with_manifest_skills(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    pkg = _pi_pkg(home / ".pi" / "agent" / "git" / "github.com" / "obra" / "superpowers",
                  name="superpowers", version="7.0.0",
                  pi={"skills": ["./skills"]})
    _skill(pkg / "skills", "superpowers-skill")
    settings = _pi_settings(home, ["git:github.com/obra/superpowers"])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "superpowers" and p.version == "7.0.0"
    assert p.scope == "user" and p.enabled and p.recursive
    assert p.skills_roots == [pkg / "skills" / "superpowers-skill"]
    assert p.provenance_source == "superpowers==7.0.0"
    assert p.evidence == settings


def test_pi_npm_scoped_package_conventional_skills_dir(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    pkg = _pi_pkg(home / ".pi" / "agent" / "npm" / "node_modules" / "@lunaroute" / "pi-extension",
                  name="@lunaroute/pi-extension", version="1.2.3")
    _skill(pkg / "skills", "pi-extension-skill")
    # @version must be stripped without mangling the @scope name
    _pi_settings(home, ["npm:@lunaroute/pi-extension@1.2.3"])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "@lunaroute/pi-extension" and p.version == "1.2.3"
    assert p.scope == "user" and p.enabled
    assert p.skills_roots == [pkg / "skills" / "pi-extension-skill"]


def test_pi_manifest_redirects_skills_and_patterns_are_not_roots(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    pkg = _pi_pkg(home / ".pi" / "agent" / "git" / "github.com" / "obra" / "sp",
                  name="sp", pi={"skills": ["./resources/skills", "!./skills/secret"]})
    _skill(pkg / "resources" / "skills", "moved-skill")
    _skill(pkg / "skills", "decoy-skill")  # conventional dir: NOT scanned
    _pi_settings(home, ["git:github.com/obra/sp"])
    plugins, _ = discover_plugins("pi", home, proj)
    (p,) = plugins
    assert p.skills_roots == [pkg / "resources" / "skills" / "moved-skill"]


def test_pi_manifest_glob_entries_expand(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    pkg = _pi_pkg(home / ".pi" / "agent" / "git" / "github.com" / "obra" / "sp",
                  name="sp", pi={"skills": ["./packs/*/skills"]})
    _skill(pkg / "packs" / "a" / "skills", "a-skill")
    _skill(pkg / "packs" / "b" / "skills", "b-skill")
    _pi_settings(home, ["git:github.com/obra/sp"])
    plugins, _ = discover_plugins("pi", home, proj)
    (p,) = plugins
    assert p.skills_roots == [pkg / "packs" / "a" / "skills" / "a-skill",
                             pkg / "packs" / "b" / "skills" / "b-skill"]


def test_pi_manifest_exclusion_pattern_filters_skills(tmp_path):
    # manifest override patterns (!/+/-, applyPatterns) filter the discovered
    # files; they are not roots and are not silently ignored.
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    pkg = _pi_pkg(home / ".pi" / "agent" / "git" / "github.com" / "obra" / "sp",
                  name="sp", pi={"skills": ["./skills", "!./skills/secret"]})
    _skill(pkg / "skills", "keep")
    _skill(pkg / "skills", "secret")
    _pi_settings(home, ["git:github.com/obra/sp"])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.skills_roots == [pkg / "skills" / "keep"]


def test_pi_object_entry_skills_filter_narrows(tmp_path):
    # object-form package entries carry a per-resource skills filter that pi
    # applies on top of the manifest set (applyPackageFilter).
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    pkg = _pi_pkg(home / ".pi" / "agent" / "git" / "github.com" / "obra" / "sp",
                  name="sp", pi={"skills": ["./skills"]})
    _skill(pkg / "skills", "keep")
    _skill(pkg / "skills", "drop")
    _pi_settings(home, [{"source": "git:github.com/obra/sp",
                         "skills": ["./skills/keep"]}])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.skills_roots == [pkg / "skills" / "keep"]


def test_pi_object_entry_empty_skills_filter_disables_all(tmp_path):
    # an empty skills array explicitly disables every skill of that package
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    pkg = _pi_pkg(home / ".pi" / "agent" / "git" / "github.com" / "obra" / "sp",
                  name="sp", pi={"skills": ["./skills"]})
    _skill(pkg / "skills", "keep")
    _pi_settings(home, [{"source": "git:github.com/obra/sp", "skills": []}])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert plugins == [] and unreadable == []


def test_pi_conventional_package_top_level_markdown_skill(tmp_path):
    # collectSkillEntries(dir, "pi") counts bare top-level .md files in a
    # skill root as skills, not just SKILL.md dirs.
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    pkg = _pi_pkg(home / ".pi" / "agent" / "git" / "github.com" / "obra" / "sp",
                  name="sp")
    skills = pkg / "skills"
    skills.mkdir(parents=True)
    (skills / "review.md").write_text(
        "---\nname: review\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    _pi_settings(home, ["git:github.com/obra/sp"])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.skills_roots == []
    assert p.skill_files == [skills / "review.md"]


def test_pi_manifest_root_mixes_skill_dir_and_markdown(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    pkg = _pi_pkg(home / ".pi" / "agent" / "git" / "github.com" / "obra" / "sp",
                  name="sp", pi={"skills": ["./skills"]})
    skills = pkg / "skills"
    _skill(skills, "keep")
    (skills / "review.md").write_text(
        "---\nname: review\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    _pi_settings(home, ["git:github.com/obra/sp"])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.skills_roots == [skills / "keep"]
    assert p.skill_files == [skills / "review.md"]


def test_pi_manifest_without_skills_key_contributes_nothing(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    pkg = _pi_pkg(home / ".pi" / "agent" / "git" / "github.com" / "obra" / "sp",
                  name="sp", pi={"extensions": ["./.pi/extensions/sp.ts"]})
    (pkg / ".pi" / "extensions").mkdir(parents=True)
    _skill(pkg / "skills", "decoy-skill")  # pi manifest present: no fallback
    _pi_settings(home, ["git:github.com/obra/sp"])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert plugins == [] and unreadable == []


def test_pi_local_dir_package_and_local_file_entry(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    pkg = _pi_pkg(home / ".pi" / "agent" / "local-pkg",
                  name="local-pkg", version="0.1.0")
    _skill(pkg / "skills", "local-skill")
    ext = home / ".pi" / "agent" / "ext.ts"  # a file entry is an
    ext.parent.mkdir(parents=True, exist_ok=True)  # extension, not a package
    ext.write_text("// ext", encoding="utf-8")
    _pi_settings(home, ["./local-pkg", "./ext.ts"])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "local-pkg" and p.version == "0.1.0"
    assert p.scope == "user" and p.skills_roots == [pkg / "skills" / "local-skill"]


def test_pi_dedupes_equivalent_sources_ignoring_ref(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    pkg = _pi_pkg(home / ".pi" / "agent" / "git" / "github.com" / "obra" / "sp",
                  name="sp", pi={"skills": ["./skills"]})
    _skill(pkg / "skills", "sp-skill")
    _pi_settings(home, ["git:github.com/obra/sp",
                        "git:github.com/obra/sp@v7.0.0",  # ref drops from path
                        "https://github.com/obra/sp"])      # same identity
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert unreadable == []
    assert len(plugins) == 1
    assert plugins[0].skills_roots == [pkg / "skills" / "sp-skill"]


def test_pi_nested_git_repository_path(tmp_path):
    # generic git sources may have more than two path components
    # (gitlab subgroups); pi stores them under the full host/path
    # hierarchy (utils/git.ts buildGitSource: path.split("/").length >= 2).
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    rel = Path("gitlab.example.com") / "group" / "subgroup" / "pkg"
    pkg = _pi_pkg(home / ".pi" / "agent" / "git" / rel,
                  name="pkg", pi={"skills": ["./skills"]})
    _skill(pkg / "skills", "nested-skill")
    _pi_settings(home, ["git:gitlab.example.com/group/subgroup/pkg"])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert unreadable == []
    (p,) = plugins
    assert p.name == "pkg"
    assert p.skills_roots == [pkg / "skills" / "nested-skill"]


def test_pi_git_repo_path_traversal_is_rejected(tmp_path):
    # pi's buildGitSource rejects path traversal / absolute / backslash
    # parts before deriving an install root; drskill must too.
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _pi_settings(home, [
        "git:github.com/obra/../evil",
        "git:github.com/obra/..%2fevil",
        "git:github.com/obra\\evil",
    ])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert plugins == [] and unreadable == []


def test_pi_project_packages_require_trust(tmp_path):
    home = tmp_path / "home"
    work = tmp_path / "work"
    proj = work / "repo"
    proj.mkdir(parents=True)
    pkg = _pi_pkg(proj / ".pi" / "git" / "github.com" / "obra" / "sp",
                  name="sp", pi={"skills": ["./skills"]})
    _skill(pkg / "skills", "sp-skill")
    (proj / ".pi" / "settings.json").write_text(
        json.dumps({"packages": ["git:github.com/obra/sp"]}), encoding="utf-8")
    # no trust.json: project packages do not load; user packages still do
    user_pkg = _pi_pkg(home / ".pi" / "agent" / "git" / "github.com" / "obra" / "up",
                       name="up", pi={"skills": ["./skills"]})
    _skill(user_pkg / "skills", "up-skill")
    _pi_settings(home, ["git:github.com/obra/up"])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert unreadable == []
    assert [(p.name, p.scope) for p in plugins] == [("up", "user")]
    # ancestor trust covers the project dir
    _pi_trust(home, {str(work.resolve()): True})
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert unreadable == []
    by_name = {p.name: p for p in plugins}
    assert by_name["sp"].scope == "project"
    assert by_name["sp"].project_path == proj
    # an explicit false decision blocks (nearest ancestor entry wins)
    _pi_trust(home, {str(work.resolve()): False})
    plugins, _ = discover_plugins("pi", home, proj)
    assert [(p.name, p.scope) for p in plugins] == [("up", "user")]


def test_pi_project_wins_identity_and_delta_keeps_both(tmp_path):
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    proj.mkdir()
    _pi_trust(home, {str(proj.resolve()): True})
    for base, tag in ((home / ".pi" / "agent" / "git" / "github.com" / "obra" / "sp", "u"),
                      (proj / ".pi" / "git" / "github.com" / "obra" / "sp", "p")):
        _pi_pkg(base, name="sp", pi={"skills": ["./skills"]})
        _skill(base / "skills", f"{tag}-skill")
    _pi_settings(home, ["git:github.com/obra/sp"])
    (proj / ".pi" / "settings.json").write_text(
        json.dumps({"packages": ["git:github.com/obra/sp"]}), encoding="utf-8")
    plugins, _ = discover_plugins("pi", home, proj)
    # same identity: the project checkout wins outright
    assert [(p.name, p.scope) for p in plugins] == [("sp", "project")]
    # project entry with autoload=false is a delta over the user copy
    (proj / ".pi" / "settings.json").write_text(
        json.dumps({"packages": [{"source": "git:github.com/obra/sp",
                                  "autoload": False}]}), encoding="utf-8")
    plugins, _ = discover_plugins("pi", home, proj)
    assert [(p.name, p.scope, p.enabled) for p in plugins] == [
        ("sp", "project", False), ("sp", "user", True)
    ]


def test_pi_declared_but_uninstalled_packages_are_skipped(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    _pi_settings(home, ["git:github.com/obra/gone", "npm:@gone/pkg", "./missing"])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert plugins == [] and unreadable == []


def test_pi_corrupt_package_json_falls_back_to_convention(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    pkg = home / ".pi" / "agent" / "git" / "github.com" / "obra" / "sp"
    _skill(pkg / "skills", "conv-skill")
    (pkg / "package.json").write_text("{bad json", encoding="utf-8")
    _pi_settings(home, ["git:github.com/obra/sp"])
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert unreadable == []
    (p,) = plugins
    # identity falls back to the checkout name when package.json is unreadable
    assert p.name == "sp" and p.version is None
    assert p.skills_roots == [pkg / "skills" / "conv-skill"]


def test_pi_settings_invalid_json_is_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    agent = home / ".pi" / "agent"
    agent.mkdir(parents=True)
    p = agent / "settings.json"
    p.write_text("{nope", encoding="utf-8")
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert plugins == [] and unreadable == [str(p)]


def test_pi_settings_wrong_shape_and_bad_packages_are_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    agent = home / ".pi" / "agent"
    agent.mkdir(parents=True)
    for content in ("[]", json.dumps({"packages": "not-a-list"}),
                    json.dumps({"packages": [123]}),
                    json.dumps({"packages": [{"autoload": True}]})):
        p = agent / "settings.json"
        p.write_text(content, encoding="utf-8")
        plugins, unreadable = discover_plugins("pi", home, proj)
        assert plugins == [] and unreadable == [str(p)], content


def test_pi_settings_without_packages_key_is_normal(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    agent = home / ".pi" / "agent"
    agent.mkdir(parents=True)
    (agent / "settings.json").write_text(
        json.dumps({"lastChangelogVersion": "1.1.0"}), encoding="utf-8")
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert plugins == [] and unreadable == []


def test_pi_corrupt_trust_file_is_unreadable_and_blocks_project(tmp_path):
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    proj.mkdir()
    pkg = _pi_pkg(proj / ".pi" / "git" / "github.com" / "obra" / "sp",
                  name="sp", pi={"skills": ["./skills"]})
    _skill(pkg / "skills", "sp-skill")
    (proj / ".pi" / "settings.json").write_text(
        json.dumps({"packages": ["git:github.com/obra/sp"]}), encoding="utf-8")
    trust = _pi_trust(home, {})  # placeholder dir; corrupt it below
    trust.write_text("{broken", encoding="utf-8")
    plugins, unreadable = discover_plugins("pi", home, proj)
    assert plugins == [] and unreadable == [str(trust)]


def test_copilot_multiple_bad_cache_paths_single_unreadable(tmp_path):
    home, proj = tmp_path / "home", tmp_path / "proj"
    proj.mkdir()
    cfg = home / ".copilot" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"installedPlugins": [
        {"name": "a", "marketplace": "m", "cache_path": 123},
        {"name": "b", "marketplace": "m", "cache_path": 456},
    ]}), encoding="utf-8")
    plugins, unreadable = discover_plugins("copilot", home, proj)
    assert plugins == [] and unreadable == [str(cfg)]
