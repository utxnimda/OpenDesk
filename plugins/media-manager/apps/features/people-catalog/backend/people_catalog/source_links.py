"""Bounded identity-only index of explicit local person source bindings."""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import islice

from .model import source_identity
from .repository import safe_path


@dataclass(frozen=True)
class SourceLinkIndex:
    links: dict
    entity_ids: frozenset
    entity_types: dict
    retired: dict = field(default_factory=dict)

    def find(self, provider, kind, external_id):
        """Return an existing explicit/canonical owner, rejecting split identity."""
        key = (provider, kind, str(external_id))
        canonical = source_identity(*key)
        explicit = self.links.get(key)
        if canonical in self.retired:
            if explicit != self.retired[canonical]:
                raise ValueError("已合并人物的来源身份未由正确目标持有，不能重新创建独立人物")
            return explicit
        if canonical in self.entity_ids and self.entity_types[canonical] != kind:
            raise ValueError("来源规范人物标识已被不同实体类型占用；不能自动绑定")
        if explicit and canonical in self.entity_ids and canonical != explicit:
            raise ValueError("来源身份同时存在独立本地人物与其他绑定；不能自动合并或删除")
        return explicit or (canonical if canonical in self.entity_ids else None)

    def resolve(self, provider, kind, external_id):
        """Use a confirmed binding or deterministic new UUID; never match names."""
        return self.find(provider, kind, external_id) or source_identity(provider, kind, external_id)


def build_source_index(repository, *, maximum=10000):
    """Read once per operation; callers recheck under their shared write lock."""
    if type(maximum) is not int or not 1 <= maximum <= 100000:
        raise ValueError("人物来源索引数量上限无效")
    cached = getattr(repository, "import_rows", None)
    identities_to_read = (list(islice(cached, maximum + 1)) if cached is not None else
                          [path.stem for path in islice(safe_path(repository.root / "db").glob("*.yaml"), maximum + 1)])
    if len(identities_to_read) > maximum:
        raise ValueError("人物来源索引超过安全数量限制；未修改数据库")
    links, identities, types, retired, rows = {}, set(), {}, {}, {}
    for identity in identities_to_read:
        row = repository.read(identity)
        if row is None:
            continue
        rows[row["id"]] = row
        if row.get("merged_into"):
            retired[row["id"]] = row["merged_into"]
            continue
        identities.add(row["id"])
        types[row["id"]] = row["type"]
        for source in row.get("source_refs", []):
            key = (source["provider"], source["entity_type"], source["external_id"])
            owner = links.get(key)
            if owner and owner != row["id"]:
                raise ValueError("同一来源身份已绑定多个本地人物，不能自动合并")
            links[key] = row["id"]
    for identity, target_id in retired.items():
        target = rows.get(target_id)
        if not target or target.get("merged_into") or target["type"] != rows[identity]["type"]:
            raise ValueError("已合并人物的目标不存在、类型不一致或形成合并链")
    return SourceLinkIndex(links, frozenset(identities), types, retired)
