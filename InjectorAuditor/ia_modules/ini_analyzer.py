"""
Parse and validate SPID, KID, and SkyPatcher config files.

Each format has its own analyze_* method.  All three share:
  - A common _finding() factory that produces dicts compatible with
    ScriptScanner's reporter.write_report() ('script' key holds file path).
  - Line-by-line parsing that skips blank lines and ; comments.
  - Per-rule dispatch via match_type strings defined in injector_ruleset.json.

SPID valid FormID patterns (all accepted):
  0x[hex]+~Plugin.esp/esm/esl   — standard prefixed FormID
  [hex]+~Plugin.esp/esm/esl     — unprefixed (seen in Apothecary_DISTR.ini)
  EditorID~Plugin.esp/esm/esl   — EditorID reference with plugin context
  Plain keyword EditorID         — word chars only, no tilde

KID ObjectType known set (hardcoded — source: KID documentation + real-world data):
  Activator, Ammo, Armor, Book, Clothing, Enchantment, Flora, Furniture,
  Ingredient, Key, Location, Magic Effect, Misc Item, NPC, Potion, Race,
  Scroll, Soul Gem, Spell, Weapon

SkyPatcher tilde-parameter:
  Plugin|FormID~param~param  — strip everything after first ~ before hex check.
"""

import os
import re

# ── SPID known entry types — lines with a different entry_type are skipped ─────
_SPID_ENTRY_TYPES = {
    'spell', 'perk', 'item', 'levitem', 'outfit', 'deathitem',
    'keyword', 'faction', 'sleepoutfit', 'skin', 'package',
}

# ── KID known object types ─────────────────────────────────────────────────────
# Includes both full names and 4-letter record-type codes (ARMO, WEAP, etc.)
# and common abbreviations (Ingr, Misc) used by various KID file authors.
_KID_KNOWN_TYPES = {
    # Full names
    'activator', 'ammo', 'armor', 'book', 'clothing', 'enchantment',
    'flora', 'furniture', 'ingredient', 'key', 'location', 'magic effect',
    'misc item', 'npc', 'potion', 'race', 'scroll', 'soul gem', 'spell', 'weapon',
    # 4-letter record codes
    'acti', 'alch', 'ammo', 'arma', 'armo', 'book', 'clot', 'ench',
    'flor', 'furn', 'ingr', 'keym', 'mgef', 'misc', 'npc_', 'otft',
    'pack', 'race', 'slgm', 'spel', 'weap',
    # Common abbreviations
    'misc', 'ingr',
}

# ── SPID/KID FormID regex ──────────────────────────────────────────────────────
# Hex FormID with optional 0x prefix:  0x[hex]~Plugin  or  [hex]~Plugin
_FORMID_HEX_RE  = re.compile(r'^(0x)?[0-9A-Fa-f]+~[^|]+\.(esp|esm|esl)$', re.IGNORECASE)
# EditorID~Plugin (EditorID = word chars + dots, followed by tilde + plugin):
_FORMID_EDID_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_\.]*~[^|]+\.(esp|esm|esl)$', re.IGNORECASE)
# Plain keyword name (no tilde):
_KEYWORD_RE     = re.compile(r'^[A-Za-z_][A-Za-z0-9_\.]*$')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _finding(rule: dict, lineno: int, snippet: str, file_path: str, mod_name: str) -> dict:
    return {
        'rule_id':     rule['id'],
        'name':        rule['name'],
        'severity':    rule['severity'],
        'line':        lineno,
        'snippet':     snippet[:200],
        'script':      file_path,   # reporter uses 'script' key — reuse for file path
        'mod':         mod_name,
        'remediation': rule.get('remediation', ''),
    }


def _is_valid_formid_or_keyword(value: str) -> bool:
    """Return True if value is a valid SPID/KID FormID reference or keyword name."""
    v = value.strip()
    # Strip comma-separated list — take first token only
    v = v.split(',')[0].strip()
    if _FORMID_HEX_RE.match(v):   # 0x[hex]~Plugin or [hex]~Plugin
        return True
    if _FORMID_EDID_RE.match(v):  # EditorID~Plugin
        return True
    if _KEYWORD_RE.match(v):      # plain keyword name
        return True
    return False


def _parse_sp_refs(value: str) -> list:
    """
    Extract all Plugin|FormID references from a SkyPatcher value string.
    Returns list of (plugin, formid_raw) tuples — formid_raw may include ~ params.
    Only returns entries that contain '|'; plain keyword values are skipped.
    """
    refs = []
    # Values may be comma-separated lists of refs
    for token in value.split(','):
        token = token.strip()
        if '|' not in token:
            continue  # plain keyword or numeric, not a plugin ref
        parts = token.split('|', 1)
        if len(parts) == 2:
            refs.append((parts[0].strip(), parts[1].strip()))
    return refs


# ── Analyser class ────────────────────────────────────────────────────────────

