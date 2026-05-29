"""
Auto-patcher for WARN_MISSING_EFFECT_CLEANUP findings.

For each ActiveMagicEffect script missing OnEffectFinish:
  1. Reads the decompiled .psc from psc_cache
  2. Appends a standard OnEffectFinish cleanup block
  3. Compiles with PapyrusCompiler.exe
  4. Writes the patched .pex to fix_output_mod/Scripts/

Only handles WARN_MISSING_EFFECT_CLEANUP — orphaned Quest/Alias
registrations require context-specific cleanup and are excluded.

Output mod layout mirrors MO2 mod structure:
  fix_output_mod/Scripts/<ScriptName>.pex
"""

import json
import os
import subprocess
import tempfile

_ILLEGAL_CHARS = r'\/:*?"<>|'


def _safe_dirname(name: str) -> str:
    return ''.join('_' if c in _ILLEGAL_CHARS else c for c in name)[:80]


# Standard cleanup block injected at end of flagged scripts.
# UnregisterFor* calls are no-ops if already unregistered, so this is safe
# even when only a subset of registration types were used.
_CLEANUP_BLOCK = (
    '\n\nEvent OnEffectFinish(Actor akTarget, Actor akCaster)\n'
    '    UnregisterForUpdate()\n'
    '    UnregisterForUpdateGameTime()\n'
    '    UnregisterForAllModEvents()\n'
    'EndEvent\n'
)


def _compile(compiler: str, psc_path: str, import_dirs: list,
             flags_file: str, out_dir: str, timeout: int = 60) -> tuple:
    """
    Run PapyrusCompiler.exe on psc_path, writing .pex to out_dir.
    Returns (success: bool, error_output: str).

    Success is determined by whether the expected .pex was created —
    the compiler exits 0 even on errors, so exit code alone is unreliable.
    """
    psc_base   = os.path.splitext(os.path.basename(psc_path))[0]
    pex_out    = os.path.join(out_dir, psc_base + '.pex')
    import_str = ';'.join(import_dirs)

    # Remove stale output so we can detect whether this compile produced one
    if os.path.isfile(pex_out):
        try:
            os.unlink(pex_out)
        except OSError:
            pass

    cmd = [compiler, psc_path,
           f'-f={flags_file}',
           f'-i={import_str}',
           f'-o={out_dir}']

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, 'compile timed out'
    except OSError as e:
        return False, str(e)

    if os.path.isfile(pex_out):
        return True, ''

    # .pex not created — find the most useful error line.
    # Prefer lines that contain ': ERROR:' (compiler diagnostics) over the
    # generic "No output generated" summary line.
    out_text = (result.stdout + result.stderr).strip()
    lines    = [l.strip() for l in out_text.splitlines() if l.strip()]
    error_lines = [l for l in lines if ': error:' in l.lower() or ': warning:' in l.lower()]
    if error_lines:
        return False, error_lines[0][:300]
    # Fall back to first non-boilerplate line
    for line in lines:
        if not line.lower().startswith('starting') and not line.lower().startswith('compiling'):
            return False, line[:300]
    return False, out_text[:300] or 'no output from compiler'


