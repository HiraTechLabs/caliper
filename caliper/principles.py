PRINCIPLES = {
    "security": {
        "title": "Security",
        "rules": [
            ("security", "SEC-001", "All external input is validated at the boundary (types, ranges, encoding)."),
            ("security", "SEC-002", "Secrets are never hardcoded; they come from environment or a secret store."),
            ("security", "SEC-003", "Every mutating endpoint enforces authorization before doing work."),
            ("security", "SEC-004", "Dependencies are pinned, known-vulnerable versions are not accepted."),
            ("security", "SEC-005", "Logs never contain secrets, tokens, or personal data."),
        ],
    },
    "business": {
        "title": "Business",
        "rules": [
            ("business", "BUS-001", "Each change is traceable to a user-visible business outcome."),
            ("business", "BUS-002", "Money-affecting logic is explicit, tested, and reviewed line by line."),
            ("business", "BUS-003", "Feature flags have a removal date; dead code is deleted, not commented out."),
            ("business", "BUS-004", "Public behavior changes are documented (API/UX changelog entry)."),
        ],
    },
    "architecture": {
        "title": "Clean Architecture / Design Patterns",
        "rules": [
            ("architecture", "ARC-001", "Domain logic has no framework imports; frameworks live at the edge."),
            ("architecture", "ARC-002", "Dependencies point inward; outer layers may not be imported by inner ones."),
            ("architecture", "ARC-003", "Side effects (network, fs, clock) are behind interfaces, injected at the edge."),
            ("architecture", "ARC-004", "Patterns are used only when they buy something now, not by speculative intent."),
            ("architecture", "ARC-005", "Each module has one reason to change; cross-cutting concerns go through one point."),
        ],
    },
    "techstack": {
        "title": "TechStack",
        "rules": [
            ("techstack", "TEC-001", "New libraries are approved via a Decision entry before merging."),
            ("techstack", "TEC-002", "One established way per concern; no parallel frameworks for the same job."),
            ("techstack", "TEC-003", "Versions and CI behavior match between local, Dev and Prod runs."),
        ],
    },
    "cleancode": {
        "title": "Clean Code",
        "rules": [
            ("cleancode", "CLC-001", "Names state intent; no single-letter names outside loop indexes."),
            ("cleancode", "CLC-002", "Functions do one thing at one level of abstraction; short is a symptom, not a goal."),
            ("cleancode", "CLC-003", "No comments that restate code; comments explain why, never what."),
            ("cleancode", "CLC-004", "Errors are handled explicitly; no silent excepts or swallowed errors."),
        ],
    },
    "performance": {
        "title": "Performance",
        "rules": [
            ("performance", "PRF-001", "No N+1 queries; list endpoints fetch in batches or eager loading."),
            ("performance", "PRF-002", "O(n^2) or worse over unbounded input needs an explicit justification."),
            ("performance", "PRF-003", "Hot paths measure before optimizing; no perf work without evidence."),
            ("performance", "PRF-004", "Payloads and queries return only what the caller uses."),
        ],
    },
}

RULE_PREFIXES = {key: meta["rules"][0][0] for key, meta in PRINCIPLES.items()}


def render_principle_file(key: str) -> str:
    meta = PRINCIPLES[key]
    lines = [f"# {meta['title']}", "", "Checkable rules. Every plan must cite rule IDs from this file,", "and every review gives pass/fail per rule ID.", ""]
    for _, rid, text in meta["rules"]:
        lines.append(f"- **{rid}**: {text}")
    lines.append("")
    return "\n".join(lines)
