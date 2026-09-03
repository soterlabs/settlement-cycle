#!/usr/bin/env python
"""Generate the MSC settlement-summary forum post for one month.

    PYTHONPATH=src python scripts/build_forum_post.py --month 2026-08

Reads ONLY the committed settlement reports under ``settlements/`` — the
per-prime ``summary.md`` files and the consolidated ``sky_total`` one — plus
the prime configs for facts the reports don't carry (ilk name, whether the
subsidy applies). Editorial content (executor-agent grouping, MSC numbering,
Atlas citations, the standing narrative) lives in ``config/forum_post.yaml``.

WHY IT PARSES summary.md AND NOT provenance.json. ``provenance.json`` is
richer and full-precision, but it is gitignored — so it exists only on the
machine that last ran the pipeline. The post must be reproducible from a
fresh clone, which means the tracked artifacts are the only honest input.
Parsing is by ROW LABEL within a known section, never by position, and every
required row is mandatory: a renamed heading fails the run rather than
silently reporting a zero.

COMPLETENESS IS ENFORCED. Every prime in ``sky_total.yaml → accrual_primes``
must have a report for the month, AND the ``sky_total`` report must exist.
The latter is the real gate: sky_total can only be built once every prime
report and the non-MSC leg are in place, so its presence is proof the whole
cycle was generated rather than a subset.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

import yaml  # noqa: E402

from settle.domain.config import load_prime  # noqa: E402
from settle.domain.period import Month  # noqa: E402

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


class PostError(RuntimeError):
    """Anything that should stop the run with a readable message."""


# ── report parsing ────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Strip markdown emphasis and backticks from a cell."""
    return text.replace("**", "").replace("`", "").strip()


def _money(text: str) -> Decimal:
    raw = _clean(text).replace("$", "").replace(",", "").replace("USDS", "").strip()
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise PostError(f"not a number: {text!r}") from exc


def parse_rows(path: Path) -> list[tuple[tuple[str, ...], str, list[str]]]:
    """``[(heading_path, row_label, raw_value)]`` for every table row.

    The heading PATH (not just the nearest heading) is what disambiguates the
    two identically-labelled ``supply-side revenue`` rows: one lives under
    "Prime side" > "Supply-Side revenue", the other directly under "Sky side".
    Keying on the nearest heading alone silently picks the wrong one.
    """
    rows: list[tuple[tuple[str, ...], str, list[str]]] = []
    stack: list[tuple[int, str]] = []
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            title = line.lstrip("#").strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            continue
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        label = _clean(cells[0])
        if not label or set(label) <= set("-: "):
            continue                                    # separator row
        rows.append((tuple(t for _, t in stack), label.lower(), cells[1:]))
    return rows


def find(rows, label: str, *under: str, col: int = 0) -> str | None:
    """Raw value of ``label`` in the first row whose path contains ``under``
    in order. ``under`` may be a partial path — matching is by subsequence, so
    an added intermediate heading doesn't break the lookup."""
    for path, row_label, cells in rows:
        if row_label != label.lower() or col >= len(cells):
            continue
        it = iter(path)
        if all(any(want.lower() == seen.lower() for seen in it) for want in under):
            return cells[col]
    return None


def _require(rows, label: str, *under: str, path: Path) -> Decimal:
    value = find(rows, label, *under)
    if value is None:
        raise PostError(
            f"{path}: no '{label}' row under {' > '.join(under) or '(any)'} "
            "— report format changed?"
        )
    return _money(value)


# ── settlement-reports lookup ─────────────────────────────────────────────
#
# ``soterlabs/settlement-reports`` is not hand-maintained: this repo's
# ``publish-settlements`` workflow rsyncs ``settlements/`` into its
# ``reports/`` on every merge that touches them. So the commit to cite is
# derivable — and, because it is a mirror, CHECKABLE. That matters: the note
# claims every figure reproduces from that commit, and citing the latest
# commit without checking can assert something false (a settlements-touching
# PR merged since you generated, or a local tree ahead of / behind main).
#
# Network lives HERE and in ``main()`` only. ``build()`` stays offline and
# deterministic, so the generator still works from a clone with no network
# and the tests never reach for the wire.

_GH_API = "https://api.github.com/repos/{slug}/commits/{branch}"
_GH_RAW = "https://raw.githubusercontent.com/{slug}/{sha}/reports/{rel}"
_HTTP_TIMEOUT = 20


def _repo_slug(cfg: dict) -> str:
    return cfg["reports_repo"].rstrip("/").split("github.com/", 1)[-1]


