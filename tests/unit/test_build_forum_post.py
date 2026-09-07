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
    assert "Send `1,342,064 USDS` from surplus buffer to the Grove Subproxy" in post


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


# --- cross-checks against the report's own totals -------------------------

@pytest.fixture
def repo_copy(tmp_path, monkeypatch):
    """A writable copy of settlements/ + config/ so a report can be corrupted."""
    import shutil
    shutil.copytree(_REPO / "settlements", tmp_path / "settlements")
    shutil.copytree(_REPO / "config", tmp_path / "config")
    monkeypatch.setattr(bfp, "_REPO", tmp_path)
    return tmp_path


def test_a_dropped_prime_row_is_caught(repo_copy):
    """The failure that would otherwise be invisible: one prime silently
    missing from the MSC leg still renders a plausible-looking post."""
    f = repo_copy / "settlements" / "sky_total" / "2026-08" / "summary.md"
    f.write_text("\n".join(
        ln for ln in f.read_text().splitlines() if not ln.startswith("| grove |")
    ))
    with pytest.raises(bfp.PostError, match="a prime row was missed"):
        bfp.read_sky_total("2026-08")


def test_swapped_mint_send_columns_are_caught(repo_copy):
    f = repo_copy / "settlements" / "sky_total" / "2026-08" / "summary.md"
    f.write_text(f.read_text().replace(
        "| grove | 9,574,714.00 | -1,342,064.00 |",
        "| grove | -1,342,064.00 | 9,574,714.00 |",
    ))
    with pytest.raises(bfp.PostError, match="parse is wrong"):
        bfp.read_sky_total("2026-08")


def test_snr_identity_is_checked(repo_copy):
    f = repo_copy / "settlements" / "sky_total" / "2026-08" / "summary.md"
    f.write_text(f.read_text().replace(
        "| **Sky Net Revenue** | **15,745,296.07** |",
        "| **Sky Net Revenue** | **99,999,999.00** |",
    ))
    with pytest.raises(bfp.PostError, match="!= SNR"):
        bfp.read_sky_total("2026-08")


def test_the_total_row_is_not_mistaken_for_a_prime():
    """`total` and `MSC net (accrual)` live in the same table as the primes."""
    total = bfp.read_sky_total("2026-08")
    assert set(total.mint_by_prime) == {
        "spark", "grove", "obex", "osero", "keel", "skybase",
    }


def test_bad_month_exits_cleanly():
    assert bfp.main(["--month", "whenever"]) == 1


# --- foreword, Atlas links, subproxy links --------------------------------

def test_each_executor_agent_carries_its_foreword(august):
    post, _ = august
    amatsu = post.split("## Amatsu Operational Executor Agent", 1)[1].split("###", 1)[0]
    assert "on behalf of the Amatsu OEA" in amatsu
    assert "Spark, Grove and Keel" in amatsu
    ozone = post.split("## Ozone Operational Executor Agent", 1)[1].split("###", 1)[0]
    assert "on behalf of the Ozone OEA" in ozone
    assert "Obex, Skybase and Osero" in ozone


def test_atlas_citations_are_links(august):
    post, _ = august
    assert (
        "[A.2.8.2.2.2.2.1](https://sky-atlas.io/"
        "#552e7b01-c2d0-4658-ac49-2c74e230aeac)" in post
    )
    assert (
        "[A.2.2.9.1.1.1.1.2.0.6.1](https://sky-atlas.io/"
        "#5f368e33-7a82-4244-a9ba-f285193ec043)" in post
    )
    # every citation rendered must be a link, never a bare reference
    import re
    assert not re.search(r"\[A\.[\d.]+\](?!\()", post)


def test_every_send_names_a_linked_subproxy(august):
    post, _ = august
    for prime in ("Spark", "Grove", "Keel", "Obex", "Skybase", "Osero"):
        block = section(post, prime)
        assert "https://etherscan.io/address/0x" in block, prime


def test_subproxy_addresses_come_from_the_prime_configs(august):
    """The post must cite the address the PIPELINE settles to, so it is read
    from each prime's own config rather than restated in the post config."""
    import yaml
    post, _ = august
    known = yaml.safe_load((_REPO / "config" / "sky_total.yaml").read_text())
    for prime_id, addr in known["subproxies"].items():
        block = section(post, prime_id.capitalize())
        assert addr in block, f"{prime_id}: expected {addr}"
        assert f"https://etherscan.io/address/{addr}" in block


def test_addresses_are_eip55_checksummed(august):
    """Lowercase would still resolve on Etherscan but loses the typo
    detection that makes a checksummed address worth eyeballing."""
    post, _ = august
    assert bfp.checksum_address(
        "0x3300f198988e4c9c63f75df86de36421f06af8c4"
    ) == "0x3300f198988e4C9C63F75dF86De36421f06af8c4"
    assert "0x3300f198988e4C9C63F75dF86De36421f06af8c4" in post


def test_checksum_matches_every_configured_address():
    """The configs are written checksummed; recomputing must agree, or the
    post and the config would disagree about the same address."""
    import yaml
    known = yaml.safe_load((_REPO / "config" / "sky_total.yaml").read_text())
    for prime_id, addr in known["subproxies"].items():
        assert bfp.checksum_address(addr.lower()) == addr, prime_id


# --- reproducibility note -------------------------------------------------

