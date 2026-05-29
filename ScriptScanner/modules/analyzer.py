"""
Static analysis engine for decompiled Papyrus scripts.

Uses a stateful line iterator (state machine) rather than pure per-line regex
so that block-scoped rules (ERR_UNCAPPED_LOOP, WARN_PLAYER_LOOKUP) can see
their full context before firing.

Suppression:
  Place this comment on the line immediately before the flagged line:
    ; optimization-scanner-ignore: RULE_ID
  The engine silently drops that specific match.

Nested While loops:
  Depth-tracked via a counter so inner EndWhile does not prematurely close
  the outer buffer.  Each top-level While block is evaluated independently.
"""

import os
import re


# ── Helpers ───────────────────────────────────────────────────────────────────

def _suppressed(prev_line: str, rule_id: str) -> bool:
    return f'optimization-scanner-ignore: {rule_id}' in prev_line


def _finding(rule: dict, lineno: int, snippet: str, script: str, mod: str) -> dict:
    return {
        'rule_id':     rule['id'],
        'name':        rule['name'],
        'severity':    rule['severity'],
        'line':        lineno,
        'snippet':     snippet[:200],
        'script':      script,
        'mod':         mod,
        'remediation': rule.get('remediation', ''),
    }


# ── Analyser class ────────────────────────────────────────────────────────────

