#!/usr/bin/env bash
# sync_issues.sh — Reconcile QUESTIONS.md (at the repo root) with the
# GitHub issues in soterlabs/settlement-cycle.
#
# Editing model:
#   - QUESTIONS.md owns question CONTENT (text, counterparty, priority).
#   - GitHub issues own LIFECYCLE (open ↔ closed, comments, triage).
#
# Modes:
#   --check  (default)   Dry run. Reports drift. Exit 1 on hard drift.
#   --apply              Reconcile both directions:
#                          • create issues missing in GitHub
#                          • update title/body/labels of existing issues
#                          • close orphans (Q-ID dropped from QUESTIONS.md)
#                          • move closed-issue Q-IDs to ## Resolved in markdown
#                        Prompts before destructive ops unless --yes.
#
# Flags:
#   --quiet  / -q        Suppress soft warnings (body-text-only diffs).
#   --yes    / -y        Auto-confirm destructive ops (close orphans).
#   --help   / -h        Show this help.
#
# Exit codes:
#   0 — clean (or --apply succeeded)
#   1 — hard drift detected in --check
#   2 — runtime / setup error (gh missing, file not found, etc.)

set -uo pipefail

REPO="soterlabs/settlement-cycle"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
QFILE="$REPO_ROOT/QUESTIONS.md"

MODE=check
QUIET=0
YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE=check; shift ;;
    --apply) MODE=apply; shift ;;
    --quiet|-q) QUIET=1; shift ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

command -v gh >/dev/null 2>&1 || { echo "error: gh CLI not found." >&2; exit 2; }
[[ -f "$QFILE" ]] || { echo "error: $QFILE not found" >&2; exit 2; }

ISSUES_JSON=$(gh issue list --repo "$REPO" --state all --limit 500 \
  --json number,title,labels,body,state,closedAt 2>&1) || {
  echo "error: gh issue list failed:" >&2
  echo "$ISSUES_JSON" >&2
  exit 2
}

REPO="$REPO" QFILE="$QFILE" \
  MODE="$MODE" QUIET="$QUIET" YES="$YES" ISSUES_JSON="$ISSUES_JSON" \
  python3 - <<'PYEOF'
import hashlib, json, os, re, subprocess, sys
from datetime import datetime

REPO       = os.environ['REPO']
QFILE      = os.environ['QFILE']
MODE       = os.environ['MODE']         # 'check' | 'apply'
QUIET      = os.environ.get('QUIET') == '1'
YES        = os.environ.get('YES') == '1'

COUNTERPARTIES = {'Spark', 'Grove', 'BA Labs'}
PRIORITIES     = {'P0', 'P1', 'P2', 'P3'}
SECTION_LABEL  = {'Grove team': 'Grove', 'Spark team': 'Spark', 'BA labs': 'BA Labs'}
LABEL_SECTION  = {v: k for k, v in SECTION_LABEL.items()}
QID_RE         = re.compile(r'^([GSB]\d+[a-z]?)\.\s')

# ─── Parse QUESTIONS.md ───────────────────────────────────────────
md_text = open(QFILE).read()

def parse_markdown(text):
    """Returns (open_qs, resolved_qids, body_index, raw_blocks).

    open_qs[q_id]  = {section, priority, title, body}
    resolved_qids  = {q_id}                 (just the IDs in ## Resolved)
    body_index     = {q_id: full ##-block text}   (for round-trip rewriting)
    """
    open_qs = {}
    resolved = set()
    blocks = {}

    # Split into top-level sections (## …)
    head_split = re.split(r'^(## .+)$', text, flags=re.M)
    # head_split: [preamble, '## Grove team', body, '## Spark team', body, …, '## Resolved', body]
    for i in range(1, len(head_split), 2):
        header = head_split[i].strip()
        body   = head_split[i+1] if i+1 < len(head_split) else ''
        section_name = header[3:].strip()
        if section_name == 'Resolved':
            for line in body.splitlines():
                m = re.match(r'^### ([GSB]\d+[a-z]?)\.', line)
                if m:
                    resolved.add(m.group(1))
        elif section_name in SECTION_LABEL:
            cp = SECTION_LABEL[section_name]
            # Split priority subsections
            pri_split = re.split(r'^(### P[0-3] — [^\n]*)$', body, flags=re.M)
            for j in range(1, len(pri_split), 2):
                pri_header = pri_split[j]
                pri_body = pri_split[j+1] if j+1 < len(pri_split) else ''
                pm = re.match(r'### (P[0-3]) ', pri_header)
                priority = pm.group(1) if pm else None
                # Split into questions
                for chunk in re.split(r'^(#### .+)$', pri_body, flags=re.M)[1:]:
                    pass  # placeholder — handled below in pairs
                heads = re.split(r'^#### ', pri_body, flags=re.M)
                for chunk in heads[1:]:
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    head_line, _, body_text = chunk.partition('\n')
                    head_line = head_line.strip()
                    # Trim trailing separator (--- before next ##)
                    body_text = re.sub(r'\n---\s*$', '', body_text).strip()
                    qm = QID_RE.match(head_line)
                    if not qm:
                        print(f'warn: unparsed heading: {head_line[:60]!r}', file=sys.stderr)
                        continue
                    q_id = qm.group(1)
                    open_qs[q_id] = {
                        'section': section_name,
                        'counterparty': cp,
                        'priority': priority,
                        'title': head_line,
                        'body': body_text,
                    }
                    blocks[q_id] = '#### ' + chunk.rstrip() + '\n'
    return open_qs, resolved, blocks

