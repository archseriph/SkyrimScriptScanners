"""
Skyrim SE Injector Config Auditor
==================================
Scans SPID, KID, and SkyPatcher config files across the active MO2 mod list
and writes a Markdown report of syntax errors, invalid FormIDs, and bad patterns.

Shared library: imports mo2_parser and reporter directly from ScriptScanner/modules
using a relative sys.path injection — no file restructuring required.

Usage:
  python auditor.py [--profile PATH] [--mod NAME] [--type TYPE] [--severity LEVEL]

Flags:
  --profile PATH         Override MO2 profile directory (default from settings.json)
  --mod NAME             Scan only this mod (case-insensitive)
  --type TYPE            Limit to one framework: spid, kid, skypatcher (default: all)
  --severity LEVEL       Minimum severity to include: CRITICAL, WARNING, NOTICE
"""

import argparse
import json
import os
import sys

# ── Shared library: import mo2_parser and reporter from ScriptScanner ─────────
_AUDITOR_DIR = os.path.dirname(os.path.abspath(__file__))
_SCANNER_DIR = os.path.join(os.path.dirname(_AUDITOR_DIR), 'ScriptScanner')
if _SCANNER_DIR not in sys.path:
    sys.path.insert(0, _SCANNER_DIR)

from modules.mo2_parser import load_mod_list
from modules.reporter   import write_report

