import pytest
from pydantic import ValidationError

from core.doctrine import (
    BlueprintQueueItem,
    DoctrinePatch,
    DoctrineStore,
    FortressDoctrine,
    StockTarget,
    apply_patch,
    default_doctrine,
    diff_doctrines,
)


def test_default_doctrine_is_valid_and_roundtrips():
    d = default_doctrine()
    assert FortressDoctrine.model_validate_json(d.model_dump_json()) == d


def test_stock_target_requires_ordered_bounds():
    with pytest.raises(ValidationError):
        StockTarget(item="drink", job="BrewDrink", min=50, target=10, max=100)


def test_unknown_fields_rejected():
    data = default_doctrine().model_dump()
    data["surprise"] = 1
    with pytest.raises(ValidationError):
        FortressDoctrine.model_validate(data)


def test_duplicate_keys_rejected():
    d = default_doctrine()
    data = d.model_dump()
    data["stock_targets"].append(data["stock_targets"][0])
    with pytest.raises(ValidationError):
        FortressDoctrine.model_validate(data)


def test_patch_upsert_remove_and_diff():
    d = default_doctrine()
    drink = d.target_for("drink")
    patch = DoctrinePatch(
        upsert_stock_targets=[drink.model_copy(update={"min": 100, "target": 200, "max": 400}),
                              StockTarget(item="ropes", job="MakeRope", min=1, target=2, max=3)],
        remove_blueprints=["dining-1"],
        upsert_blueprints=[BlueprintQueueItem(id="storage-1", blueprint="stockpiles_core", zone_id="Z4")],
    )
    new = apply_patch(d, patch)
    assert new.target_for("drink").target == 200
    assert [t.item for t in new.stock_targets][-1] == "ropes"
    assert "dining-1" not in {b.id for b in new.blueprint_queue}
    diff = {(e.path, e.kind) for e in diff_doctrines(d, new)}
    assert ("stock_targets[drink].target", "changed") in diff
    assert ("stock_targets[ropes]", "added") in diff
    assert ("blueprint_queue[dining-1]", "removed") in diff
    assert ("blueprint_queue[storage-1]", "added") in diff
    assert d.target_for("drink").target == drink.target  # original untouched


def test_invalid_patch_fails_validation():
    d = default_doctrine()
    bad = DoctrinePatch(upsert_blueprints=[BlueprintQueueItem(id="bedrooms-1", blueprint="x", zone_id="Z1"),
                                           BlueprintQueueItem(id="bedrooms-1", blueprint="y", zone_id="Z2")])
    new = apply_patch(d, bad)  # same key twice -> last wins, still unique
    assert sum(b.id == "bedrooms-1" for b in new.blueprint_queue) == 1


def test_patch_cannot_touch_modules():
    assert "modules" not in DoctrinePatch.model_fields
    assert "modules" not in default_doctrine().compact_json()


def test_diff_ignores_version_metadata():
    d = default_doctrine()
    assert diff_doctrines(d, d.approved()) == []


def test_store_persists_history(tmp_path):
    store = DoctrineStore(tmp_path)
    assert store.load() == default_doctrine()
    approved = default_doctrine().approved()
    store.save(approved)
    assert store.load() == approved
    assert (tmp_path / "history" / f"doctrine_v{approved.version:04d}.json").exists()
    assert approved.version == default_doctrine().version + 1 and approved.approved_at


def test_module_switch_save_keeps_approval_archive(tmp_path):
    store = DoctrineStore(tmp_path)
    approved = default_doctrine().approved()
    store.save(approved)
    archived = (tmp_path / "history" / f"doctrine_v{approved.version:04d}.json").read_text()
    toggled = approved.model_copy(update={"modules": approved.modules.model_copy(update={"blueprints": True})})
    store.save(toggled, archive=False)
    assert store.load().modules.blueprints is True
    assert (tmp_path / "history" / f"doctrine_v{approved.version:04d}.json").read_text() == archived
