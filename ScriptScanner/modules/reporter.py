"""
Markdown report generator for script analysis findings.

Output format:
  - Header with run metadata (timestamp, profile, counts)
  - Summary table of finding counts by severity
  - Decompilation failures section (if any)
  - Findings grouped by mod, sorted by severity (CRITICAL first) then line number
"""

import os
from datetime import datetime


SEVERITY_ORDER = {'CRITICAL': 0, 'WARNING': 1, 'NOTICE': 2}
SEVERITY_LABEL = {'CRITICAL': 'CRITICAL', 'WARNING': 'WARNING', 'NOTICE': 'NOTICE'}


def write_report(findings: list, decompile_errors: list, output_dir: str,
                 profile_name: str, mods_scanned: int, scripts_analyzed: int,
                 tif_filtered: int, rules: list) -> str:
    """
    Write a Markdown report to output_dir/report_YYYYMMDD_HHMMSS.md.
    Returns the full path of the written file.
    """
    timestamp  = datetime.now()
    stamp_str  = timestamp.strftime('%Y%m%d_%H%M%S')
    report_path = os.path.join(output_dir, f'report_{stamp_str}.md')

    lines = _build_report(findings, decompile_errors, profile_name,
                           mods_scanned, scripts_analyzed, tif_filtered,
                           timestamp)

    os.makedirs(output_dir, exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return report_path


def _build_report(findings, decompile_errors, profile_name, mods_scanned,
                  scripts_analyzed, tif_filtered, timestamp) -> list:
    out = []

    # ── Header ────────────────────────────────────────────────────────────
    out.append('# Skyrim SE Script Optimization Report')
    out.append('')
    out.append(f'Generated: {timestamp.strftime("%Y-%m-%d %H:%M:%S")}  ')
    out.append(f'Profile: {profile_name}  ')
    out.append(f'Mods scanned: {mods_scanned} | '
               f'Scripts analyzed: {scripts_analyzed} | '
               f'TIF scripts filtered: {tif_filtered}')
    out.append('')
    out.append('---')
    out.append('')

    # ── Summary table ─────────────────────────────────────────────────────
    counts = {s: 0 for s in SEVERITY_ORDER}
    for f in findings:
        counts[f['severity']] = counts.get(f['severity'], 0) + 1

    out.append('## Summary')
    out.append('')
    out.append('| Severity | Count |')
    out.append('|----------|-------|')
    for sev in ('CRITICAL', 'WARNING', 'NOTICE'):
        out.append(f'| {sev} | {counts.get(sev, 0)} |')
    out.append('')

    if not findings and not decompile_errors:
        out.append('No issues found.')
        return out

    out.append('---')
    out.append('')

    # ── Decompilation failures ─────────────────────────────────────────────
    if decompile_errors:
        out.append(f'## Decompilation Failures ({len(decompile_errors)})')
        out.append('')
        out.append('These scripts could not be decompiled and were skipped during analysis.')
        out.append('')
        for err in decompile_errors:
            out.append(f'- `{err}`')
        out.append('')
        out.append('---')
        out.append('')

    if not findings:
        return out

    # ── Findings by mod ───────────────────────────────────────────────────
    out.append('## Findings by Mod')
    out.append('')

    by_mod: dict = {}
    for f in findings:
        by_mod.setdefault(f['mod'], []).append(f)

    # Sort mods by highest severity finding, then alphabetically
    def mod_sort_key(item):
        mod_findings = item[1]
        best_sev = min(SEVERITY_ORDER.get(f['severity'], 99) for f in mod_findings)
        return (best_sev, item[0].lower())

    for mod_name, mod_findings in sorted(by_mod.items(), key=mod_sort_key):
        count = len(mod_findings)
        out.append(f'### {mod_name} — {count} finding{"s" if count != 1 else ""}')
        out.append('')

        # Sort findings: severity first, then line number
        sorted_findings = sorted(
            mod_findings,
            key=lambda f: (SEVERITY_ORDER.get(f['severity'], 99), f['line'])
        )

        for f in sorted_findings:
            script_base = os.path.basename(f['script'])
            out.append(f'---')
            out.append('')
            out.append(f'**{f["severity"]} — {f["name"]}**  ')
            out.append(f'`{script_base}` line {f["line"]}')
            out.append('')
            out.append('```')
            out.append(f['snippet'])
            out.append('```')
            out.append('')
            out.append(f'> {f["remediation"]}')
            out.append('')

        out.append('')

    return out
