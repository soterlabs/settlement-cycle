"""``scripts/build_forum_post.py`` — MSC forum post generation.

The post is counterparty-facing, so the properties worth pinning are the ones
where a silent wrong answer is worse than a crash:

* **Completeness.** Every settling prime plus ``sky_total`` must have a report
  for the month. A post assembled from a subset would look complete.
* **Row disambiguation.** ``supply-side revenue`` appears TWICE in a prime
  report — once as the prime's share, once as Sky's. Keying on the nearest
  heading picks the wrong one; the parser keys on the heading PATH.
* **Active primitives only.** A primitive is listed only when its component is
  non-zero, so Keel's retired DR and Skybase's retired GAR drop out on their
  own rather than needing the script edited.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

# ``scripts/`` isn't a package, so load the file directly. It must be
# registered in ``sys.modules`` BEFORE exec: @dataclass resolves its own
# module to evaluate annotations and blows up on a module that isn't there.
_spec = importlib.util.spec_from_file_location(
    "build_forum_post", _REPO / "scripts" / "build_forum_post.py"
)
bfp = importlib.util.module_from_spec(_spec)
sys.modules["build_forum_post"] = bfp
_spec.loader.exec_module(bfp)


# --- completeness gate ----------------------------------------------------

def test_missing_reports_are_reported_together(tmp_path, monkeypatch):
    """One run must name everything to regenerate, not fail one at a time."""
    monkeypatch.setattr(bfp, "_REPO", tmp_path)
    with pytest.raises(bfp.PostError) as exc:
        bfp.require_all_reports("2026-08", ["spark", "grove"])
    msg = str(exc.value)
    assert "spark" in msg and "grove" in msg and "sky_total" in msg
    assert "3 report(s) missing" in msg


def test_sky_total_alone_missing_still_fails(tmp_path, monkeypatch):
    """sky_total is the real gate: it only builds once everything else has,
    so its absence means the cycle was not fully generated."""
    monkeypatch.setattr(bfp, "_REPO", tmp_path)
    for pid in ("spark",):
        d = tmp_path / "settlements" / pid / "2026-08"
        d.mkdir(parents=True)
        (d / "summary.md").write_text("# x\n")
    with pytest.raises(bfp.PostError, match="sky_total"):
        bfp.require_all_reports("2026-08", ["spark"])


def test_passes_when_everything_is_present():
    bfp.require_all_reports("2026-08", ["spark", "grove", "obex", "osero",
                                        "keel", "skybase"])


# --- parsing --------------------------------------------------------------

_SUMMARY = """# SPARK — 2026-08

## Headline

### Prime side

#### Demand-Side revenue

| Field | USDS |
|---|---:|
| agent rate | 145,038.81 |
| distribution rewards | 694,641.15 |
| **demand-side revenue** | **839,679.96** |

#### Supply-Side revenue

| Field | USDS |
|---|---:|
| **supply-side revenue** | **97,755.79** |

### Sky side