def apply_script_fixes(findings: list, psc_cache: str, settings: dict) -> dict:
    """
    Patch scripts flagged as WARN_MISSING_EFFECT_CLEANUP.

    Args:
        findings:   full list of analysis findings (filtered internally)
        psc_cache:  root cache dir containing per-mod psc subdirs
        settings:   scanner settings.json dict

    Returns stats dict: patched, compile_failed, skipped.
    """
    mods_dir    = settings['mo2_mods_dir']
    fix_output  = settings.get('fix_output_mod', os.path.join(mods_dir, 'Config Fixes'))
    compiler    = settings.get('papyrus_compiler_exe', '')
    import_dirs = list(settings.get('papyrus_import_dirs', []))
    flags_file  = settings.get('papyrus_flags_file', '')
    timeout     = settings.get('papyrus_compile_timeout', 60)

    scripts_out   = os.path.join(fix_output, 'Scripts')
    failures_path = os.path.join(fix_output, 'compile_failures.json')
    os.makedirs(scripts_out, exist_ok=True)

    # Load persistent compile-failure list so we don't retry known-bad scripts
    known_failures: set = set()
    if os.path.isfile(failures_path):
        try:
            with open(failures_path, encoding='utf-8') as f:
                known_failures = set(json.load(f))
        except (OSError, json.JSONDecodeError):
            known_failures = set()

    stats = {'patched': 0, 'compile_failed': 0, 'skipped': 0, 'already_patched': 0}
    new_failures: set = set()

    ame_findings = [f for f in findings if f['rule_id'] == 'WARN_MISSING_EFFECT_CLEANUP']
    if not ame_findings:
        print('  No WARN_MISSING_EFFECT_CLEANUP findings — nothing to patch.')
        return stats

    if not compiler or not os.path.isfile(compiler):
        print('  ERROR: papyrus_compiler_exe not found or not set in settings.json')
        if compiler:
            print(f'    Configured path: {compiler!r}')
        stats['skipped'] = len(ame_findings)
        return stats

    if not flags_file or not os.path.isfile(flags_file):
        print('  ERROR: papyrus_flags_file not found or not set in settings.json')
        if flags_file:
            print(f'    Configured path: {flags_file!r}')
        stats['skipped'] = len(ame_findings)
        return stats

    # Deduplicate: one fix per unique script filename
    seen: set = set()
    for finding in ame_findings:
        script_name = os.path.basename(finding['script'])  # e.g. wb_script.pex
        key = script_name.lower()
        if key in seen:
            continue
        seen.add(key)

        safe_mod    = _safe_dirname(finding['mod'])
        psc_name    = os.path.splitext(script_name)[0] + '.psc'
        psc_path    = os.path.join(psc_cache, safe_mod, psc_name)
        mod_psc_dir = os.path.dirname(psc_path)

        # Skip if already patched on a previous run
        existing_pex = os.path.join(scripts_out, os.path.splitext(psc_name)[0] + '.pex')
        if os.path.isfile(existing_pex):
            stats['already_patched'] += 1
            continue

        # Skip if this script previously failed to compile
        if psc_name.lower() in known_failures:
            stats['already_patched'] += 1
            continue

        if not os.path.isfile(psc_path):
            print(f'  SKIP (no psc in cache): {psc_name}')
            stats['skipped'] += 1
            continue

        try:
            with open(psc_path, encoding='utf-8', errors='replace') as f:
                original = f.read()
        except OSError as e:
            print(f'  SKIP (read error): {psc_name}: {e}')
            stats['skipped'] += 1
            continue

        patched = original.rstrip() + _CLEANUP_BLOCK

        # Write patched source to a temp dir using the correct script filename
        # so the compiler names the .pex output correctly.
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_psc = os.path.join(tmpdir, psc_name)
            try:
                with open(tmp_psc, 'w', encoding='utf-8') as f:
                    f.write(patched)
            except OSError as e:
                print(f'  SKIP (temp write error): {psc_name}: {e}')
                stats['skipped'] += 1
                continue

            # Include the mod's own psc dir so cross-script dependencies resolve
            all_imports = import_dirs + [mod_psc_dir]
            success, err_msg = _compile(compiler, tmp_psc, all_imports,
                                        flags_file, scripts_out, timeout)

        if success:
            stats['patched'] += 1
            pex_name = os.path.splitext(psc_name)[0] + '.pex'
            print(f'  Patched : {pex_name}  [{finding["mod"]}]')
        else:
            stats['compile_failed'] += 1
            new_failures.add(psc_name.lower())
            print(f'  FAIL    : {psc_name}: {err_msg}')

    # Persist updated failure list so these scripts are skipped on future runs
    if new_failures:
        all_failures = sorted(known_failures | new_failures)
        try:
            with open(failures_path, 'w', encoding='utf-8') as f:
                json.dump(all_failures, f, indent=2)
            print(f'  Recorded {len(new_failures)} new failure(s) → compile_failures.json')
        except OSError as e:
            print(f'  WARN: could not save compile_failures.json: {e}')

    return stats