class IniAnalyzer:
    def __init__(self, rules: list):
        self.rules      = {r['id']: r for r in rules}
        self.spid_rules = [r for r in rules if r.get('framework') == 'spid']
        self.kid_rules  = [r for r in rules if r.get('framework') == 'kid']
        self.sp_rules   = [r for r in rules if r.get('framework') == 'skypatcher']

    # ── SPID ──────────────────────────────────────────────────────────────────

    def analyze_spid_file(self, file_path: str, mod_name: str) -> list:
        findings = []
        try:
            with open(file_path, encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
        except OSError:
            return findings

        for lineno, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith(';'):
                continue
            if '=' not in line:
                continue

            _entry_type, value_str = line.split('=', 1)
            # Skip separator or noise lines — valid SPID lines have a known entry type
            if _entry_type.strip().lower() not in _SPID_ENTRY_TYPES:
                continue
            fields = [f.strip() for f in value_str.split('|')]
            snippet = line

            for rule in self.spid_rules:
                mt = rule['match_type']

                if mt == 'field_formid':
                    fi = rule.get('field_index', 0)
                    if fi < len(fields):
                        # field[0] may be a comma-separated FormID list; check each token
                        for token in fields[fi].split(','):
                            token = token.strip()
                            if token and not _is_valid_formid_or_keyword(token):
                                findings.append(_finding(rule, lineno, snippet, file_path, mod_name))
                                break

                elif mt == 'field_numeric':
                    fi = rule.get('field_index', -1)
                    if len(fields) >= 7:  # chance field only meaningful when 7+ fields
                        try:
                            idx_field = fields[fi] if fi != -1 else fields[-1]
                            val = float(idx_field)
                            op  = rule.get('operator', 'eq')
                            threshold = rule.get('value', 0)
                            if op == 'eq' and val == threshold:
                                findings.append(_finding(rule, lineno, snippet, file_path, mod_name))
                        except (ValueError, IndexError):
                            pass

                elif mt == 'field_all_none':
                    check_fields = rule.get('fields', [])
                    if len(fields) >= max(check_fields, default=0) + 1:
                        if all(fields[i].upper() == 'NONE' for i in check_fields if i < len(fields)):
                            findings.append(_finding(rule, lineno, snippet, file_path, mod_name))

        return findings

    # ── KID ───────────────────────────────────────────────────────────────────

    def analyze_kid_file(self, file_path: str, mod_name: str) -> list:
        findings = []
        try:
            with open(file_path, encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
        except OSError:
            return findings

        for lineno, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith(';'):
                continue
            if '=' not in line:
                continue

            _entry_type, value_str = line.split('=', 1)
            fields = [f.strip() for f in value_str.split('|')]
            snippet = line

            for rule in self.kid_rules:
                mt = rule['match_type']

                if mt == 'field_formid':
                    fi = rule.get('field_index', 0)
                    if fi < len(fields):
                        token = fields[fi].split(',')[0].strip()
                        if token and not _is_valid_formid_or_keyword(token):
                            findings.append(_finding(rule, lineno, snippet, file_path, mod_name))

                elif mt == 'field_enum':
                    fi = rule.get('field_index', 1)
                    # Only check when 3+ fields exist (entry has explicit ObjectType)
                    if len(fields) >= 3 and fi < len(fields):
                        raw_type = fields[fi].strip()
                        obj_type = raw_type.lower()
                        # Skip if field looks like a keyword EditorID, FormID ref, or
                        # a plugin filename (Keyword|Plugin.esp|LocalIndex shorthand).
                        # These are all valid KID filter layouts — not the ObjectType field.
                        is_plugin = bool(re.search(r'\.(esp|esm|esl)$', raw_type, re.IGNORECASE))
                        if is_plugin or _KEYWORD_RE.match(raw_type) or _is_valid_formid_or_keyword(raw_type):
                            pass  # ambiguous layout — not safe to flag
                        elif obj_type and obj_type not in _KID_KNOWN_TYPES:
                            findings.append(_finding(rule, lineno, snippet, file_path, mod_name))

                elif mt == 'field_exact':
                    fi = rule.get('field_index', 2)
                    expected = rule.get('value', '')
                    if len(fields) >= 3 and fi < len(fields):
                        if fields[fi].strip() == expected:
                            findings.append(_finding(rule, lineno, snippet, file_path, mod_name))

        return findings

    # ── SkyPatcher ────────────────────────────────────────────────────────────

    def analyze_skypatcher_file(self, file_path: str, mod_name: str,
                                 record_type: str = '') -> list:
        findings = []
        try:
            with open(file_path, encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
        except OSError:
            return findings

        for lineno, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith(';') or line.startswith('['):
                continue

            snippet = line

            # SkyPatcher lines are colon-separated action chains (key=value:key=value).
            # Split ONLY at ':' immediately followed by a word= pattern to avoid
            # breaking on colons that appear inside values (timestamps, version strings).
            segments = re.split(r':(?=\w+=)', line)
            for seg in segments:
                if '=' not in seg:
                    continue
                _key, val = seg.split('=', 1)
                val = val.strip()

                # Extract Plugin|FormID references from the value
                refs = _parse_sp_refs(val)
                for plugin, formid_raw in refs:
                    # Strip tilde-parameters (Plugin|FormID~param) AND
                    # equals-quantity suffixes (Plugin|FormID=1) before hex check.
                    formid = formid_raw.split('~')[0].split('=')[0].strip()

                    for rule in self.sp_rules:
                        mt = rule['match_type']

                        if mt == 'sp_ref_structure':
                            # Malformed if plugin or formid part is empty
                            if not plugin or not formid:
                                findings.append(_finding(rule, lineno, snippet,
                                                         file_path, mod_name))

                        elif mt == 'sp_ref_hex':
                            # FormID must be purely hexadecimal
                            if formid and not re.match(r'^[0-9A-Fa-f]+$', formid):
                                findings.append(_finding(rule, lineno, snippet,
                                                         file_path, mod_name))

        return findings
