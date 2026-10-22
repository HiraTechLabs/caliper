"""Project principles: rule format, states, and the Git-backed service.

The principle files live in the project repository (``.caliper/principles/<key>.md``) and are
the source of truth. Caliper never invents rules: a new principle file is a heading and one
instruction line, and only the human writes the rules.
"""

import datetime
import hashlib
import re

from caliper.repo import GitRepo, RepoError

PRINCIPLE_ORDER = ["security", "business", "architecture", "techstack", "cleancode", "performance"]

PRINCIPLE_TITLES = {
    "security": "Security",
    "business": "Business",
    "architecture": "Clean Architecture / Design Patterns",
    "techstack": "TechStack",
    "cleancode": "Clean Code",
    "performance": "Performance",
}

PRINCIPLE_PREFIXES = {
    "security": "SEC",
    "business": "BUS",
    "architecture": "ARC",
    "techstack": "TEC",
    "cleancode": "CLC",
    "performance": "PRF",
}

PRINCIPLES_DIR = ".caliper/principles"

MISSING = "missing"
DRAFT = "draft"
APPROVED = "approved"

RULE_ID = re.compile(r"^[A-Z]{3}-\d{3}$")
RULE_LINE = re.compile(
    r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?\*{0,2}([A-Za-z]{3}-\d{3})\*{0,2}\s*[:.)-]?\s*(.*)$"
)


class PrinciplesError(Exception):
    pass


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def principle_path(key: str) -> str:
    return f"{PRINCIPLES_DIR}/{key}.md"


def principle_starter(key: str) -> str:
    """A new principle file: a heading and one instruction line. No rules are invented."""
    return (
        f"# {PRINCIPLE_TITLES[key]}\n"
        "\n"
        f"Write this principle's checkable rules here. One rule per line, each line starting "
        f"with a unique ID like {PRINCIPLE_PREFIXES[key]}-001.\n"
    )


def content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def parse_rules(content: str) -> list[dict]:
    """Rules are list items whose text starts with an ID like SEC-001."""
    rules = []
    for line in (content or "").splitlines():
        match = RULE_LINE.match(line)
        if not match:
            continue
        rule_id, text = match.group(1), match.group(2).strip()
        if not RULE_ID.match(rule_id):
            continue
        rules.append({"id": rule_id, "text": text})
    return rules


def rule_ids(content: str) -> list[str]:
    return [rule["id"] for rule in parse_rules(content)]


def validate_for_approval(content: str) -> list[str]:
    """Approval needs at least one rule and unique rule IDs. Returns the rule IDs."""
    rules = parse_rules(content)
    if not rules:
        raise PrinciplesError(
            "This principle has no numbered rules yet. Add at least one rule that starts "
            "with an ID like SEC-001."
        )
    seen: set[str] = set()
    duplicates: list[str] = []
    for rule in rules:
        if rule["id"] in seen and rule["id"] not in duplicates:
            duplicates.append(rule["id"])
        seen.add(rule["id"])
    if duplicates:
        raise PrinciplesError(
            "Rule IDs must be unique. Duplicate IDs: " + ", ".join(duplicates) + "."
        )
    return [rule["id"] for rule in rules]


def empty_record() -> dict:
    return {
        "state": MISSING,
        "content_hash": None,
        "approved_commit": None,
        "approved_at": None,
        "updated_at": None,
        "rule_ids": [],
    }


def derive_state(record: dict, content: str | None) -> str:
    """Missing / Draft / Approved. An approved file that changed is back to Draft."""
    record = record or {}
    if content is None:
        return record.get("state") or MISSING
    if record.get("state") == APPROVED and record.get("content_hash") != content_hash(content):
        return DRAFT
    if record.get("state") in (APPROVED, DRAFT):
        return record["state"]
    return DRAFT


def reconcile(record: dict, content: str | None) -> dict:
    """The stored record after comparing it with the file content in the repository."""
    record = record or {}
    if content is None:
        return empty_record()
    digest = content_hash(content)
    if record.get("state") == APPROVED and record.get("content_hash") != digest:
        # The file changed after it was approved: the approval is void.
        return {
            "state": DRAFT,
            "content_hash": digest,
            "approved_commit": None,
            "approved_at": None,
            "updated_at": _now(),
            "rule_ids": [],
        }
    state = record.get("state") if record.get("state") in (APPROVED, DRAFT) else DRAFT
    return {
        "state": state,
        "content_hash": digest,
        "approved_commit": record.get("approved_commit"),
        "approved_at": record.get("approved_at"),
        "updated_at": record.get("updated_at"),
        "rule_ids": list(record.get("rule_ids") or []),
    }