def latest_published_commit(cfg: dict) -> str:
    """HEAD of the settlement-reports default branch."""
    import requests

    url = _GH_API.format(slug=_repo_slug(cfg), branch=cfg.get("reports_branch", "main"))
    r = requests.get(url, timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()["sha"]


def published_report_mismatches(
    sha: str, month: str, prime_ids: list[str], cfg: dict,
) -> list[str]:
    """Report paths whose published copy at ``sha`` differs from the local one.

    Empty list means the note's claim is TRUE for this month at this commit.
    """
    import requests

    slug = _repo_slug(cfg)
    bad: list[str] = []
    for pid in [*prime_ids, "sky_total"]:
        rel = f"{pid}/{month}/summary.md"
        local = (_REPO / "settlements" / rel).read_bytes()
        r = requests.get(_GH_RAW.format(slug=slug, sha=sha, rel=rel), timeout=_HTTP_TIMEOUT)
        if r.status_code == 404:
            bad.append(f"{rel} (absent from the published tree)")
            continue
        r.raise_for_status()
        if r.content != local:
            bad.append(rel)
    return bad


# ── model ─────────────────────────────────────────────────────────────────

@dataclass
class PrimeFigures:
    prime_id: str
    label: str
    ilk: str | None
    demand_total: Decimal
    demand_components: list[str]          # human labels of the ACTIVE ones
    prime_share: Decimal
    sky_share: Decimal
    sky_direct_exposure: Decimal
    subsidised: bool
    subproxy: str                         # checksummed 0x… from the prime config
    mint: Decimal
    send: Decimal
    adjustments: dict[str, Decimal] = field(default_factory=dict)

    @property
    def has_supply_side(self) -> bool:
        return self.ilk is not None


# Order matters — it is the order the published posts use (MSC#11 t/28151):
# Distribution Rewards, then Agent Rate, then Chronicle Points, then GAR.
_DEMAND_LABELS = {
    "distribution rewards": "Distribution Rewards: Active referral codes.",
    "agent rate": "Agent Rate earned on Subproxy treasury holdings.",
    "chronicle points": "Chronicle points.",
}


def read_prime(prime_id: str, month: str, cfg: dict) -> PrimeFigures:
    path = _REPO / "settlements" / prime_id / month / "summary.md"
    rows = parse_rows(path)

    demand_total = _require(rows, "demand-side revenue", "Demand-Side revenue", path=path)
    components: list[str] = []
    for key, text in _DEMAND_LABELS.items():
        raw = find(rows, key, "Demand-Side revenue")
        if raw is not None and _money(raw) != 0:
            components.append(text)
    # GAR was retired from 2026-08; older months still carry the row.
    _gar = find(rows, "governance accessibility rewards", "Demand-Side revenue")
    if _gar is not None and _money(_gar) != 0:
        components.append(
            "Governance Accessibility Rewards - "
            + _atlas(cfg, "governance_accessibility_rewards")
        )
    if not components:
        raise PostError(
            f"{path}: demand side totals {demand_total} but no component row is "
            "non-zero — the post would list a total with no primitives."
        )

    prime = load_prime(_REPO / "config" / f"{prime_id}.yaml")
    from settle.domain.primes import Chain
    subproxy = prime.subproxy.get(Chain.ETHEREUM)
    if subproxy is None:
        raise PostError(
            f"{prime_id}: no ethereum subproxy in config — the post has to "
            "name the address the funds are sent to."
        )
    ilk = (
        bytes(prime.ilk_bytes32).rstrip(b"\x00").decode()
        if prime.ilk_bytes32 else None
    )
    return PrimeFigures(
        prime_id=prime_id,
        label=prime_id.capitalize(),
        ilk=ilk,
        demand_total=demand_total,
        demand_components=components,
        prime_share=_require(
            rows, "supply-side revenue", "Prime side", "Supply-Side revenue", path=path,
        ),
        sky_share=_require(rows, "supply-side revenue", "Sky side", path=path),
        sky_direct_exposure=_require(
            rows, "sky direct exposure", "Sky side", path=path,
        ),
        subsidised=bool(getattr(prime.subsidy, "enabled", False)),
        subproxy=checksum_address(subproxy.hex),
        mint=Decimal(0),
        send=Decimal(0),
    )


@dataclass
class SkyTotal:
    mint_by_prime: dict[str, Decimal]
    send_by_prime: dict[str, Decimal]
    msc_net: Decimal
    non_msc_net: Decimal
    snr: Decimal
    pinned: bool                          # False => mint/send are derived


def read_sky_total(month: str) -> SkyTotal:
    path = _REPO / "settlements" / "sky_total" / month / "summary.md"
    text = path.read_text()
    rows = parse_rows(path)

    # Per-prime mint/send, scoped to the MSC-leg section. Scoping matters: a
    # loose scan of the whole file would also pick up any future 3-column
    # table, and an unbolded "total" row would arrive as a phantom prime.
    leg = [r for r in rows if any(t.lower().startswith("msc leg") for t in r[0])]
    if not leg:
        raise PostError(f"{path}: no 'MSC leg' section — report format changed?")

    mints: dict[str, Decimal] = {}
    sends: dict[str, Decimal] = {}
    for _, label, cells in leg:
        if label in ("prime", "total", "msc net (accrual)") or len(cells) < 2:
            continue
        mints[label] = _money(cells[0])
        sends[label] = abs(_money(cells[1]))
    if not mints:
        raise PostError(f"{path}: 'MSC leg' section has no per-prime rows")

    total = SkyTotal(
        mint_by_prime=mints,
        send_by_prime=sends,
        msc_net=_require(rows, "msc net (accrual)", "Sky Net Revenue", path=path),
        non_msc_net=_require(rows, "non-msc net", "Sky Net Revenue", path=path),
        snr=_require(rows, "sky net revenue", "Sky Net Revenue", path=path),
        # The report prints this warning when config/sky_total.yaml carries no
        # msc_preview entry, i.e. nothing is pinned to a published MSC post.
        pinned="msc_preview: no entry" not in text,
    )

    # Cross-check the rows we parsed against the report's own totals. Catches
    # a mis-parse — a dropped prime row, a swapped column — before it reaches
    # a counterparty-facing post, where it would look authoritative.
    derived = sum(mints.values()) - sum(sends.values())
    if abs(derived - total.msc_net) > Decimal("1"):
        raise PostError(
            f"{path}: parsed per-prime rows sum to {derived} but the report "
            f"states MSC net {total.msc_net} — parse is wrong, or a prime row "
            "was missed."
        )
    if abs(total.msc_net + total.non_msc_net - total.snr) > Decimal("0.01"):
        raise PostError(
            f"{path}: MSC net {total.msc_net} + non-MSC {total.non_msc_net} "
            f"!= SNR {total.snr}"
        )
    return total


# ── rendering ─────────────────────────────────────────────────────────────

def checksum_address(addr: str) -> str:
    """EIP-55 mixed-case checksum of a hex address.

    ``domain.primes.Address`` normalises to lowercase, but the published posts
    quote the checksummed form — which is what operators eyeball against a
    block explorer, and the only form that detects a mistyped character.
    """
    from settle.extract._keccak import keccak256

    body = addr[2:] if addr.lower().startswith("0x") else addr
    body = body.lower()
    digest = keccak256(body.encode()).hex()
    return "0x" + "".join(
        c.upper() if c.isalpha() and int(digest[i], 16) >= 8 else c
        for i, c in enumerate(body)
    )


def _atlas(cfg: dict, key: str) -> str:
    """``[A.x.y](url)`` — the citation as a link, as the published posts do."""
    entry = cfg["atlas"][key]
    return f"[{entry['ref']}]({entry['url']})"


def _usds(v: Decimal) -> str:
    whole = v.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{whole:,}"


_ADJ_LABELS = {
    "dv_adj": "Demand-side correction",
    "sv_adj": "Supply-side, {label} Share correction",
    "sky_adj": "Supply-side, Sky Share correction",
    "send_credit": "Send credit",
}


def reproducibility_note(month: str, cfg: dict, override: str | None = None) -> str | None:
    """The second methodology note, or None when no commit is pinned.

    Returning None rather than a note with a stale hash is deliberate: the
    note is a REPRODUCIBILITY CLAIM, and one pointing at the wrong commit is
    worse than no claim at all.
    """
    commit = override or (cfg.get("report_commits") or {}).get(month)
    if not commit:
        return None
    return cfg["reproducibility_note"].format(
        repo=cfg["reports_repo"], commit=commit, short=commit[:7],
    ).strip()


def render(
    month: str, primes: list[PrimeFigures], total: SkyTotal, cfg: dict,
    reports_commit: str | None = None,
) -> str:
    m = Month.parse(month)
    month_name = f"{_MONTH_NAMES[m.month - 1]} {m.year}"

    anchor = Month.parse(str(cfg["msc_anchor"]["month"]))
    number = int(cfg["msc_anchor"]["number"]) + (
        (m.year - anchor.year) * 12 + (m.month - anchor.month)
    )

    by_id = {p.prime_id: p for p in primes}
    L: list[str] = [f"# MSC #{number} - Settlement Summary ({month_name})", ""]
    L += ["## Methodology", "", cfg["methodology"].strip(), ""]
    note = reproducibility_note(month, cfg, reports_commit)
    if note:
        L += [note, ""]
    L += ["---", ""]

    for group in cfg["executor_agents"]:
        L += [f"## {group['name']}", ""]
        if group.get("foreword"):
            L += [group["foreword"].strip(), ""]
        for pid in group["primes"]:
            p = by_id[pid]
            L += [f"### {p.label} Settlement for {month_name}", ""]

            L += ["#### Demand Side Primitives", ""]
            L += [f"- {c}" for c in p.demand_components]
            L += ["", f"**Demand Side Total**: {_usds(p.demand_total)} USDS", ""]

            L += ["#### Supply Side Primitives", ""]
            if p.has_supply_side:
                L.append("- Allocation System Primitive")
                if p.subsidised:
                    L.append(
                        "- Subsidized borrow rate as defined in "
                        + _atlas(cfg, "subsidised_borrow_rate")
                    )
                if p.sky_direct_exposure != 0:
                    L.append(
                        "- Sky Direct Exposure reimbursements as defined in "
                        + _atlas(cfg, "sky_direct_exposure")
                    )
                L += ["", "**Supply Side Total**", ""]
                L += [
                    f"- **{p.label} Share:** {_usds(p.prime_share)} USDS",
                    f"- **Sky Share:** {_usds(p.sky_share)} USDS",
                    "",
                ]
            else:
                L += ["- No active Supply side primitive instances.", ""]

            if p.adjustments:
                L += ["#### Adjustments", ""]
                for key, amount in p.adjustments.items():
                    label = _ADJ_LABELS.get(key, key).format(label=p.label)
                    L.append(f"- {label}: {_usds(amount)} USDS")
                L.append("")

            L += [f"#### {p.label} Settlement", ""]
            if p.mint > 0:
                L.append(
                    f"- Mint `{_usds(p.mint)} USDS` debt in `{p.ilk}` and "
                    "transfer to surplus buffer."
                )
            link = f"[{p.subproxy}]({cfg['etherscan_address_url']}{p.subproxy})"
            L.append(
                f"- Send `{_usds(p.send)} USDS` from surplus buffer to the "
                f"{p.label} Subproxy {link}."
            )
            L += ["", "---", ""]

    L += ["## Sky Treasury Management Function Calculations", ""]
    L += [
        cfg["treasury_note"]
        .strip()
        .format(month_name=month_name.split()[0], snr=_usds(total.snr)),
        "",
    ]
    return "\n".join(L).rstrip() + "\n"


# ── orchestration ─────────────────────────────────────────────────────────

def require_all_reports(month: str, prime_ids: list[str]) -> None:
    """Fail unless every prime's report AND the sky_total report exist.

    sky_total is the meaningful one: it can only be built once every prime
    report and the non-MSC leg are present, so its existence is evidence the
    whole cycle was generated. Missing paths are reported together — one run
    tells the operator everything to regenerate.
    """
    missing = [
        str(p.relative_to(_REPO))
        for p in (
            _REPO / "settlements" / pid / month / "summary.md"
            for pid in [*prime_ids, "sky_total"]
        )
        if not p.exists()
    ]
    if missing:
        raise PostError(
            f"{month}: {len(missing)} report(s) missing — the post would be "
            "incomplete:\n  " + "\n  ".join(missing)
            + "\n\nGenerate them first (scripts/run_<prime>_2026.py, then "
            "scripts/build_sky_total_2026.py)."
        )


def build(month: str, reports_commit: str | None = None) -> tuple[str, SkyTotal]:
    cfg = yaml.safe_load((_REPO / "config" / "forum_post.yaml").read_text())
    sky_cfg = yaml.safe_load((_REPO / "config" / "sky_total.yaml").read_text())
    prime_ids = list(sky_cfg["accrual_primes"])

    grouped = [pid for g in cfg["executor_agents"] for pid in g["primes"]]
    if sorted(grouped) != sorted(prime_ids):
        raise PostError(
            "config/forum_post.yaml executor_agents must list every prime in "
            "sky_total.yaml accrual_primes exactly once.\n"
            f"  accrual_primes: {sorted(prime_ids)}\n"
            f"  grouped:        {sorted(grouped)}"
        )

    require_all_reports(month, prime_ids)

    total = read_sky_total(month)
    primes = []
    for pid in prime_ids:
        p = read_prime(pid, month, cfg)
        if pid not in total.mint_by_prime:
            raise PostError(
                f"sky_total {month} has no MSC-leg row for {pid} — the post "
                "cannot state its settlement instruction."
            )
        p.mint = total.mint_by_prime[pid]
        p.send = total.send_by_prime[pid]
        adj = ((sky_cfg.get("msc_preview") or {}).get(month) or {}).get(pid) or {}
        p.adjustments = {
            k: Decimal(str(v))
            for k, v in adj.items()
            if k in _ADJ_LABELS and Decimal(str(v)) != 0
        }
        primes.append(p)

    return render(month, primes, total, cfg, reports_commit), total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--month", required=True, help="settlement month, YYYY-MM")
    ap.add_argument(
        "--reports-commit",
        help="settlement-reports commit to cite. Default: the month's pin in "
             "config/forum_post.yaml report_commits, else the current HEAD of "
             "the settlement-reports default branch.",
    )
    ap.add_argument(
        "--skip-verify",
        action="store_true",
        help="cite the commit without checking the published reports match "
             "(offline drafting only — the note becomes an unchecked claim)",
    )
    ap.add_argument(
        "--out",
        help="output path (default: forum_posts/msc-<month>.md); '-' for stdout",
    )
    args = ap.parse_args(argv)

    try:
        month = str(Month.parse(args.month))
    except Exception:
        print(f"error: --month {args.month!r} is not a YYYY-MM month",
              file=sys.stderr)
        return 1
    cfg = yaml.safe_load((_REPO / "config" / "forum_post.yaml").read_text())
    sky_cfg = yaml.safe_load((_REPO / "config" / "sky_total.yaml").read_text())

    # Resolve the commit to cite: explicit flag, then the month's pin (so a
    # already-published post keeps citing ITS commit rather than silently
    # re-pointing at a newer tree), then the published HEAD.
    commit = args.reports_commit or (cfg.get("report_commits") or {}).get(month)
    source = "--reports-commit" if args.reports_commit else (
        "config report_commits" if commit else "")
    if not commit:
        try:
            commit = latest_published_commit(cfg)
            source = f"{cfg['reports_repo']} HEAD"
        except Exception as exc:
            print(
                f"note: could not reach {cfg['reports_repo']} ({exc}) and no "
                f"commit is pinned for {month} — the reproducibility note is "
                "omitted. Pin one under config/forum_post.yaml "
                "report_commits, or pass --reports-commit.",
                file=sys.stderr,
            )

    # Verify the claim rather than assert it: the published reports at that
    # commit must be byte-identical to the ones this post was built from.
    if commit and not args.skip_verify:
        try:
            bad = published_report_mismatches(
                commit, month, list(sky_cfg["accrual_primes"]), cfg,
            )
        except Exception as exc:
            print(
                f"warning: could not verify the published reports at "
                f"{commit[:7]} ({exc}) — citing it unchecked. Re-run with "
                "network, or pass --skip-verify to silence this.",
                file=sys.stderr,
            )
        else:
            if bad:
                print(
                    f"error: {commit[:7]} does not publish the reports this "
                    f"post was built from — the reproducibility note would be "
                    f"false. Differing:\n  " + "\n  ".join(bad)
                    + "\n\nPublish the current settlements/ first (merge to "
                    "main), or cite the right commit with --reports-commit.",
                    file=sys.stderr,
                )
                return 1
            print(
                f"verified: {len(list(sky_cfg['accrual_primes'])) + 1} reports "
                f"at {commit[:7]} ({source}) match settlements/ exactly",
                file=sys.stderr,
            )

    try:
        post, total = build(month, commit)
    except PostError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not total.pinned:
        # Expected while DRAFTING: msc_preview is filled in from the post once
        # the cycle executes, so a month being unpinned is the normal state
        # for the post you are about to publish. Said out loud only because
        # two things follow from it — the figures are derived, and any
        # prior-cycle corrections riding this settlement aren't in yet.
        print(
            f"note: config/sky_total.yaml has no msc_preview entry for {month} "
            "(expected while drafting). Every mint/send is DERIVED from the "
            "monthly reports, and no Adjustments sections are emitted — add "
            "any prior-cycle corrections riding this settlement, then pin the "
            "published figures once the cycle executes.",
            file=sys.stderr,
        )

    if args.out == "-":
        print(post, end="")
        return 0
    out = Path(args.out) if args.out else _REPO / "forum_posts" / f"msc-{month}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(post)
    print(f"wrote {out.relative_to(_REPO) if out.is_relative_to(_REPO) else out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
