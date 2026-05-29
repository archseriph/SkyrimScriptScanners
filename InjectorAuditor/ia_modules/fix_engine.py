"""
Injector config auto-fixer.

Reads findings from the IniAnalyzer, applies per-rule fix strategies to the
original ini files, and writes corrected copies to a MO2 override mod folder.
The output files are placed at the same relative path as their originals so
MO2's virtual file system override mechanism applies automatically.

Fix strategies by rule:
  qualify_formid  — add ~Plugin.esp to bare hex FormIDs in field[0]
  comment_out     — prefix line with '; AUTO-FIXED: ' to disable it
  mark_manual     — prefix line with '; NEEDS MANUAL FIX: ' with the rule ID
  skip            — produce no output for this rule (notices, broad wildcards)

When multiple rules fire on the same line, comment_out takes precedence over
qualify_formid, which takes precedence over mark_manual.
"""

import os
import re


# ── Per-rule strategy map ─────────────────────────────────────────────────────

STRATEGIES = {
    'ERR_SPID_INVALID_FORMID': 'qualify_formid',
    'WARN_SPID_ZERO_CHANCE':   'comment_out',
    'ERR_KID_INVALID_FORMID':  'qualify_formid',
    'ERR_KID_UNKNOWN_TYPE':    'mark_manual',
    'ERR_SP_INVALID_HEX':      'mark_manual',
    'ERR_SP_MALFORMED_REF':    'mark_manual',
    'NOTE_SPID_NO_FILTER':     'skip',
    'WARN_KID_BROAD_WILDCARD': 'skip',
}

STRATEGY_PRIORITY = {'comment_out': 0, 'qualify_formid': 1, 'mark_manual': 2, 'skip': 3}


# ── Plugin detection ──────────────────────────────────────────────────────────

def _detect_plugin(mod_path: str, mod_name: str, plugin_overrides: dict):
    """
    Return the plugin filename for a mod, or None if ambiguous/not found.

    Priority: plugin_overrides dict → single-plugin auto-detection → None.
    """
    if mod_name in plugin_overrides:
        return plugin_overrides[mod_name]
    try:
        plugins = [f for f in os.listdir(mod_path)
                   if f.lower().endswith(('.esp', '.esm', '.esl'))]
    except OSError:
        return None
    return plugins[0] if len(plugins) == 1 else None


# ── Field-level FormID qualification ─────────────────────────────────────────

def _qualify_bare_formids(field_value: str, plugin_name: str) -> tuple:
    """
    Add ~plugin_name to every bare hex FormID token in a comma-separated field.
    Returns (new_value, changed: bool).

    Leaves alone: tokens already containing '~', 'NONE', empty strings, or
    plain keyword EditorIDs (word chars only — these belong to a different plugin
    and can't be auto-qualified).
    """
    result = []
    changed = False
    for raw in field_value.split(','):
        token = raw.strip()
        if (not token
                or token.upper() == 'NONE'
                or '~' in token):
            result.append(raw)
            continue
        # Bare hex FormID: optional 0x prefix, then only hex digits
        if re.match(r'^(0x)?[0-9A-Fa-f]+$', token, re.IGNORECASE):
            result.append(token + '~' + plugin_name)
            changed = True
        else:
            result.append(raw)  # keyword name or other — leave as-is
    return ','.join(result), changed


# ── Line-level fix application ────────────────────────────────────────────────