class ScriptAnalyzer:
    def __init__(self, rules: list):
        # Index rules by id for O(1) lookup
        self.rules = {r['id']: r for r in rules}

    def analyze_file(self, psc_path: str, script_name: str, mod_name: str) -> list:
        try:
            with open(psc_path, encoding='utf-8', errors='replace') as f:
                lines = [l.rstrip('\n\r') for l in f.readlines()]
        except OSError:
            return []

        findings = []

        # ── State machine variables ───────────────────────────────────────
        prev          = ''          # previous stripped line (for line-rule suppression)

        # Event-block context
        event_name      = None      # lowercase name of current Event block, or None
        event_start     = 0         # 1-based line where Event opened
        event_open_prev = ''        # prev captured at Event open (for block suppression)
        event_lines     = []        # body lines collected between Event and EndEvent
        player_calls    = 0         # Game.GetPlayer() count within OnUpdate

        # While-block context (depth-tracked to handle nested whiles)
        while_depth     = 0
        while_lines     = []        # all lines in the outermost While block
        while_start     = 0         # 1-based line where the outermost While opened
        while_open_prev = ''        # prev captured at outermost While open (for suppression)

        # File-scope save-bloat tracking
        script_extends       = None  # lowercased base class from scriptname ... extends X
        persistent_registers = []    # (lineno, snippet, prev_line) — RegisterForUpdate/ModEvent/AnimEvent
        single_in_onupdate   = None  # becomes (lineno, snippet) when triggered inside OnUpdate
        any_register_calls   = []    # (lineno, snippet) — all RegisterFor* (for AME check)
        has_any_unregister   = False # any UnregisterFor* seen anywhere in the file
        has_effect_finish    = False # OnEffectFinish event block found

        # ── Main loop ────────────────────────────────────────────────────
        for i, raw in enumerate(lines):
            line = raw.strip()
            ll   = line.lower()
            lineno = i + 1

            # ── scriptName ... extends X ─────────────────────────────────
            m_ext = re.match(r'^scriptname\s+\w+\s+extends\s+(\w+)', line, re.IGNORECASE)
            if m_ext:
                script_extends = m_ext.group(1).lower()

            # ── Event open ───────────────────────────────────────────────
            m = re.match(r'^event\s+(\w+)\s*\(', line, re.IGNORECASE)
            if m:
                event_name      = m.group(1).lower()
                event_start     = lineno
                event_open_prev = prev   # capture here — suppression comment goes BEFORE Event
                event_lines     = []
                player_calls    = 0
                if event_name == 'oneffectfinish':
                    has_effect_finish = True
                prev = line
                continue

            # ── Event close ──────────────────────────────────────────────
            if ll == 'endevent' and event_name is not None:
                self._check_empty_event(event_name, event_start, event_lines,
                                        event_open_prev, script_name, mod_name, findings)
                self._check_player_lookup(event_name, event_start, player_calls,
                                          event_open_prev, script_name, mod_name, findings)
                event_name  = None
                event_lines = []
                prev = line
                continue

            # ── Collect event body ────────────────────────────────────────
            if event_name is not None:
                event_lines.append(line)

            # ── While open ───────────────────────────────────────────────
            if re.match(r'^while\s*\(', line, re.IGNORECASE):
                while_depth += 1
                if while_depth == 1:
                    while_lines     = [line]
                    while_start     = lineno
                    while_open_prev = prev   # suppression comment goes BEFORE While
                else:
                    while_lines.append(line)  # nested while — buffer only

            # ── While close ───────────────────────────────────────────────
            elif ll == 'endwhile' and while_depth > 0:
                while_lines.append(line)
                while_depth -= 1
                if while_depth == 0:
                    self._check_uncapped_loop(while_start, while_lines,
                                              while_open_prev, script_name, mod_name, findings)
                    while_lines = []

            # ── While body ────────────────────────────────────────────────
            elif while_depth > 0:
                while_lines.append(line)

            # ── ERR_BUSY_WAIT (line-level, numeric comparison) ───────────
            self._check_busy_wait(line, lineno, prev, script_name, mod_name, findings)

            # ── WARN_PLAYER_LOOKUP: count calls inside OnUpdate ───────────
            if event_name == 'onupdate' and re.search(r'Game\.GetPlayer\(\)', line, re.IGNORECASE):
                player_calls += 1

            # ── File-scope save-bloat tracking ────────────────────────────
            # (?<![A-Za-z_]) ensures we match actual calls, not identifiers that
            # contain RegisterFor* as a substring (e.g. CheckLocationAndRegisterForUpdate)
            if re.search(r'(?<![A-Za-z_])RegisterFor\w+\s*\(', line, re.IGNORECASE):
                any_register_calls.append((lineno, line))

            if re.search(r'(?<![A-Za-z_])RegisterFor(Update|ModEvent|AnimationEvent)\s*\(', line, re.IGNORECASE):
                persistent_registers.append((lineno, line, prev))

            if (event_name == 'onupdate' and single_in_onupdate is None
                    and re.search(r'(?<![A-Za-z_])RegisterForSingleUpdate\s*\(', line, re.IGNORECASE)):
                single_in_onupdate = (lineno, line)  # capture first occurrence

            if re.search(r'UnregisterFor', line, re.IGNORECASE):
                has_any_unregister = True

            prev = line

        # ── File-scope checks (after full parse) ─────────────────────────
        self._check_orphaned_register(
            persistent_registers, single_in_onupdate, has_any_unregister,
            script_name, mod_name, findings)
        self._check_effect_cleanup(
            script_extends, any_register_calls, has_effect_finish,
            script_name, mod_name, findings)

        return findings

    # ── Rule check methods ────────────────────────────────────────────────────

    def _check_busy_wait(self, line, lineno, prev, script, mod, findings):
        rule = self.rules.get('ERR_BUSY_WAIT')
        if not rule:
            return
        m = re.search(r'(?<![A-Za-z_])RegisterForUpdate\s*\(\s*([0-9]*\.?[0-9]+)\s*\)', line, re.IGNORECASE)
        if not m:
            return
        try:
            interval = float(m.group(1))
        except ValueError:
            return
        if interval < rule.get('numeric_threshold', 0.5) and not _suppressed(prev, 'ERR_BUSY_WAIT'):
            findings.append(_finding(rule, lineno, line, script, mod))

    def _check_uncapped_loop(self, while_start, while_lines, prev, script, mod, findings):
        rule = self.rules.get('ERR_UNCAPPED_LOOP')
        if not rule:
            return
        block = '\n'.join(while_lines).lower()
        if 'utility.wait' not in block and not _suppressed(prev, 'ERR_UNCAPPED_LOOP'):
            findings.append(_finding(rule, while_start,
                                     f'While loop at line {while_start} — no Utility.Wait()',
                                     script, mod))

    def _check_player_lookup(self, event_name, event_start, player_calls,
                              prev, script, mod, findings):
        rule = self.rules.get('WARN_PLAYER_LOOKUP')
        if not rule:
            return
        target_event = rule.get('event_name', 'onupdate').lower()
        min_count    = rule.get('min_count', 2)
        if (event_name == target_event and player_calls >= min_count
                and not _suppressed(prev, 'WARN_PLAYER_LOOKUP')):
            findings.append(_finding(rule, event_start,
                                     f'OnUpdate calls Game.GetPlayer() {player_calls} times',
                                     script, mod))

    def _check_empty_event(self, event_name, event_start, event_lines,
                            prev, script, mod, findings):
        rule = self.rules.get('NOTE_EMPTY_EVENT')
        if not rule:
            return
        # A line is "real" if it's non-blank and not a comment
        real = [l for l in event_lines if l.strip() and not l.strip().startswith(';')]
        if not real and not _suppressed(prev, 'NOTE_EMPTY_EVENT'):
            findings.append(_finding(rule, event_start,
                                     f'Event {event_name}()',
                                     script, mod))

    def _check_orphaned_register(self, persistent_registers, single_in_onupdate,
                                  has_any_unregister, script, mod, findings):
        rule = self.rules.get('ERR_ORPHANED_REGISTER')
        if not rule:
            return
        fired = False
        # Persistent loop registrations (RegisterForUpdate/ModEvent/AnimEvent):
        # an unregister anywhere in the file is sufficient to silence this.
        if persistent_registers and not has_any_unregister:
            lineno, snippet, open_prev = persistent_registers[0]
            if not _suppressed(open_prev, 'ERR_ORPHANED_REGISTER'):
                findings.append(_finding(rule, lineno, snippet, script, mod))
                fired = True
        # RegisterForSingleUpdate inside OnUpdate creates a self-perpetuating loop.
        # An unregister *elsewhere* in the file does not stop the OnUpdate cycle,
        # so this fires independently of has_any_unregister.
        if not fired and single_in_onupdate is not None:
            lineno, snippet = single_in_onupdate
            findings.append(_finding(rule, lineno, snippet, script, mod))

    def _check_effect_cleanup(self, script_extends, any_register_calls,
                               has_effect_finish, script, mod, findings):
        rule = self.rules.get('WARN_MISSING_EFFECT_CLEANUP')
        if not rule or script_extends != 'activemagiceffect':
            return
        if not any_register_calls or has_effect_finish:
            return
        lineno, snippet = any_register_calls[0][0], any_register_calls[0][1]
        findings.append(_finding(rule, lineno,
                                 'ActiveMagicEffect script registers without OnEffectFinish',
                                 script, mod))