# ── Local modules (ia_modules avoids name collision with ScriptScanner/modules) ─
sys.path.insert(0, _AUDITOR_DIR)
from ia_modules.ini_finder   import find_spid_files, find_kid_files, find_skypatcher_files
from ia_modules.ini_analyzer import IniAnalyzer
from ia_modules.fix_engine   import apply_fixes


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_json(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        return json.load(f)


# ── Main pipeline ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Skyrim SE SPID / KID / SkyPatcher Config Auditor'
    )
    parser.add_argument('--profile',  metavar='PATH',
                        help='MO2 profile directory (overrides settings.json)')
    parser.add_argument('--mod',      metavar='NAME',
                        help='Scan only this mod (case-insensitive name match)')
    parser.add_argument('--type',     choices=['spid', 'kid', 'skypatcher', 'all'],
                        default='all',
                        help='Limit scan to one framework (default: all)')
    parser.add_argument('--severity', choices=['CRITICAL', 'WARNING', 'NOTICE'],
                        help='Minimum severity level to include in the report')
    parser.add_argument('--fix',      action='store_true',
                        help='Write auto-corrected ini files to fix_output_mod (set in settings.json)')
    args = parser.parse_args()

    # ── Step 1: Load config ────────────────────────────────────────────────
    settings  = _load_json(os.path.join(_AUDITOR_DIR, 'settings.json'))
    ruleset   = _load_json(os.path.join(_AUDITOR_DIR, 'injector_ruleset.json'))
    rules     = ruleset['rules']

    if args.profile:
        settings['mo2_profile'] = args.profile

    profile_dir   = settings['mo2_profile']
    mods_dir      = settings['mo2_mods_dir']
    overwrite_dir = settings['mo2_overwrite']
    output_dir    = os.path.join(_AUDITOR_DIR, settings.get('output_dir', 'output'))

    scan_spid       = args.type in ('spid',       'all')
    scan_kid        = args.type in ('kid',        'all')
    scan_skypatcher = args.type in ('skypatcher', 'all')

    # ── Step 2: Load mod list ─────────────────────────────────────────────
    print(f'[1/5] Loading MO2 profile: {os.path.basename(profile_dir)}')
    mod_list = load_mod_list(profile_dir, mods_dir, overwrite_dir)

    if args.mod:
        mod_list = [(n, p) for n, p in mod_list if n.lower() == args.mod.lower()]
        if not mod_list:
            print(f'ERROR: Mod "{args.mod}" not found or not enabled.')
            sys.exit(1)
        print(f'  Filtering to mod: {mod_list[0][0]}')
    else:
        print(f'  {len(mod_list)} enabled mods')

    # ── Step 3: Discover config files (VFS deduplication) ────────────────
    # mod_list is highest-priority-first. Track seen relative paths so a
    # higher-priority mod's version of the same file wins — exactly how MO2's
    # virtual filesystem works. This prevents scanning both an original broken
    # file AND its corrected override in e.g. a "Config Fixes" mod.
    print('[2/5] Discovering injector config files...')
    spid_jobs  = []
    kid_jobs   = []
    sp_jobs    = []
    seen_ini: set = set()   # lowercased relative path from mod root

    for mod_name, mod_path in mod_list:
        if scan_spid:
            for job in find_spid_files(mod_path, mod_name):
                rel = os.path.relpath(job['file'], mod_path).lower()
                if rel not in seen_ini:
                    seen_ini.add(rel)
                    spid_jobs.append(job)
        if scan_kid:
            for job in find_kid_files(mod_path, mod_name):
                rel = os.path.relpath(job['file'], mod_path).lower()
                if rel not in seen_ini:
                    seen_ini.add(rel)
                    kid_jobs.append(job)
        if scan_skypatcher:
            for job in find_skypatcher_files(mod_path, mod_name):
                rel = os.path.relpath(job['file'], mod_path).lower()
                if rel not in seen_ini:
                    seen_ini.add(rel)
                    sp_jobs.append(job)

    total_files = len(spid_jobs) + len(kid_jobs) + len(sp_jobs)
    print(f'  SPID: {len(spid_jobs)} files  |  KID: {len(kid_jobs)} files  |  '
          f'SkyPatcher: {len(sp_jobs)} files  |  Total: {total_files}')

    # ── Step 4: Analyse ────────────────────────────────────────────────────
    print('[3/5] Analysing config files...')
    analyzer     = IniAnalyzer(rules)
    all_findings = []

    for job in spid_jobs:
        all_findings.extend(analyzer.analyze_spid_file(job['file'], job['mod']))

    for job in kid_jobs:
        all_findings.extend(analyzer.analyze_kid_file(job['file'], job['mod']))

    for job in sp_jobs:
        all_findings.extend(
            analyzer.analyze_skypatcher_file(job['file'], job['mod'],
                                             job.get('record_type', ''))
        )

    print(f'  {len(all_findings)} raw finding(s)')

    # ── Step 5: Filter and report ──────────────────────────────────────────
    sev_order = {'CRITICAL': 0, 'WARNING': 1, 'NOTICE': 2}
    if args.severity:
        threshold    = sev_order[args.severity]
        all_findings = [f for f in all_findings
                        if sev_order.get(f['severity'], 99) <= threshold]
        print(f'  After {args.severity}+ filter: {len(all_findings)} finding(s)')

    print('[4/5] Writing report...')
    profile_name = os.path.basename(profile_dir.rstrip('/\\'))
    report_path  = write_report(
        findings         = all_findings,
        decompile_errors = [],
        output_dir       = output_dir,
        profile_name     = profile_name,
        mods_scanned     = len(mod_list),
        scripts_analyzed = total_files,   # "Scripts analyzed" = config files here
        tif_filtered     = 0,
        rules            = rules,
    )
    print(f'  {report_path}')

    # ── Optional: apply fixes ─────────────────────────────────────────────
    if args.fix:
        fix_output = settings.get('fix_output_mod',
                                  os.path.join(mods_dir, 'Config Fixes'))
        print(f'\n[Fix] Writing corrected files to: {fix_output}')
        stats = apply_fixes(all_findings, mods_dir, settings)
        print(f'  Files written : {stats["files_written"]}')
        print(f'  Auto-fixed    : {stats["auto_fixed"]} finding(s)')
        print(f'  Manual review : {stats["marked_manual"]} finding(s) (marked in file)')
        print(f'  Skipped       : {stats["skipped"]} finding(s) (notices/warnings)')

    print('[5/5] Done.')
    critical = sum(1 for f in all_findings if f['severity'] == 'CRITICAL')
    warning  = sum(1 for f in all_findings if f['severity'] == 'WARNING')
    notice   = sum(1 for f in all_findings if f['severity'] == 'NOTICE')
    print(f'\nCRITICAL: {critical}  WARNING: {warning}  NOTICE: {notice}')
    print(f'Report: {report_path}')


if __name__ == '__main__':
    main()
