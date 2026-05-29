"""
Parse an MO2 modlist.txt and return mods in virtual-FS priority order.

modlist.txt format:
  - Line starting with '+' = enabled mod
  - Line starting with '-' = disabled mod
  - Lines ending with '_separator' = category label, not a real mod folder
  - File order: top = LOWEST priority, bottom = HIGHEST priority
    (bottom of file = top of MO2 mod list = overwrites everything below it)

This module reverses the list so index 0 is highest priority, then prepends
the MO2 overwrite/ folder which always beats everything.
"""

import os


def load_mod_list(profile_dir: str, mods_dir: str, overwrite_dir: str) -> list:
    """
    Parse modlist.txt and return [(mod_name, mod_path)] ordered highest-priority first.

    Mods whose folder does not exist on disk are skipped with a warning.
    The overwrite/ folder is prepended at index 0 (implicit MO2 highest priority).
    """
    modlist_path = os.path.join(profile_dir, 'modlist.txt')
    if not os.path.isfile(modlist_path):
        raise FileNotFoundError(f"modlist.txt not found: {modlist_path}")

    enabled = []
    missing = 0

    with open(modlist_path, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n\r')
            if not line or line.startswith('#'):
                continue
            if not line.startswith('+'):
                continue  # disabled (starts with -) or unrecognised

            name = line[1:]

            if name.endswith('_separator'):
                continue

            folder = os.path.join(mods_dir, name)
            if not os.path.isdir(folder):
                missing += 1
                continue

            enabled.append((name, folder))

    if missing:
        print(f"  WARN: {missing} enabled mod(s) have no folder on disk and were skipped")

    # Reverse: last in file (highest priority) becomes index 0
    enabled.reverse()

    # Prepend overwrite/ — MO2's implicit top-priority staging folder
    if os.path.isdir(overwrite_dir):
        enabled.insert(0, ('_overwrite', overwrite_dir))

    return enabled
