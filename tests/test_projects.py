import pytest
import yaml

from caliper.principles import PRINCIPLES
from caliper.registry import PRINCIPLE_ORDER, ProjectError, ProjectRegistry


@pytest.fixture()
def registry(tmp_path):
    return ProjectRegistry(tmp_path / "projects")


def test_create_project_writes_manifest_and_six_principles(registry):
    manifest = registry.create_project(name="Shop API", repo_mode="create_new")

    assert manifest["id"] == "shop-api"
    assert manifest["name"] == "Shop API"
    assert manifest["repo"]["mode"] == "create_new"
    assert manifest["repo"]["url"] is None
    assert set(manifest["environments"]) == {"dev", "prod"}

    project_dir = registry.projects_root / "shop-api"
    assert (project_dir / "manifest.yaml").is_file()
    for key in PRINCIPLE_ORDER:
        principle_file = project_dir / "principles" / f"{key}.md"
        assert principle_file.is_file()
        content = principle_file.read_text(encoding="utf-8")
        first_rule_id = PRINCIPLES[key]["rules"][0][1]
        assert f"**{first_rule_id}**" in content


def test_principle_files_contain_numbered_rule_ids(registry):
    registry.create_project(name="Birds", repo_mode="create_new")
    content = (registry.projects_root / "birds" / "principles" / "security.md").read_text(encoding="utf-8")
    for rule_id in [r[1] for r in PRINCIPLES["security"]["rules"]]:
        assert rule_id in content


def test_product_field_is_accepted_and_stored(registry):
    manifest = registry.create_project(name="Cos Contracts", repo_mode="create_new", product="cos")
    stored = yaml.safe_load((registry.projects_root / "cos-contracts" / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["product"] == "cos"
    assert stored["product"] == "cos"


def test_project_ids_are_unique_and_slugs_are_stable(registry):
    registry.create_project(name="Shop API", repo_mode="create_new")
    with pytest.raises(ProjectError, match="already exists"):
        registry.create_project(name="shop api", repo_mode="create_new")


def test_existing_repo_requires_url(registry):
    with pytest.raises(ProjectError, match="Repository URL"):
        registry.create_project(name="shop-api", repo_mode="existing", repo_url="   ")


def test_invalid_mode_and_preset_rejected(registry):
    with pytest.raises(ProjectError, match="mode"):
        registry.create_project(name="a", repo_mode="gitclone")
    with pytest.raises(ProjectError, match="preset"):
        registry.create_project(name="a", repo_mode="create_new", stack_preset="rust")


def test_empty_name_rejected(registry):
    with pytest.raises(ProjectError, match="name"):
        registry.create_project(name="   ", repo_mode="create_new")


def test_list_and_get_roundtrip(registry):
    registry.create_project(name="Alpha", repo_mode="create_new")
    registry.create_project(name="Beta", repo_mode="create_new")

    ids = [p["id"] for p in registry.list_projects()]
    assert ids == ["alpha", "beta"]

    loaded = registry.get_project("alpha")
    assert loaded["name"] == "Alpha"
    assert [p["key"] for p in loaded["principles"]] == PRINCIPLE_ORDER


def test_missing_project_raises(registry):
    with pytest.raises(ProjectError, match="does not exist"):
        registry.get_project("ghost")
