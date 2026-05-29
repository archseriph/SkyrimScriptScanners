# Skyrim Mod Auditor Suite

Static analysis tools for Skyrim SE mod lists. Two tools in one package:

- **Script Scanner** — decompiles every Papyrus `.pex` in your load order and flags orphaned event registrations, missing cleanup handlers, uncapped loops, and other script issues that cause save bloat and frame drops over long playthroughs.
- **Injector Auditor** — validates SPID, KID, and SkyPatcher config files for malformed FormIDs, invalid plugin references, and bad syntax that silently prevents distributions from applying.

Both tools write auto-fix output to a **Config Fixes** MO2 mod folder so the fixes drop in non-destructively without touching original mod files.

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.8+ | https://www.python.org/downloads/ — add to PATH during install |
| [Champollion](https://github.com/Orvid/Champollion/releases) | `.pex` decompiler, place in your MO2 tools folder |
| PapyrusCompiler *(--fix only)* | Comes with the Creation Kit or as a standalone download |
| [SKSE64 source scripts](https://skse.silverlock.org/) *(--fix only)* | For recompiling patched scripts |
| [CK Output source scripts](https://www.nexusmods.com/skyrimspecialedition/mods/1755) *(--fix only)* | Complete vanilla Papyrus source |

---

## Installation

1. Unzip to any folder (e.g. `D:\SkyrimModAuditor\`)
2. Edit **`ScriptScanner\settings.json`** — set all paths to match your setup
3. Edit **`InjectorAuditor\settings.json`** — set MO2 paths
4. In MO2, create a mod called **Config Fixes** and place it near the bottom of your left panel (high priority)
5. Double-click **`RunScanners.bat`**

---

## Configuration

### ScriptScanner\settings.json

```jsonc
{
  // MO2 profile directory containing modlist.txt
  "mo2_profile": "C:\\Modding\\MO2\\profiles\\Default",

  // Path to your MO2 mods folder
  "mo2_mods_dir": "C:\\Modding\\MO2\\mods",

  // MO2 overwrite folder (always highest VFS priority)
  "mo2_overwrite": "C:\\Modding\\MO2\\overwrite",

  // Champollion.exe — used to decompile .pex files for analysis
  "champollion_exe": "C:\\Modding\\MO2\\tools\\Champollion\\Champollion.exe",
  "champollion_timeout": 600,

  // PapyrusCompiler — only needed for --fix mode
  "papyrus_compiler_exe": "C:\\Steam\\...\\Papyrus Compiler\\PapyrusCompiler.exe",
  "papyrus_flags_file":   "C:\\Steam\\...\\Papyrus Compiler\\TESV_Papyrus_Flags.flg",

  // Script import paths for recompilation — SKSE must come before CK Output.
  // Use 8.3 short names (no spaces) to avoid a PapyrusCompiler parsing bug.
  // Find the short name by running: cmd /c "for %f in ("C:\path\to\folder") do echo %~sf"
  "papyrus_import_dirs": [
    "C:\\Modding\\MO2\\mods\\SKYRIM~1\\Scripts\\Source",
    "C:\\Modding\\MO2\\mods\\CKOUTP~1\\Source\\Scripts"
  ],
  "papyrus_compile_timeout": 60,

  // MO2 mod folder where patched scripts and fixed configs are written
  "fix_output_mod": "C:\\Modding\\MO2\\mods\\Config Fixes",

  // Mods to skip entirely during the scan
  "skip_mods": [],

  // Suppress specific rule IDs for specific mods.
  // Useful for mods with intentional patterns that are not real issues.
  "mod_rule_suppressions": {
    "Dylbills Papyrus Functions": ["ERR_UNCAPPED_LOOP"]
  }
}
```

### InjectorAuditor\settings.json

```jsonc
{
  "mo2_profile":   "C:\\Modding\\MO2\\profiles\\Default",
  "mo2_mods_dir":  "C:\\Modding\\MO2\\mods",
  "mo2_overwrite": "C:\\Modding\\MO2\\overwrite",
  "fix_output_mod": "C:\\Modding\\MO2\\mods\\Config Fixes",

  // If a mod has multiple plugins and auto-detection fails, specify the
  // correct plugin name here so FormID qualification works.
  "plugin_overrides": {
    "Some Mod With Two Plugins": "CorrectPlugin.esp"
  }
}
```

---

## Usage

### Full analysis + auto-fix (recommended)

```
RunScanners.bat
```

Both tools run with `--fix` enabled. Reports open automatically when done.

### Script Scanner only

```
python ScriptScanner\scanner.py [flags]
```

| Flag | Description |
|---|---|
| `--fix` | Compile patched scripts to `fix_output_mod\Scripts\` |
| `--severity CRITICAL` | Only report CRITICAL findings |
| `--mod "Mod Name"` | Scan a single mod |
| `--no-cleanup` | Keep decompiled source in cache after run |

### Injector Auditor only

```
python InjectorAuditor\auditor.py [flags]
```

| Flag | Description |
|---|---|
| `--fix` | Write corrected ini files to `fix_output_mod\` |
| `--type spid\|kid\|skypatcher` | Scan one framework only |
| `--severity CRITICAL` | Only report CRITICAL findings |
| `--mod "Mod Name"` | Audit a single mod |

---

## Understanding the output

Reports are written to `ScriptScanner\output\` and `InjectorAuditor\output\` as timestamped Markdown files.

### Script Scanner severities

| Severity | Rule | Meaning |
|---|---|---|
| CRITICAL | `ERR_ORPHANED_REGISTER` | Script registers for updates/events but never unregisters — leaks persist across saves |
| CRITICAL | `ERR_UNCAPPED_LOOP` | `While` loop with no `Utility.Wait()` — freezes the Papyrus thread |
| CRITICAL | `ERR_BUSY_WAIT` | `RegisterForUpdate` with interval < 0.5s — hammers the update queue |
| WARNING | `WARN_MISSING_EFFECT_CLEANUP` | `ActiveMagicEffect` script registers without `OnEffectFinish` — registrations survive effect end |

### Injector Auditor severities

| Severity | Rule | Meaning |
|---|---|---|
| CRITICAL | `ERR_SPID_INVALID_FORMID` | SPID FormID missing `~Plugin.esp` qualifier |
| CRITICAL | `ERR_KID_INVALID_FORMID` | KID FormID or keyword syntax invalid |
| CRITICAL | `ERR_SP_INVALID_HEX` | SkyPatcher FormID contains non-hex characters |
| CRITICAL | `ERR_SP_MALFORMED_REF` | SkyPatcher plugin reference malformed |
| NOTICE | `NOTE_SPID_NO_FILTER` | SPID distributes to all NPCs with no filter |

---

## Config Fixes mod

The `--fix` flag writes output to a single MO2 mod called **Config Fixes**:

```
Config Fixes\
├── Scripts\          ← patched .pex files from Script Scanner
│   └── *.pex
└── *.ini             ← corrected SPID/KID/SkyPatcher configs from Injector Auditor
```

Make sure this mod is **enabled** in MO2 and positioned **near the bottom** of the left panel (high priority) so its files override the originals.

On subsequent runs the scanner reads Config Fixes first via VFS simulation — already-patched scripts are skipped automatically. Scripts that fail to compile (due to Champollion decompile limitations) are recorded in `compile_failures.json` inside Config Fixes and never retried.

---

## Suppressing false positives

### Line-level suppression (Script Scanner)

Place this comment on the line immediately before the flagged line:

```papyrus
; optimization-scanner-ignore: ERR_ORPHANED_REGISTER
self.RegisterForModEvent("MyEvent", "OnMyEvent")
```

### Mod-level suppression

Add to `mod_rule_suppressions` in `ScriptScanner\settings.json`:

```json
"mod_rule_suppressions": {
  "My Mod Name": ["ERR_ORPHANED_REGISTER", "WARN_MISSING_EFFECT_CLEANUP"]
}
```

---

## Common issues

**"Python not found"** — Install Python 3.8+ from python.org and check "Add to PATH" during setup. Or edit the `PYTHON` variable at the top of `RunScanners.bat`.

**Champollion errors** — Make sure `champollion_exe` points to `Champollion.exe` (not the folder). Download from https://github.com/Orvid/Champollion/releases

**`--fix` compile failures** — Scripts decompiled by Champollion sometimes have artifacts that prevent recompilation. These are recorded in `compile_failures.json` and skipped on future runs. This is expected for complex scripts.

**Import path short names** — PapyrusCompiler cannot handle spaces in the semicolon-delimited `-i=` import string. Find 8.3 short names by running this in cmd:
```
for %f in ("C:\Modding\MO2\mods\Skyrim Script Extender (SKSE64)") do echo %~sf
```

---

## License

MIT — free to use, modify, and share. If you improve the rule set or add new checks, pull requests are welcome.