open_qs, resolved_qids, blocks = parse_markdown(md_text)

# ─── Index live issues by Q-ID ────────────────────────────────────
issues = json.loads(os.environ['ISSUES_JSON'])
live_by_qid = {}
for it in issues:
    title = (it.get('title') or '').strip()
    qm = QID_RE.match(title)
    if not qm:
        continue
    q_id = qm.group(1)
    labels = {lab['name'] for lab in it.get('labels', [])}
    body = (it.get('body') or '').replace('\r\n', '\n').strip()
    live_by_qid[q_id] = {
        'number':    it['number'],
        'title':     title,
        'labels':    labels,
        'body':      body,
        'state':     it['state'],          # 'OPEN' | 'CLOSED'
        'closedAt':  it.get('closedAt'),
    }

# ─── Reconciliation plan ──────────────────────────────────────────
# Each entry in `actions` is a structured op the apply phase will execute
# (and the check phase will list).
actions = []   # list[(severity, kind, message, callable_or_None)]

def add(sev, kind, msg, fn=None):
    actions.append((sev, kind, msg, fn))

def gh(*args, body=None):
    """Run a gh CLI command. body, if given, is piped to stdin."""
    res = subprocess.run(
        ['gh', *args],
        input=body, text=True, capture_output=True, timeout=60,
    )
    if res.returncode != 0:
        raise RuntimeError(f'gh {args[0]} failed: {res.stderr.strip()[:300]}')
    return res.stdout.strip()

def normalize_body(s):
    return (s or '').replace('\r\n', '\n').strip()

# Forward sync: open_qs → issues
for q_id, q in open_qs.items():
    live = live_by_qid.get(q_id)
    if live is None:
        add('hard', 'CREATE',
            f'create issue for {q_id} ({q["counterparty"]}/{q["priority"]}): {q["title"][:60]}',
            lambda q=q: gh('issue', 'create', '--repo', REPO,
                           '--title', q['title'],
                           '--body-file', '-',
                           '--label', q['counterparty'],
                           '--label', q['priority'],
                           body=q['body']))
        continue
    if live['state'] == 'CLOSED':
        add('hard', 'STATE',
            f'{q_id} present in QUESTIONS.md but issue #{live["number"]} is closed — '
            f'either move {q_id} to ## Resolved (manual) or reopen the issue (manual)',
            None)
        continue
    # Same Q-ID, both sides open → check labels + title + body
    needed_labels = {q['counterparty'], q['priority']}
    current_meta_labels = live['labels'] & (COUNTERPARTIES | PRIORITIES)
    add_labels = needed_labels - current_meta_labels
    remove_labels = current_meta_labels - needed_labels
    title_changed = live['title'] != q['title']
    body_changed = normalize_body(live['body']) != normalize_body(q['body'])

    if add_labels or remove_labels or title_changed:
        bits = []
        if title_changed: bits.append('title')
        if add_labels: bits.append(f'+{",".join(sorted(add_labels))}')
        if remove_labels: bits.append(f'-{",".join(sorted(remove_labels))}')
        msg = f'update issue #{live["number"]} ({q_id}): {", ".join(bits)}'
        def make_edit(live=live, q=q, add_labels=add_labels, remove_labels=remove_labels, title_changed=title_changed):
            def run():
                args = ['issue', 'edit', str(live['number']), '--repo', REPO]
                if title_changed:
                    args += ['--title', q['title']]
                for lab in add_labels:    args += ['--add-label', lab]
                for lab in remove_labels: args += ['--remove-label', lab]
                gh(*args)
            return run
        add('hard', 'EDIT', msg, make_edit())
    if body_changed:
        msg = f'update body of issue #{live["number"]} ({q_id})'
        def make_body(live=live, q=q):
            def run():
                gh('issue', 'edit', str(live['number']), '--repo', REPO,
                   '--body-file', '-', body=q['body'])
            return run
        add('soft', 'BODY', msg, make_body())

# Orphan handling: issue with valid Q-ID but Q not in markdown anywhere
for q_id, live in live_by_qid.items():
    if q_id in open_qs or q_id in resolved_qids:
        continue
    if live['state'] == 'OPEN':
        msg = f'close issue #{live["number"]} ({q_id}) — Q-ID not in QUESTIONS.md'
        def make_close(live=live, q_id=q_id):
            def run():
                gh('issue', 'close', str(live['number']), '--repo', REPO,
                   '--comment', f'Removed from QUESTIONS.md (sync_issues.sh).')
            return run
        add('hard', 'CLOSE_ORPHAN', msg, make_close())

