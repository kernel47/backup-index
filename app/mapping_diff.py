"""Comparaison récursive et patchs additifs des mappings OpenSearch."""
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Difference:
    missing: list[str] = field(default_factory=list)
    incompatible: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    additions: dict = field(default_factory=dict)

    @property
    def conforms(self) -> bool:
        return not self.missing and not self.incompatible


# Omitted server defaults are equivalent to explicitly configured defaults.
DEFAULTS = {"dynamic": True, "enabled": True, "index": True, "doc_values": True,
            "store": False, "norms": True, "coerce": True,
            "ignore_malformed": False, "format": "strict_date_optional_time||epoch_millis"}


def normalized(value: Any) -> Any:
    if value == "true":
        return True
    if value == "false":
        return False
    return value


def compare_mappings(local: dict, remote: dict) -> Difference:
    diff = Difference()

    def compare(expected: dict, actual: dict, path: str, root: bool = False) -> dict:
        patch = {}
        if not root:
            local_type = expected.get("type", "object" if "properties" in expected else None)
            remote_type = actual.get("type", "object" if "properties" in actual else None)
            if local_type != remote_type:
                diff.incompatible.append(f"{path}.type : {remote_type} -> {local_type}")
                return patch
        keys = (set(expected) | set(actual)) - {"properties", "fields", "type", "_meta"}
        for key in sorted(keys):
            # Remote mapping options affect compatibility even when omitted locally.
            left = expected.get(key, DEFAULTS.get(key))
            right = actual.get(key, DEFAULTS.get(key))
            if root and key == '_source':
                left = {'enabled': True, **(left or {})}
                right = {'enabled': True, **(right or {})}
            if normalized(left) != normalized(right):
                diff.incompatible.append(f"{path}.{key} : {right!r} -> {left!r}")
        for container in ("properties", "fields"):
            wanted = expected.get(container, {})
            existing = actual.get(container, {})
            additions = {}
            for name, definition in wanted.items():
                location = f"{path}.{name}" if path else name
                if name not in existing:
                    diff.missing.append(location)
                    additions[name] = deepcopy(definition)
                else:
                    nested = compare(definition, existing[name], location)
                    if nested:
                        additions[name] = nested
            for name in existing.keys() - wanted.keys():
                diff.extra.append(f"{path}.{name}" if path else name)
            if additions:
                patch[container] = additions
        return patch

    diff.additions = compare(local, remote, "", root=True)
    return diff


def count_fields(mapping: dict) -> int:
    return sum(1 + count_fields(value) for key in ("properties", "fields")
               for value in mapping.get(key, {}).values())
