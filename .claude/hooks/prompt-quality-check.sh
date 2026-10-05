#!/bin/bash
# Prompt Quality Gate — PreToolUse hook on Agent tool
# Scores every agent prompt against 6 PQ dimensions (heuristic, no LLM required).
# Outputs scored feedback to Claude's context (stdout) + appends to ~/.code-insights/pq-log.jsonl.
#
# Matching is literal: the prompt is split into lowercase word tokens and checked
# for word/phrase membership. No regex — see scripts/utils/no-regex-debt.txt and
# hooks/claude/test-integrity-guard.sh for the pattern.

set -euo pipefail

EVENT=$(cat)
PROMPT=$(echo "$EVENT" | python3 -c "
import json, sys
try:
    e = json.load(sys.stdin)
    inp = e.get('tool_input', {})
    print(inp.get('prompt', '') or inp.get('description', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

[[ -z "$PROMPT" ]] && exit 0

python3 - "$PROMPT" << 'PYEOF'
import sys, json, datetime, hashlib, pathlib

prompt = sys.argv[1]
words = prompt.split()
text_lower = prompt.lower()


def tokenize(t):
    """Lowercase word tokens. Apostrophes stay inside words ("don't"); every
    other non-alphanumeric character is a separator, so "double-check" is the
    two tokens "double check"."""
    return ''.join(c if c.isalnum() or c == "'" else ' ' for c in t.lower()).split()


class Text:
    """A prompt as word tokens, with phrase lookups on token boundaries."""

    def __init__(self, t):
        self.raw = t
        self.toks = tokenize(t)
        self.padded = ' ' + ' '.join(self.toks) + ' '

    def has(self, *phrases):
        return any(f' {" ".join(tokenize(p))} ' in self.padded for p in phrases)

    def count(self, *phrases):
        return sum(self.padded.count(f' {" ".join(tokenize(p))} ') for p in phrases)

    def has_the_x_noun(self, nouns):
        """'the <word> <noun>' as three whitespace-separated words — e.g. 'the
        parse function', 'the graded agent's'. The middle word must be a single
        word ('the reflect-agent' does not count), matching on raw words rather
        than tokens so a hyphen or apostrophe does not split a word in two."""
        w = self.raw.split()
        return any(
            w[i].lstrip(PUNCT) == 'the' and is_word(w[i + 1]) and word_head(w[i + 2]) in nouns
            for i in range(len(w) - 2)
        )


PUNCT = '([{"\'`*_-'


def is_word(s):
    return bool(s) and all(c.isalnum() or c == '_' for c in s)


def word_head(s):
    """Leading run of word characters: "agent's" -> "agent", "hook." -> "hook"."""
    head = []
    for c in s:
        if not (c.isalnum() or c == '_'):
            break
        head.append(c)
    return ''.join(head)


ACTION_VERBS = ('implement', 'create', 'refactor', 'extract', 'rename', 'migrate', 'add',
                'remove', 'replace', 'verify', 'check', 'update', 'analyze', 'generate',
                'write', 'find', 'list')
TARGET_NOUNS = {'function', 'class', 'file', 'module', 'component', 'endpoint', 'hook',
                'method', 'script', 'tool', 'agent'}


def mentions_location(t):
    if any(s in t.raw for s in ('file', 'path', '.py', '.ts', '.js', '.sh', 'context:')):
        return True
    toks = t.toks
    if any(toks[i] == 'at' and toks[i + 1] == 'line' and toks[i + 2].isdigit()
           for i in range(len(toks) - 2)):
        return True
    raw_words = t.raw.split()
    return any(w == 'see' and nxt.endswith('.') and nxt[:-1].isalnum()
               for w, nxt in zip(raw_words, raw_words[1:]))


def score_context_provision(t, w):
    s = 2
    if mentions_location(t): s += 1
    if t.has('already', 'previously', 'tried', 'broken', 'failing', 'current', 'existing',
             'background', 'prior', 'working on'): s += 1
    if len(w) >= 30: s += 1
    return min(s, 5)


def score_request_specificity(t, w):
    s = 2
    if t.has('help me', 'look at', 'something', 'somehow', 'maybe', 'probably', 'sort of',
             'kind of', 'fix this'): s -= 1
    if t.has(*ACTION_VERBS): s += 1
    if t.has_the_x_noun(TARGET_NOUNS): s += 1
    if len(w) < 10: s -= 1
    return max(1, min(s, 5))


def score_scope_management(t, w):
    s = 2
    if t.has('only', "don't", 'do not', 'must not', 'limited to', 'no other', 'without touching',
             'leave', 'preserve', 'just the', 'exclusively'): s += 2
    if t.has('return', 'output', 'report', 'list', 'show', 'summarize', 'format', 'as json',
             'as markdown', 'emit', 'print'): s += 1
    if t.has('everything', 'all of', 'anywhere', 'any file', 'comprehensive',
             'whatever you need', 'whatever makes sense'): s -= 1
    return max(1, min(s, 5))


def score_information_timing(t, w):
    s = 3
    first_third = Text(' '.join(w[:max(1, len(w) // 3)]))
    if first_third.has('implement', 'create', 'refactor', 'find', 'check', 'verify', 'analyze',
                       'review', 'fix', 'add', 'remove', 'write', 'list', 'generate'): s += 1
    if t.raw.strip().startswith(('given', 'since', 'because', 'as you know', 'note that',
                                 'remember that', 'context:', 'background:')): s -= 1
    return max(1, min(s, 5))


def score_correction_quality(t):
    if not t.has('wrong', 'incorrect', 'actually', 'instead', 'not what', 'should be',
                 'the issue', 'the problem', 'you missed', 'missed', "that's not", 'that is not'):
        return None
    s = 2
    toks = t.toks
    names_wrong_thing = any(
        toks[i] == 'the' and toks[i + 2] in ('was', 'is') and toks[i + 3] == 'wrong'
        for i in range(len(toks) - 3)
    )
    if t.has('specifically', 'exactly', 'wrong because') or names_wrong_thing: s += 2
    if t.has('should', 'expect', 'correct version', 'instead use', 'replace with', 'the right'): s += 1
    return min(s, 5)


SCRATCHPAD = ('use a scratchpad', 'write your reasoning in', 'think step by step in a scratchpad')
THINK_INSTRUCTIONS = ('think carefully', 'think step by step', 'think hard', 'think harder',
                      'think deeply')
SHOW_REASONING = ('show your reasoning', 'show your thinking', 'reveal your reasoning',
                  'include your chain of thought', 'show your chain of thought')


def detect_anti_patterns(t):
    """Named prompt anti-patterns that bloat or degrade agent prompts without
    adding real signal — flagged separately from the 1-5 dimension scores
    above since these are presence/absence checks, not a spectrum."""
    found = []
    if t.raw.count('example:') + t.raw.count('e.g.') >= 2:
        found.append(('stale-few-shot', 'multiple example blocks — verify they still match the current codebase, or drop them'))
    scratchpad = t.has(*SCRATCHPAD)
    if scratchpad:
        found.append(('mandatory-scratchpad', 'forced scratchpad ritual — only ask for it if the task genuinely needs visible intermediate reasoning'))
    if t.has('maximally thorough', 'as thorough as possible', 'leave no stone unturned',
             'be extremely comprehensive', 'utmost thoroughness'):
        found.append(('maximally-thorough-phrasing', 'vague intensifier — name the actual completeness criterion instead (which files, which cases)'))
    if t.count('verify', 'double check', 'triple check', 'make sure to confirm') >= 3:
        found.append(('verification-ritual', 'repeated verify/check phrasing — state the one concrete check that matters instead of stacking synonyms'))
    if not scratchpad and t.has(*THINK_INSTRUCTIONS):
        found.append(('think-instruction', 'the model always reasons before replying — drop it, or raise the effort level instead'))
    if t.has(*SHOW_REASONING):
        found.append(('show-reasoning-request', 'requests to expose reasoning can be declined — ask for a short explanation of the chosen approach instead'))
    return found


text = Text(text_lower)
ctx    = score_context_provision(text, words)
spec   = score_request_specificity(text, words)
scope  = score_scope_management(text, words)
timing = score_information_timing(text, words)
corr   = score_correction_quality(text)

applicable = [ctx, spec, scope, timing] + ([corr] if corr is not None else [])
overall = round(sum(applicable) / len(applicable), 1)

ICON = {5: '✅', 4: '✅', 3: '🟡', 2: '⚠ ', 1: '❌'}
TIPS = {
    'context_provision':  'add file paths, prior attempts, or relevant background',
    'request_specificity': 'name the specific target (function/file/class) and use an action verb',
    'scope_management':   "state what NOT to change; specify output format",
    'information_timing': 'lead with the goal — state the ask before the context',
    'correction_quality': 'name the exact error and describe the expected correct behavior',
}

def dim_line(label, val, tip_key):
    if val is None:
        return f"  —  {label}: N/A"
    icon = ICON.get(val, '⚠ ')
    line = f"  {icon} {label}: {val}/5"
    if val <= 3:
        line += f"  →  {TIPS[tip_key]}"
    return line

print(f"⚡ PQ Score: {overall}/5")
print(dim_line('context_provision',  ctx,   'context_provision'))
print(dim_line('request_specificity', spec, 'request_specificity'))
print(dim_line('scope_management',    scope, 'scope_management'))
print(dim_line('information_timing',  timing, 'information_timing'))
print(dim_line('correction_quality',  corr,  'correction_quality'))
anti_patterns = detect_anti_patterns(text)
if anti_patterns:
    print("  🚩 Anti-patterns:")
    for name, tip in anti_patterns:
        print(f"     - {name}: {tip}")

if overall < 3.5:
    print("  ⬆  Consider improving flagged dimensions before spawning.")
elif overall < 4.0:
    print("  ✍  Good prompt. Address ⚠ items if time allows.")
else:
    print("  ✅ Strong prompt — proceed.")

# Log entry
log_dir = pathlib.Path.home() / '.code-insights'
log_dir.mkdir(parents=True, exist_ok=True)
entry = {
    'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'overall': overall,
    'dims': {
        'context_provision': ctx,
        'request_specificity': spec,
        'scope_management': scope,
        'information_timing': timing,
        'correction_quality': corr,
    },
    'prompt_hash': hashlib.sha256(prompt.encode()).hexdigest()[:12],
    'word_count': len(words),
    'anti_patterns': [name for name, _ in anti_patterns],
}
with open(log_dir / 'pq-log.jsonl', 'a') as f:
    f.write(json.dumps(entry) + '\n')
PYEOF
