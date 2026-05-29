"""
Champollion CLI wrapper for batch .pex decompilation.

Champollion notes (confirmed from docs + live test):
  - CLI: Champollion.exe <input_dir_or_file> -p <output_dir> [-t]
  - -t enables threaded batch mode (significant speedup on many files)
  - Exit code is ALWAYS 0, even when individual files fail
  - Per-file errors appear in stdout as lines beginning with "ERROR:"
  - Decompiled .psc files have the same basename as the input .pex files
"""

import os
import subprocess


def decompile_mod_scripts(champollion_exe: str, pex_dir: str, psc_dir: str,
                          timeout: int = 300) -> list:
    """
    Run Champollion on all .pex files in pex_dir, writing .psc to psc_dir.

    Returns a list of error strings parsed from stdout (empty = no failures).
    Raises subprocess.TimeoutExpired if decompilation takes longer than timeout seconds.
    """
    if not os.path.isdir(pex_dir):
        return []

    pex_files = [f for f in os.listdir(pex_dir) if f.lower().endswith('.pex')]
    if not pex_files:
        return []

    os.makedirs(psc_dir, exist_ok=True)

    result = subprocess.run(
        [champollion_exe, pex_dir, '-p', psc_dir, '-t'],
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    errors = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith('ERROR:') or stripped.upper().startswith('ERROR :'):
            errors.append(stripped)

    return errors
