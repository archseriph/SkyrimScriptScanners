"""
Skyrim SE Script Optimization Analyzer
=======================================
Reads the active MO2 mod list, extracts and decompiles Papyrus .pex scripts,
runs static analysis against a configurable rule set, and writes a Markdown report.

Usage:
  python scanner.py [--profile PATH] [--mod NAME] [--severity LEVEL] [--no-cleanup] [--fix]

Flags:
  --profile PATH      Override the MO2 profile directory from settings.json
  --mod NAME          Scan only a single named mod (useful for testing)
  --severity LEVEL    Minimum severity to include in report: CRITICAL, WARNING, NOTICE
  --no-cleanup        Keep the cache directory after the run finishes
  --fix               Compile patched .pex files for WARN_MISSING_EFFECT_CLEANUP findings
                      Output goes to fix_output_mod/Scripts/ (configured in settings.json)
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

# Allow running as: python scanner.py from within the project directory
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from modules.mo2_parser    import load_mod_list
from modules.bsa_extractor import scan_bsa_scripts, extract_bsa_record
from modules.decompiler    import decompile_mod_scripts
from modules.analyzer      import ScriptAnalyzer
from modules.reporter      import write_report
from modules.patcher       import apply_script_fixes


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_json(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _safe_dirname(name: str) -> str:
    """Strip Windows-illegal characters and truncate to 80 chars for use as a dir name."""
    illegal = r'\/:*?"<>|'
    return ''.join('_' if c in illegal else c for c in name)[:80]


# ── Main pipeline ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Skyrim SE Papyrus Script Optimization Analyzer'
    )
    parser.add_argument('--profile',    metavar='PATH',
                        help='MO2 profile directory (overrides settings.json)')
    parser.add_argument('--mod',        metavar='NAME',
                        help='Scan only this mod (case-insensitive name match)')
    parser.add_argument('--skip',       metavar='NAME', action='append', default=[],
                        help='Skip a mod by name (repeatable). Also reads skip_mods from settings.json')
    parser.add_argument('--severity',   choices=['CRITICAL', 'WARNING', 'NOTICE'],
                        help='Minimum severity level to include in the report')
    parser.add_argument('--no-cleanup', action='store_true',
                        help='Keep cache directory after the run')
    parser.add_argument('--fix',        action='store_true',
                        help='Compile patched scripts to fix_output_mod/Scripts/ (settings.json)')
    args = parser.parse_args()

    # ── Step 1: Load config ────────────────────────────────────────────────
    settings = _load_json(os.path.join(_SCRIPT_DIR, 'settings.json'))
    ruleset  = _load_json(os.path.join(_SCRIPT_DIR, 'ruleset.json'))
    rules    = ruleset['rules']

    if args.profile:
        settings['mo2_profile'] = args.profile

    profile_dir   = settings['mo2_profile']
    mods_dir      = settings['mo2_mods_dir']
    overwrite_dir = settings['mo2_overwrite']
    champollion   = settings['champollion_exe']
    cache_root    = os.path.join(_SCRIPT_DIR, settings.get('cache_dir', 'cache'))
    output_dir    = os.path.join(_SCRIPT_DIR, settings.get('output_dir', 'output'))
    pex_cache     = os.path.join(cache_root, 'pex')
    psc_cache     = os.path.join(cache_root, 'psc')

    print(f'[1/7] Loading MO2 profile: {os.path.basename(profile_dir)}')
    mod_list = load_mod_list(profile_dir, mods_dir, overwrite_dir)

    # Inject fix_output_mod at the front of the list (right after overwrite/).
    # This ensures already-patched scripts win VFS deduplication so the scanner
    # skips originals it has already fixed, preventing redundant re-patching.
    fix_output_mod  = settings.get('fix_output_mod', os.path.join(mods_dir, 'Config Fixes'))
    fix_mod_name    = os.path.basename(fix_output_mod)
    fix_scripts_dir = os.path.join(fix_output_mod, 'Scripts')
    _fix_pex_count  = 0
    if os.path.isdir(fix_output_mod):
        mod_list = [(n, p) for n, p in mod_list if n.lower() != fix_mod_name.lower()]
        insert_at = 1 if mod_list and mod_list[0][0] == '_overwrite' else 0
        mod_list.insert(insert_at, (fix_mod_name, fix_output_mod))
        if os.path.isdir(fix_scripts_dir):
            _fix_pex_count = sum(1 for f in os.listdir(fix_scripts_dir)
                                 if f.lower().endswith('.pex'))
        print(f'  Fix output mod : {fix_output_mod}')
        print(f'  Patched scripts: {_fix_pex_count} .pex files found — '
              f'injected at mod list position {insert_at + 1}')
    else:
        print(f'  Fix output mod : not found ({fix_output_mod})')

    if args.mod:
        mod_list = [(n, p) for n, p in mod_list if n.lower() == args.mod.lower()]
        if not mod_list:
            print(f'ERROR: Mod "{args.mod}" not found or not enabled in this profile.')
            sys.exit(1)
        print(f'  Filtering to single mod: {mod_list[0][0]}')
    else:
        # Apply skip list: CLI --skip flags + settings.json skip_mods list
        skip_names = {s.lower() for s in args.skip}
        skip_names.update(s.lower() for s in settings.get('skip_mods', []))
        if skip_names:
            before = len(mod_list)
            mod_list = [(n, p) for n, p in mod_list if n.lower() not in skip_names]
            print(f'  {len(mod_list)} enabled mods ({before - len(mod_list)} skipped)')
        else:
            print(f'  {len(mod_list)} enabled mods')

    # ── Step 2: Discover scripts with virtual-FS deduplication ────────────
    print('[2/7] Discovering scripts (simulating MO2 virtual file system)...')
    seen_scripts: set  = set()   # lowercase filenames — first seen = highest priority winner
    all_jobs:     list = []      # one job dict per winning script
    tif_filtered  = 0

    for mod_name, mod_path in mod_list:
        safe_name  = _safe_dirname(mod_name)
        pex_out    = os.path.join(pex_cache, safe_name)

        # ── Loose .pex files ───────────────────────────────────────────
        scripts_dir = os.path.join(mod_path, 'Scripts')
        if os.path.isdir(scripts_dir):
            try:
                for fname in os.listdir(scripts_dir):
                    if not fname.lower().endswith('.pex'):
                        continue
                    key = fname.lower()
                    if '_tif_' in key:
                        tif_filtered += 1
                        continue
                    if key in seen_scripts:
                        continue
                    seen_scripts.add(key)
                    all_jobs.append({
                        'mod':         mod_name,
                        'safe_mod':    safe_name,
                        'script':      fname,
                        'source_type': 'loose',
                        'src_path':    os.path.join(scripts_dir, fname),
                        'out_dir':     pex_out,
                    })
            except OSError as e:
                print(f'  WARN: could not list Scripts/ for {mod_name}: {e}')

        # ── BSA archives ────────────────────────────────────────────────
        try:
            bsa_files = [f for f in os.listdir(mod_path) if f.lower().endswith('.bsa')]
        except OSError:
            bsa_files = []

        for bsa_name in bsa_files:
            bsa_path = os.path.join(mod_path, bsa_name)
            try:
                bsa_scripts = scan_bsa_scripts(bsa_path)
            except Exception as e:
                print(f'  WARN: BSA scan failed ({bsa_name}): {e}')
                continue

            for fname, record in bsa_scripts.items():
                key = fname.lower()
                if '_tif_' in key:
                    tif_filtered += 1
                    continue
                if key in seen_scripts:
                    continue
                seen_scripts.add(key)
                all_jobs.append({
                    'mod':         mod_name,
                    'safe_mod':    safe_name,
                    'script':      fname,
                    'source_type': 'bsa',
                    'bsa_path':    bsa_path,
                    'record':      record,
                    'out_dir':     pex_out,
                })

    fix_jobs = sum(1 for j in all_jobs if j['mod'].lower() == fix_mod_name.lower())
    print(f'  {len(all_jobs)} unique scripts queued  |  '
          f'{tif_filtered} TIF dialogue scripts filtered')
    print(f'  Config Fixes won VFS dedup for {fix_jobs} script(s)')

    # ── Step 3: Extract .pex files to per-mod cache dirs ──────────────────
    print('[3/7] Extracting .pex files to cache...')
    extract_errors = []
    for job in all_jobs:
        dest = os.path.join(job['out_dir'], job['script'])
        os.makedirs(job['out_dir'], exist_ok=True)
        try:
            if job['source_type'] == 'loose':
                if not os.path.exists(dest):
                    shutil.copy2(job['src_path'], dest)
            else:
                if not os.path.exists(dest):
                    extract_bsa_record(job['bsa_path'], job['record'], dest)
        except Exception as e:
            extract_errors.append(f'{job["mod"]}/{job["script"]}: {e}')

    if extract_errors:
        print(f'  WARN: {len(extract_errors)} extraction failure(s)')

    # ── Step 4: Decompile per mod with Champollion ────────────────────────
    print('[4/7] Decompiling with Champollion...')

    # Collect unique (safe_mod, pex_dir) pairs — preserves attribution
    seen_pex_dirs: set  = set()
    decompile_mods: list = []
    for job in all_jobs:
        if job['out_dir'] not in seen_pex_dirs:
            seen_pex_dirs.add(job['out_dir'])
            decompile_mods.append((job['mod'], job['safe_mod'], job['out_dir']))

    champollion_timeout = settings.get('champollion_timeout', 600)
    decompile_errors: list = []
    for mod_name, safe_name, pex_dir in decompile_mods:
        psc_dir = os.path.join(psc_cache, safe_name)
        try:
            errors = decompile_mod_scripts(champollion, pex_dir, psc_dir,
                                           timeout=champollion_timeout)
            for err in errors:
                decompile_errors.append(f'{mod_name}: {err}')
        except Exception as e:
            decompile_errors.append(f'{mod_name}: decompiler exception: {e}')

    print(f'  {len(decompile_errors)} decompilation error(s)')

    # ── Step 5: Analyse decompiled scripts ─────────────────────────────────
    print('[5/7] Analysing scripts...')
    analyzer      = ScriptAnalyzer(rules)
    all_findings: list = []
    scripts_done  = 0
    scripts_missing = 0

    for job in all_jobs:
        psc_name = os.path.splitext(job['script'])[0] + '.psc'
        psc_path = os.path.join(psc_cache, job['safe_mod'], psc_name)
        if not os.path.isfile(psc_path):
            scripts_missing += 1
            continue
        findings = analyzer.analyze_file(psc_path, job['script'], job['mod'])
        all_findings.extend(findings)
        scripts_done += 1

    print(f'  {scripts_done} scripts analysed  |  '
          f'{scripts_missing} not decompiled (extraction/Champollion failures)')
    print(f'  {len(all_findings)} raw finding(s)')

    # ── Apply mod-level rule suppressions from settings.json ──────────────
    # Format: "mod_rule_suppressions": { "Mod Name": ["RULE_ID", ...] }
    # Use case: utility library mods with intentional patterns (tight loops etc.)
    mod_suppressions = settings.get('mod_rule_suppressions', {})
    if mod_suppressions:
        before = len(all_findings)
        all_findings = [
            f for f in all_findings
            if f['rule_id'] not in mod_suppressions.get(f['mod'], [])
        ]
        suppressed = before - len(all_findings)
        if suppressed:
            print(f'  {suppressed} finding(s) suppressed via mod_rule_suppressions')

    # ── Step 5b: Patch scripts (--fix) ────────────────────────────────────────
    patch_stats = None
    if args.fix:
        fix_output = settings.get('fix_output_mod',
                                  os.path.join(mods_dir, 'Config Fixes'))
        print(f'\n[5b/7] Patching scripts → {fix_output}')
        patch_stats = apply_script_fixes(all_findings, psc_cache, settings)
        print(f'  Already patched: {patch_stats["already_patched"]} (skipped)')
        print(f'  Newly patched  : {patch_stats["patched"]}')
        print(f'  Compile failed : {patch_stats["compile_failed"]}')
        print(f'  Skipped        : {patch_stats["skipped"]}')

    # ── Step 6: Apply severity filter and write report ─────────────────────
    sev_order = {'CRITICAL': 0, 'WARNING': 1, 'NOTICE': 2}
    if args.severity:
        threshold = sev_order[args.severity]
        all_findings = [f for f in all_findings
                        if sev_order.get(f['severity'], 99) <= threshold]
        print(f'  After {args.severity}+ filter: {len(all_findings)} finding(s)')

    print('[6/7] Writing report...')
    profile_name = os.path.basename(profile_dir.rstrip('/\\'))
    report_path  = write_report(
        findings         = all_findings,
        decompile_errors = decompile_errors,
        output_dir       = output_dir,
        profile_name     = profile_name,
        mods_scanned     = len(mod_list),
        scripts_analyzed = scripts_done,
        tif_filtered     = tif_filtered,
        rules            = rules,
    )
    print(f'  {report_path}')

    # ── Step 7: Cleanup ────────────────────────────────────────────────────
    if args.no_cleanup:
        print('[7/7] --no-cleanup: cache preserved.')
    else:
        print('[7/7] Cleaning cache...')
        shutil.rmtree(cache_root, ignore_errors=True)

    # ── Summary ────────────────────────────────────────────────────────────
    critical = sum(1 for f in all_findings if f['severity'] == 'CRITICAL')
    warning  = sum(1 for f in all_findings if f['severity'] == 'WARNING')
    notice   = sum(1 for f in all_findings if f['severity'] == 'NOTICE')
    print(f'\nDone.  CRITICAL: {critical}  WARNING: {warning}  NOTICE: {notice}')
    print(f'Report: {report_path}')


if __name__ == '__main__':
    main()