class PrinciplesService:
    """Reads and writes the principle files of a project in its Git repository."""

    def __init__(self, registry, repo_factory=None):
        self.registry = registry
        self._repo_factory = repo_factory or GitRepo

    def _repo(self, project_id: str) -> GitRepo:
        manifest = self.registry.manifest(project_id)
        url = (manifest.get("repo") or {}).get("url")
        if not url:
            raise PrinciplesError(
                "Add a repository URL first. The principle files live in the repository."
            )
        key_exists = self.registry.deploy_key_exists(project_id)
        if not key_exists and not url.startswith("file://"):
            raise PrinciplesError(
                "Generate the repository deploy key first, then test read/write access."
            )
        return self._repo_factory(
            self.registry.project_path(project_id) / "repo", url,
            key_context=None if url.startswith("file://")
            else (lambda: self.registry.deploy_key_context(project_id)),
        )

    def _read_repo(self, project_id: str) -> tuple[dict, str | None, str | None]:
        """Returns (contents, branch, error). Never raises for an unreachable repository."""
        try:
            repo = self._repo(project_id)
            branch = repo.sync()
        except (PrinciplesError, RepoError) as exc:
            return {}, None, str(exc)
        contents = {}
        for key in PRINCIPLE_ORDER:
            try:
                contents[key] = repo.read(principle_path(key))
            except RepoError as exc:
                return {}, None, str(exc)
        return contents, branch, None

    def list_principles(self, project_id: str) -> dict:
        """All six principles with their state, content and approval record."""
        records = self.registry.principle_records(project_id)
        contents, branch, error = self._read_repo(project_id)
        items = []
        for key in PRINCIPLE_ORDER:
            record = records.get(key) or {}
            content = contents.get(key)
            state = derive_state(record, content)
            items.append({
                "key": key,
                "title": PRINCIPLE_TITLES[key],
                "state": state,
                "content": content or "",
                "content_hash": content_hash(content) if content is not None else None,
                "approved_commit": record.get("approved_commit"),
                "approved_at": record.get("approved_at"),
                "updated_at": record.get("updated_at"),
                "rule_ids": list(record.get("rule_ids") or []),
                "starter": principle_starter(key),
                "stale": bool(error),
            })
        return {
            "project_id": project_id,
            "default_branch": branch,
            "repo_error": error,
            "principles": items,
        }

    def read_principle(self, project_id: str, key: str) -> dict:
        if key not in PRINCIPLE_ORDER:
            raise PrinciplesError(f"Unknown principle '{key}'.")
        listing = self.list_principles(project_id)
        for item in listing["principles"]:
            if item["key"] == key:
                return item
        raise PrinciplesError(f"Unknown principle '{key}'.")

    def sync(self, project_id: str) -> dict:
        """Read the principle files from the repository default branch."""
        repo = self._repo(project_id)
        try:
            branch = repo.sync()
        except RepoError as exc:
            raise PrinciplesError(str(exc))
        records = self.registry.principle_records(project_id)
        for key in PRINCIPLE_ORDER:
            content = repo.read(principle_path(key))
            records[key] = reconcile(records.get(key), content)
        self.registry.set_principle_records(project_id, records)
        listing = self.list_principles(project_id)
        listing["default_branch"] = branch
        listing["synced"] = True
        return listing

    def put(self, project_id: str, key: str, content: str | None) -> dict:
        """Write a principle file in the repository and commit it to the default branch."""
        if key not in PRINCIPLE_ORDER:
            raise PrinciplesError(f"Unknown principle '{key}'.")
        repo = self._repo(project_id)
        text = content if content is not None else ""
        try:
            repo.ensure_clone()
            repo.sync()
            if not text.strip():
                existing = repo.read(principle_path(key))
                if existing is not None:
                    raise PrinciplesError(
                        "Content is required. The principle file already exists in the repository."
                    )
                text = principle_starter(key)
            commit = repo.write(
                principle_path(key), text, f"docs(principles): write the {key} principle"
            )
        except RepoError as exc:
            raise PrinciplesError(str(exc))
        records = self.registry.principle_records(project_id)
        records[key] = {
            "state": DRAFT,
            "content_hash": content_hash(text),
            "approved_commit": None,
            "approved_at": None,
            "updated_at": _now(),
            "rule_ids": [],
        }
        self.registry.set_principle_records(project_id, records)
        return {
            "key": key,
            "title": PRINCIPLE_TITLES[key],
            "state": DRAFT,
            "content": text,
            "content_hash": content_hash(text),
            "commit": commit,
            "approved_commit": None,
            "approved_at": None,
            "rule_ids": [],
        }

    def approve(self, project_id: str, key: str) -> dict:
        """Approve a principle at its current content hash and commit."""
        if key not in PRINCIPLE_ORDER:
            raise PrinciplesError(f"Unknown principle '{key}'.")
        repo = self._repo(project_id)
        try:
            repo.ensure_clone()
            repo.sync()
            content = repo.read(principle_path(key))
            if content is None:
                raise PrinciplesError(
                    f"The {key} principle file is not in the repository yet. Write it first."
                )
            ids = validate_for_approval(content)
            commit = repo.head_commit()
        except RepoError as exc:
            raise PrinciplesError(str(exc))
        records = self.registry.principle_records(project_id)
        records[key] = {
            "state": APPROVED,
            "content_hash": content_hash(content),
            "approved_commit": commit,
            "approved_at": _now(),
            "updated_at": _now(),
            "rule_ids": ids,
        }
        self.registry.set_principle_records(project_id, records)
        return {
            "key": key,
            "title": PRINCIPLE_TITLES[key],
            "state": APPROVED,
            "content": content,
            "content_hash": content_hash(content),
            "approved_commit": commit,
            "approved_at": records[key]["approved_at"],
            "rule_ids": ids,
        }

    def unapprove(self, project_id: str, key: str) -> dict:
        """Withdraw an approval; the principle goes back to Draft."""
        if key not in PRINCIPLE_ORDER:
            raise PrinciplesError(f"Unknown principle '{key}'.")
        records = self.registry.principle_records(project_id)
        record = records.get(key) or empty_record()
        record.update({
            "state": DRAFT,
            "approved_commit": None,
            "approved_at": None,
            "updated_at": _now(),
            "rule_ids": [],
        })
        records[key] = record
        self.registry.set_principle_records(project_id, records)
        return {
            "key": key,
            "title": PRINCIPLE_TITLES[key],
            "state": DRAFT,
            "content_hash": record.get("content_hash"),
            "approved_commit": None,
            "approved_at": None,
            "rule_ids": [],
        }
