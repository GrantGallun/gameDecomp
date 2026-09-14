"""The imported IDO knowledge must stay inert until measured here.

The document these came from was RIGHT about alignment nops and WRONG about
scratch preludes in our harness. That is exactly why an import must not be
allowed to steer anything on the strength of its source.
"""

from patterns import catalog, imported_ido  # noqa: F401  (import registers)


def test_every_imported_entry_is_a_hypothesis():
    """None of this is confirmed HERE, so none of it may change behaviour."""
    imported = [p for p in catalog.CATALOG.values()
                if p.id.startswith("ido-")]
    assert imported, "expected the imported entries to be registered"
    unconfirmed = [p.id for p in imported if not p.is_hypothesis]
    assert unconfirmed == [], (
        f"imported entries claim confirmation we have not measured: "
        f"{unconfirmed}")


def test_imported_entries_carry_a_prescription_and_a_kind():
    for p in catalog.CATALOG.values():
        if not p.id.startswith("ido-"):
            continue
        assert p.kind in ("evidence", "solver", "review"), p.id
        assert p.prescription.strip(), p.id
        assert p.means.strip(), p.id


def test_registration_rejects_duplicate_ids():
    """The catalog must not silently accept a second entry under one id."""
    import pytest
    existing = next(iter(catalog.CATALOG.values()))
    with pytest.raises(ValueError):
        catalog.register(catalog.Pattern(
            id=existing.id, name="dupe", kind="review",
            looks_like="x", means="y", prescription="z"))


def test_allocation_facts_are_present():
    """The measured allocator constraints are the load-bearing part."""
    for wanted in ("ido-t-registers-are-colored-not-temps",
                   "ido-web-ties-break-on-construction-order",
                   "ido-web-priority-is-non-monotone",
                   "ido-dead-filler-is-not-allocation-neutral"):
        assert wanted in catalog.CATALOG