def test_methodology_carries_two_numbered_italic_notes(august):
    post, _ = august
    methodology = post.split("## Methodology", 1)[1].split("\n---", 1)[0]
    notes = [ln for ln in methodology.splitlines() if ln.startswith("*Note ")]
    assert len(notes) == 2, "expected Note 1 and Note 2"
    assert notes[0].startswith("*Note 1: Potential inaccuracies")
    assert notes[1].startswith("*Note 2: every figure")
    assert "soterlabs/settlement-reports" in notes[1]
    assert notes[1].endswith("*"), "must stay italic"


def test_note_links_the_repo_and_the_pinned_commit(august):
    post, _ = august
    sha = "f3a74bddf2614c8cf0c82183a19e2dd13c66ddc6"
    assert f"https://github.com/soterlabs/settlement-reports/commit/{sha}" in post
    assert "[`f3a74bd`]" in post, "short sha shown, full sha linked"


def test_note_is_omitted_when_no_commit_is_pinned():
    """A reproducibility claim against the WRONG commit is worse than none, so
    an unpinned month emits nothing rather than reusing a stale hash."""
    import yaml
    cfg = yaml.safe_load((_REPO / "config" / "forum_post.yaml").read_text())
    assert bfp.reproducibility_note("2026-09", cfg) is None
    assert bfp.reproducibility_note("2026-08", cfg) is not None


def test_commit_can_be_overridden_for_drafting():
    import yaml
    cfg = yaml.safe_load((_REPO / "config" / "forum_post.yaml").read_text())
    note = bfp.reproducibility_note("2026-09", cfg, "0123456789abcdef")
    assert note is not None
    assert "[`0123456`]" in note and "commit/0123456789abcdef" in note


def test_commits_are_recorded_per_month():
    """Keyed by month so last cycle's hash can't silently ride this cycle's
    post — the failure this design exists to prevent."""
    import yaml
    cfg = yaml.safe_load((_REPO / "config" / "forum_post.yaml").read_text())
    assert set(cfg["report_commits"]) == {"2026-08"}


# --- resolving and VERIFYING the settlement-reports commit ----------------
#
# ``settlement-reports`` is an rsync mirror of this repo's ``settlements/``
# (see .github/workflows/publish-settlements.yml), so the commit to cite is
# both derivable and checkable. Network lives in ``main()`` and the two
# helpers below, which these tests patch — the suite never hits the wire.

def test_repo_slug_is_derived_from_the_configured_url():
    import yaml
    cfg = yaml.safe_load((_REPO / "config" / "forum_post.yaml").read_text())
    assert bfp._repo_slug(cfg) == "soterlabs/settlement-reports"


def _stub(monkeypatch, tmp_path, *, head="deadbeef" * 5, mismatches=None):
    monkeypatch.setattr(bfp, "latest_published_commit", lambda cfg: head)
    monkeypatch.setattr(
        bfp, "published_report_mismatches",
        lambda sha, month, prime_ids, cfg: list(mismatches or []),
    )
    return tmp_path / "out.md"


def test_pinned_commit_wins_over_published_head(monkeypatch, tmp_path, capsys):
    """An already-published post must keep citing ITS commit rather than
    silently re-pointing at a newer tree."""
    out = _stub(monkeypatch, tmp_path)
    assert bfp.main(["--month", "2026-08", "--out", str(out)]) == 0
    assert "f3a74bddf2614c8cf0c82183a19e2dd13c66ddc6" in out.read_text()
    assert "deadbeef" not in out.read_text()


def test_unpinned_month_falls_back_to_published_head(monkeypatch, tmp_path):
    """2026-07 has no `report_commits` entry, so it must resolve the published
    HEAD rather than emit no note."""
    out = _stub(monkeypatch, tmp_path)
    assert bfp.main(["--month", "2026-07", "--out", str(out)]) == 0
    assert ("deadbeef" * 5) in out.read_text()


def test_explicit_flag_wins_over_everything(monkeypatch, tmp_path):
    out = _stub(monkeypatch, tmp_path)
    rc = bfp.main([
        "--month", "2026-08", "--out", str(out), "--reports-commit", "abc123def456",
    ])
    assert rc == 0
    assert "abc123def456" in out.read_text()


def test_a_commit_that_does_not_publish_these_reports_is_refused(
    monkeypatch, tmp_path, capsys,
):
    """The whole point: the note is a CLAIM, so a commit whose published
    reports differ must fail the run rather than be cited."""
    out = _stub(monkeypatch, tmp_path, mismatches=["keel/2026-08/summary.md"])
    assert bfp.main(["--month", "2026-08", "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert "would be false" in err
    assert "keel/2026-08/summary.md" in err
    assert not out.exists(), "no post should be written on a false claim"


def test_skip_verify_bypasses_the_check(monkeypatch, tmp_path):
    out = _stub(monkeypatch, tmp_path, mismatches=["keel/2026-08/summary.md"])
    assert bfp.main([
        "--month", "2026-08", "--out", str(out), "--skip-verify",
    ]) == 0
    assert out.exists()


def test_unreachable_repo_still_produces_a_post(monkeypatch, tmp_path, capsys):
    """Offline must degrade, not break: the pinned commit is cited unverified
    with a warning, rather than failing the run."""
    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(bfp, "latest_published_commit", boom)
    monkeypatch.setattr(bfp, "published_report_mismatches", boom)
    out = tmp_path / "out.md"
    assert bfp.main(["--month", "2026-08", "--out", str(out)]) == 0
    assert "could not verify" in capsys.readouterr().err
    assert "f3a74bd" in out.read_text()