# Reverse sync: closed issue + still in open QUESTIONS.md → move to Resolved
to_resolve = []   # list of (q_id, issue_number, closed_at_str, title)
for q_id, live in live_by_qid.items():
    if live['state'] != 'CLOSED' or q_id not in open_qs:
        continue
    closed_at = live.get('closedAt') or ''
    closed_date = closed_at.split('T')[0] if closed_at else 'unknown'
    title = open_qs[q_id]['title']
    to_resolve.append((q_id, live['number'], closed_date, title))
    msg = (f'move {q_id} to ## Resolved in QUESTIONS.md '
           f'(issue #{live["number"]} closed {closed_date})')
    add('hard', 'RESOLVE', msg, None)   # markdown rewrite handled in batch below

# Reopened: closed issue but Q is under ## Resolved → manual reopen needed
for q_id, live in live_by_qid.items():
    if live['state'] == 'OPEN' and q_id in resolved_qids:
        msg = (f'manual: issue #{live["number"]} ({q_id}) reopened — please move it back '
               f'from ## Resolved to its open section in QUESTIONS.md')
        add('hard', 'REOPEN', msg, None)

# ─── Report (always) ──────────────────────────────────────────────
print(f'Repo:         {REPO}')
print(f'QUESTIONS.md: {QFILE}')
print(f'  open: {len(open_qs)}  resolved-pointers: {len(resolved_qids)}')
print(f'  issues fetched: {len(issues)}  with valid Q-IDs: {len(live_by_qid)}  '
      f'(open: {sum(1 for x in live_by_qid.values() if x["state"]=="OPEN")}, '
      f'closed: {sum(1 for x in live_by_qid.values() if x["state"]=="CLOSED")})')
print()

hard = [a for a in actions if a[0] == 'hard']
soft = [a for a in actions if a[0] == 'soft']

if not hard and not soft:
    print('OK — no drift.')

if hard:
    print(f'DRIFT — {len(hard)} hard action(s):')
    for sev, kind, msg, _ in hard:
        print(f'  [{kind}] {msg}')
    print()

if soft and not QUIET:
    print(f'WARN — {len(soft)} soft action(s) (body text differs):')
    for sev, kind, msg, _ in soft:
        print(f'  [{kind}] {msg}')
    print('Pass --quiet to suppress body-drift warnings.')
    print()

# ─── Apply ────────────────────────────────────────────────────────
def confirm(prompt):
    if YES:
        return True
    sys.stdout.write(prompt + ' [y/N] ')
    sys.stdout.flush()
    return sys.stdin.readline().strip().lower() == 'y'

if MODE == 'apply':
    if hard or soft:
        print('Applying...')

        destructive = [a for a in actions if a[1] == 'CLOSE_ORPHAN']
        if destructive:
            print(f'About to close {len(destructive)} orphan issue(s):')
            for _, _, m, _ in destructive:
                print(f'  - {m}')
            if not confirm('Proceed?'):
                print('aborted.')
                sys.exit(2)

        for sev, kind, msg, fn in actions:
            if fn is None:
                continue
            try:
                fn()
                print(f'  ok  [{kind}] {msg}')
            except Exception as e:
                print(f'  FAIL [{kind}] {msg}\n       {e}')
    else:
        print('No drift — regenerating public index only.')

    # Reverse-sync rewrite of QUESTIONS.md (move closed → Resolved)
    if to_resolve:
        new_md = md_text
        # Remove the open ##-blocks
        for q_id, _, _, _ in to_resolve:
            block = blocks.get(q_id)
            if block:
                new_md = new_md.replace(block, '', 1)
        # Append resolved entries
        # Find ## Resolved section, replace "_None yet._" or append
        m = re.search(r'(## Resolved\n.*?\n\n)(_None yet\._\n?)?(.*)$', new_md, re.S)
        if m:
            head = new_md[:m.end(1)]
            existing_resolved = '' if (m.group(2) or '').strip() else m.group(3) or ''
            new_entries = []
            for q_id, num, date, title in sorted(to_resolve):
                new_entries.append(
                    f'### {title}\n'
                    f'**Resolved {date}** via [#{num}]'
                    f'(https://github.com/{REPO}/issues/{num}). See `PRD.md §17.13`.\n'
                )
            new_md = head + '\n'.join(new_entries) + ('\n' + existing_resolved if existing_resolved else '\n')
        else:
            print('WARN: could not locate ## Resolved section; not rewriting QUESTIONS.md', file=sys.stderr)
            new_md = None

        if new_md:
            with open(QFILE, 'w') as f:
                f.write(new_md)
            print(f'  ok  rewrote {QFILE} (moved {len(to_resolve)} → ## Resolved)')
            print(f'  TODO: add resolution narrative to PRD.md §17.13 for: '
                  + ', '.join(q for q, _, _, _ in to_resolve))

# ─── Exit ─────────────────────────────────────────────────────────
if MODE == 'check' and hard:
    print('\nRun `./scripts/sync_issues.sh --apply` to reconcile.')
    sys.exit(1)
sys.exit(0)
PYEOF
