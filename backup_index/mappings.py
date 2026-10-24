"""Validation locale raisonnable et comparaison récursive conservatrice."""
from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any

TYPES = set("binary boolean byte short integer long unsigned_long half_float float double scaled_float date date_nanos ip keyword constant_keyword wildcard text match_only_text object nested geo_point geo_shape flattened flat_object completion token_count alias histogram rank_feature rank_features knn_vector".split())


class MappingError(ValueError):
    pass


def validate_name(name: Any, prefix: str, label: str = "index") -> None:
    if (not isinstance(name, str) or not name.startswith(prefix + "-")
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name)
            or len(name.encode()) > 255):
        raise MappingError(f"{label} : nom invalide ou hors préfixe autorisé")


def validate_fields(properties: Any, path: str = "mappings.properties") -> None:
    if not isinstance(properties, dict):
        raise MappingError(f"{path} : objet attendu")
    for name, definition in properties.items():
        location = f"{path}.{name}"
        if not name or not isinstance(definition, dict):
            raise MappingError(f"{location} : définition invalide")
        kind = definition.get("type", "object" if "properties" in definition else None)
        if kind not in TYPES:
            raise MappingError(f"{location}.type : type absent ou non pris en charge")
        for key, value in definition.items():
            if key in {"enabled", "index", "doc_values", "store", "norms"} and type(value) is not bool:
                raise MappingError(f"{location}.{key} : booléen attendu")
            if key == "ignore_above" and (type(value) is not int or value < 0):
                raise MappingError(f"{location}.{key} : entier positif attendu")
        if "enabled" in definition and kind != "object":
            raise MappingError(f"{location}.enabled : réservé aux objets")
        if "properties" in definition:
            if kind not in {"object", "nested"}:
                raise MappingError(f"{location}.properties : objet ou nested attendu")
            validate_fields(definition["properties"], location + ".properties")
        if "fields" in definition:
            if kind in {"object", "nested"}:
                raise MappingError(f"{location}.fields : multi-champ scalaire attendu")
            validate_fields(definition["fields"], location + ".fields")


def flatten_settings(settings: dict, prefix: str = "") -> dict:
    result = {}
    for key, value in settings.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten_settings(value, path))
        else:
            result[path.removeprefix("index.")] = value
    return result


def validate_mapping(document: Any, prefix: str) -> None:
    if not isinstance(document, dict):
        raise MappingError("racine : objet attendu")
    validate_name(document.get("index"), prefix)
    validate_name(document.get("alias"), prefix, "alias")
    if not re.fullmatch(re.escape(document['alias']) + r'-v[1-9][0-9]*', document['index']):
        raise MappingError('index : nom physique attendu sous la forme <alias>-vN (N >= 1)')
    mappings = document.get("mappings")
    if not isinstance(mappings, dict) or "properties" not in mappings:
        raise MappingError("mappings.properties : objet obligatoire")
    source = mappings.get('_source', {})
    if not isinstance(source, dict) or source.get('enabled', True) is not True:
        raise MappingError('mappings._source.enabled : _source doit rester activé')
    if "dynamic" in mappings and (type(mappings["dynamic"]) not in {bool, str} or mappings["dynamic"] not in (True, False, "true", "false", "strict", "strict_allow_templates", "false_allow_templates")):
        raise MappingError("mappings.dynamic : valeur invalide")
    validate_fields(mappings["properties"])
    settings = document.get("settings")
    if not isinstance(settings, dict):
        raise MappingError("settings : objet attendu")
    for key, value in flatten_settings(settings).items():
        if not isinstance(value, (str, int, float, bool, list)):
            raise MappingError(f"settings.{key} : valeur invalide")
        if key in {"number_of_shards", "number_of_replicas"}:
            minimum = 1 if key == "number_of_shards" else 0
            if type(value) is not int or value < minimum:
                raise MappingError(f"settings.{key} : entier >= {minimum} attendu")


def load_mappings(directory: Path, prefix: str) -> dict[str, dict]:
    files = sorted(directory.glob("*.json"))
    if not files:
        raise MappingError(f"{directory} : aucun fichier JSON")
    result, names = {}, set()
    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            validate_mapping(document, prefix)
            if {document['index'], document['alias']} & names:
                raise MappingError('index/alias : nom défini dans plusieurs fichiers')
        except (ValueError, OSError) as error:
            if isinstance(error, json.JSONDecodeError):
                detail = f"JSON invalide ligne {error.lineno}, colonne {error.colno}"
            elif isinstance(error, MappingError):
                detail = str(error)
            else:
                detail = "lecture impossible"
            raise MappingError(f"{path.name} : {detail}") from None
        names.update((document['index'], document['alias']))
        result[path.stem] = document
    return result


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