| Field | USDS |
|---|---:|
| prime cost of funds | 6,267,981.96 |
| sky direct exposure | -7,825.94 |
| **supply-side revenue** | **6,260,156.02** |
"""


def test_the_two_supply_side_rows_are_distinguished(tmp_path):
    """The bug this parser exists to avoid: both rows are labelled
    ``supply-side revenue``, and confusing them would swap the prime's share
    with Sky's — a 64x error on this month's Spark figures."""
    f = tmp_path / "summary.md"
    f.write_text(_SUMMARY)
    rows = bfp.parse_rows(f)
    prime = bfp.find(rows, "supply-side revenue", "Prime side", "Supply-Side revenue")
    sky = bfp.find(rows, "supply-side revenue", "Sky side")
    assert bfp._money(prime) == Decimal("97755.79")
    assert bfp._money(sky) == Decimal("6260156.02")


def test_a_renamed_row_fails_loudly(tmp_path):
    f = tmp_path / "summary.md"
    f.write_text(_SUMMARY.replace("demand-side revenue", "demand side total"))
    rows = bfp.parse_rows(f)
    with pytest.raises(bfp.PostError, match="report format changed"):
        bfp._require(rows, "demand-side revenue", "Demand-Side revenue", path=f)


def test_intermediate_heading_does_not_break_lookup(tmp_path):
    """Path matching is by subsequence, so an added sub-heading is tolerated
    — otherwise every report tweak would break the generator."""
    f = tmp_path / "summary.md"
    f.write_text(_SUMMARY.replace(
        "#### Supply-Side revenue", "#### Supply-Side revenue\n\n##### Detail"))
    rows = bfp.parse_rows(f)
    assert bfp.find(rows, "supply-side revenue", "Prime side") is not None


# --- rendering against the real August reports ----------------------------

def section(post: str, prime: str, month_name: str = "August 2026") -> str:
    """The whole block for one prime, heading through the trailing rule.

    Not ``post.split(f"### {prime} Settlement")`` — that substring ALSO matches
    the ``#### {prime} Settlement`` instruction sub-heading, so the slice stops
    short of the Mint/Send lines and any assertion about them passes
    vacuously. Anchor on the full heading and cut at the next horizontal rule.
    """
    head = f"### {prime} Settlement for {month_name}"
    assert head in post, f"no section for {prime}"
    body = post.split(head, 1)[1]
    return body.split("\n---", 1)[0]


@pytest.fixture(scope="module")
def august():
    post, total = bfp.build("2026-08")
    return post, total


def test_title_and_msc_number(august):
    post, _ = august
    assert post.startswith("# MSC #12 - Settlement Summary (August 2026)")


def test_msc_number_counts_from_the_anchor():
    import yaml
    cfg = yaml.safe_load((_REPO / "config" / "forum_post.yaml").read_text())
    assert cfg["msc_anchor"] == {"month": "2026-07", "number": 11}


def test_every_settling_prime_has_a_section(august):
    post, _ = august
    for prime in ("Spark", "Grove", "Keel", "Obex", "Skybase", "Osero"):
        assert f"### {prime} Settlement for August 2026" in post


def test_retired_primitives_drop_out_on_their_own(august):
    """Keel's DR and Skybase's GAR were retired from 2026-08. Neither the
    script nor its config names them — the bullets vanish because the
    component is zero."""
    post, _ = august
    keel = section(post, 'Keel')
    assert "Agent Rate" in keel
    assert "Distribution Rewards" not in keel, "Keel's DR is $0 from 2026-08"
    skybase = section(post, 'Skybase')
    assert "Distribution Rewards" in skybase
    assert "Governance Accessibility" not in skybase, "GAR retired from 2026-08"


def test_primes_without_an_ilk_state_no_supply_side_and_never_mint(august):
    post, _ = august
    for prime in ("Keel", "Skybase"):
        block = section(post, prime)
        assert "No active Supply side primitive instances." in block
        assert "Mint `" not in block, f"{prime} has no ilk and must not mint"
        assert "Send `" in block


def test_negative_supply_share_renders_and_does_not_mint_more(august):
    """Osero's August share is negative; it nets inside the send and the mint
    is Sky's share alone (7,006), not reduced further."""
    post, _ = august
    block = section(post, 'Osero')
    assert "**Osero Share:** -1,448 USDS" in block
    assert "Mint `7,006 USDS`" in block
    assert "Send `30,156 USDS`" in block


def test_subsidy_and_sde_bullets_only_where_they_apply(august):
    post, _ = august
    for prime in ("Spark", "Grove"):
        block = section(post, prime)
        assert "Subsidized borrow rate" in block
        assert "Sky Direct Exposure reimbursements" in block
    for prime in ("Obex", "Osero"):
        block = section(post, prime)
        assert "Allocation System Primitive" in block
        assert "Subsidized borrow rate" not in block
        assert "Sky Direct Exposure" not in block


def test_snr_matches_the_sky_total_report(august):
    post, total = august
    assert total.snr == Decimal("15745296.07")
    assert "Sky's August net revenue was `15,745,296 USDS`" in post


def test_august_is_not_pinned_to_a_published_post(august):
    """2026-08 has no msc_preview entry, so mint/send are derived. The caller
    warns on this; the flag is what it keys off."""
    _, total = august
    assert total.pinned is False


def test_settlement_instructions_match_sky_total(august):
    post, total = august
    assert total.mint_by_prime["grove"] == Decimal("9574714.00")
    assert total.send_by_prime["grove"] == Decimal("1342064.00")
    assert "Mint `9,574,714 USDS` debt in `ALLOCATOR-BLOOM-A`" in post
    assert "Send `1,342,064 USDS` from surplus buffer to Grove Subproxy." in post


# --- config consistency ---------------------------------------------------

def test_executor_grouping_must_cover_every_settling_prime(monkeypatch, tmp_path):
    """Onboarding a prime without adding it to a group must fail, not quietly
    leave it out of the post."""
    import yaml
    cfg = yaml.safe_load((_REPO / "config" / "forum_post.yaml").read_text())
    cfg["executor_agents"][0]["primes"] = ["spark"]        # drop grove + keel
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "forum_post.yaml").write_text(yaml.safe_dump(cfg))
    (tmp_path / "config" / "sky_total.yaml").write_text(
        (_REPO / "config" / "sky_total.yaml").read_text()
    )
    monkeypatch.setattr(bfp, "_REPO", tmp_path)
    with pytest.raises(bfp.PostError, match="exactly once"):
        bfp.build("2026-08")


def test_july_reproduces_the_published_adjustments():
    """MSC#11 (t/28151) is the reference. Its per-prime ADJUSTMENTS come from
    config, so they must reproduce exactly; the base figures do not, because
    Spark's July report was re-priced after publication (see #187)."""
    post, _ = bfp.build("2026-07")
    assert post.startswith("# MSC #11 - Settlement Summary (July 2026)")
    spark = section(post, "Spark", "July 2026")
    assert "Demand-side correction: -41,908 USDS" in spark
    assert "Supply-side, Spark Share correction: 563,527 USDS" in spark
    assert "Supply-side, Sky Share correction: 304,476 USDS" in spark
