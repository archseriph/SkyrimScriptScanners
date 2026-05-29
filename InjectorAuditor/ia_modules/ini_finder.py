"""
Locate injector config files across MO2 mod directories.

Three framework finders:
  find_spid_files(mod_path, mod_name)      → files ending in _DISTR.ini
  find_kid_files(mod_path, mod_name)       → files ending in _KID.ini
  find_skypatcher_files(mod_path, mod_name)→ *.ini inside SKSE/Plugins/SkyPatcher/

All three walk only the single mod's folder and return a list of result dicts.
The caller iterates the mod list and calls these per-mod.
"""

import os


def find_spid_files(mod_path: str, mod_name: str) -> list:
    """
    Return [{'file': path, 'mod': mod_name}] for every *_DISTR.ini in mod_path.
    Walks the full directory tree (some mods nest configs in subdirs).
    """
    results = []
    try:
        for root, _dirs, files in os.walk(mod_path):
            for fname in files:
                if fname.lower().endswith('_distr.ini'):
                    results.append({'file': os.path.join(root, fname), 'mod': mod_name})
    except OSError:
        pass
    return results


def find_kid_files(mod_path: str, mod_name: str) -> list:
    """
    Return [{'file': path, 'mod': mod_name}] for every *_KID.ini in mod_path.
    """
    results = []
    try:
        for root, _dirs, files in os.walk(mod_path):
            for fname in files:
                if fname.lower().endswith('_kid.ini'):
                    results.append({'file': os.path.join(root, fname), 'mod': mod_name})
    except OSError:
        pass
    return results


def find_skypatcher_files(mod_path: str, mod_name: str) -> list:
    """
    Return [{'file': path, 'mod': mod_name, 'record_type': subdir}] for every
    *.ini inside the SkyPatcher directory (case-insensitive path detection).

    SkyPatcher files live under: <mod>/SKSE/Plugins/SkyPatcher/<record_type>/*.ini
    The immediate subdirectory after SkyPatcher/ is stored as `record_type`
    (e.g. 'npc', 'weapon', 'armor') for report context.

    Detects 'SkyPatcher' and 'Skypatcher' (and other case variants) by lowercasing
    directory names during the walk.
    """
    results = []
    skse_plugins = os.path.join(mod_path, 'SKSE', 'Plugins')
    if not os.path.isdir(skse_plugins):
        return results

    # Walk SKSE/Plugins/ looking for a directory whose lowercased name is 'skypatcher'
    try:
        for entry in os.scandir(skse_plugins):
            if not entry.is_dir():
                continue
            if entry.name.lower() != 'skypatcher':
                continue
            sp_root = entry.path
            # Enumerate all *.ini files recursively from here
            for root, dirs, files in os.walk(sp_root):
                # record_type = the first path component below sp_root
                rel = os.path.relpath(root, sp_root)
                parts = rel.split(os.sep)
                record_type = parts[0].lower() if parts[0] != '.' else 'root'
                for fname in files:
                    if fname.lower().endswith('.ini'):
                        results.append({
                            'file':        os.path.join(root, fname),
                            'mod':         mod_name,
                            'record_type': record_type,
                        })
    except OSError:
        pass
    return results