def _fix_line(raw_line: str, rule_ids: list, plugin_name) -> tuple:
    """
    Apply all relevant fix strategies to one line.
    Returns (new_line, dominant_strategy_used).

    Strategies are applied in priority order: comment_out > qualify_formid > mark_manual.
    """
    strategies = {STRATEGIES.get(rid, 'skip') for rid in rule_ids}
    dominant   = min(strategies, key=lambda s: STRATEGY_PRIORITY.get(s, 99))

    line = raw_line.rstrip('\n\r')

    if dominant == 'skip':
        return raw_line, 'skip'

    if dominant == 'comment_out':
        rule_label = next((rid for rid in rule_ids if STRATEGIES.get(rid) == 'comment_out'), '')
        new_line = f'; AUTO-FIXED ({rule_label}): {line}\n'
        return new_line, 'comment_out'

    if dominant == 'qualify_formid':
        if not plugin_name:
            # Can't qualify without knowing the plugin — mark instead
            rule_label = ', '.join(r for r in rule_ids if STRATEGIES.get(r) == 'qualify_formid')
            new_line = f'; NEEDS MANUAL FIX ({rule_label} - could not detect plugin): {line}\n'
            return new_line, 'mark_manual'

        if '=' not in line or line.lstrip().startswith(';'):
            return raw_line, 'skip'

        entry_type, value_str = line.split('=', 1)
        fields = value_str.split('|')

        # Only qualify field[0] — the item/spell/perk FormID.
        # Field[2] (NPC targets) may reference Skyrim.esm or other plugins;
        # we can't determine the correct qualifier without parsing the load order.
        new_f0, changed = _qualify_bare_formids(fields[0].strip(), plugin_name)
        if changed:
            fields[0] = new_f0
            new_line = entry_type + '=' + '|'.join(fields) + '\n'
            return new_line, 'qualify_formid'
        else:
            # Nothing to qualify on this line — could be a keyword EditorID
            return raw_line, 'skip'

    if dominant == 'mark_manual':
        rule_label = ', '.join(rid for rid in rule_ids if STRATEGIES.get(rid) == 'mark_manual')
        new_line = f'; NEEDS MANUAL FIX ({rule_label}): {line}\n'
        return new_line, 'mark_manual'

    return raw_line, 'skip'


# ── File output helper ────────────────────────────────────────────────────────

def _output_path(file_path: str, mod_path: str, output_mod: str) -> str:
    """Compute the output path preserving relative directory structure."""
    rel = os.path.relpath(file_path, mod_path)
    return os.path.join(output_mod, rel)


def _write_fixed_file(lines: list, out_path: str):
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)


# ── Main entry point ──────────────────────────────────────────────────────────

def apply_fixes(findings: list, mods_dir: str, settings: dict) -> dict:
    """
    Apply fix strategies to all files referenced in findings.

    Returns stats dict:
        files_written  — number of output files created/overwritten
        auto_fixed     — lines automatically corrected
        marked_manual  — lines marked as needing manual review
        skipped        — findings on rules with no fix action
    """
    output_mod      = settings.get('fix_output_mod',
                                   os.path.join(mods_dir, 'Config Fixes'))
    plugin_overrides = settings.get('plugin_overrides', {})

    stats = {'files_written': 0, 'auto_fixed': 0, 'marked_manual': 0, 'skipped': 0}

    # Group findings: file_path → { lineno → [rule_id, ...] }
    file_map: dict = {}
    for finding in findings:
        fp  = finding['script']
        ln  = finding['line']
        rid = finding['rule_id']
        file_map.setdefault(fp, {}).setdefault(ln, []).append(rid)

    for file_path, line_fixes in file_map.items():
        # Derive mod name and paths
        mod_name = next((f['mod'] for f in findings if f['script'] == file_path), None)
        if not mod_name:
            continue
        mod_path   = os.path.join(mods_dir, mod_name)
        plugin     = _detect_plugin(mod_path, mod_name, plugin_overrides)
        out_path   = _output_path(file_path, mod_path, output_mod)

        # Read original
        try:
            with open(file_path, encoding='utf-8', errors='replace') as f:
                original_lines = f.readlines()
        except OSError as e:
            print(f'  WARN: could not read {file_path}: {e}')
            continue

        # Apply fixes
        fixed_lines = []
        file_changed = False
        for i, raw_line in enumerate(original_lines):
            lineno = i + 1
            if lineno not in line_fixes:
                fixed_lines.append(raw_line)
                continue

            rule_ids = line_fixes[lineno]
            new_line, strategy = _fix_line(raw_line, rule_ids, plugin)
            fixed_lines.append(new_line)

            if strategy == 'skip':
                stats['skipped'] += len(rule_ids)
            elif strategy == 'qualify_formid':
                stats['auto_fixed'] += len(rule_ids)
                file_changed = True
            elif strategy == 'comment_out':
                stats['auto_fixed'] += len(rule_ids)
                file_changed = True
            elif strategy == 'mark_manual':
                stats['marked_manual'] += len(rule_ids)
                file_changed = True

        # Only write if something changed
        if file_changed:
            _write_fixed_file(fixed_lines, out_path)
            stats['files_written'] += 1
            plugin_note = f' (plugin: {plugin})' if plugin else ' (no plugin detected)'
            print(f'  Written: {os.path.basename(out_path)}{plugin_note}')

    return stats
