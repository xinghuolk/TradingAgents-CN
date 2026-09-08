from __future__ import annotations

import re
from copy import deepcopy
from itertools import product
from types import SimpleNamespace
from typing import Mapping

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


def _operator_matches(
    actual: object, exists: bool, operator: str, expected: object
) -> bool:
    values = actual if isinstance(actual, list) else [actual]
    if operator == "$in":
        return any(value in expected for value in values)  # type: ignore[operator]
    if operator == "$ne":
        return all(value != expected for value in values)
    if operator == "$exists":
        return exists is bool(expected)
    raise AssertionError(f"unsupported fake Mongo operator: {operator}")


def matches(document: Mapping[str, object], query: Mapping[str, object]) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not any(
                matches(document, branch)
                for branch in expected  # type: ignore[union-attr]
            ):
                return False
            continue
        exists = key in document
        actual = document.get(key)
        if isinstance(expected, Mapping):
            options = expected.get("$options")
            for operator, operand in expected.items():
                if operator == "$options":
                    continue
                if operator == "$regex":
                    flags = re.IGNORECASE if options == "i" else 0
                    values = actual if isinstance(actual, list) else [actual]
                    if not any(
                        re.search(str(operand), str(value), flags) is not None
                        for value in values
                    ):
                        return False
                elif not _operator_matches(actual, exists, operator, operand):
                    return False
        elif isinstance(actual, list):
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = deepcopy(documents)

    def sort(self, keys: list[tuple[str, int]]) -> FakeCursor:
        for key, direction in reversed(keys):
            self.documents.sort(
                key=lambda document: (
                    document.get(key) is not None,
                    document.get(key),
                ),
                reverse=direction < 0,
            )
        return self

    def skip(self, count: int) -> FakeCursor:
        self.documents = self.documents[count:]
        return self

    def limit(self, count: int) -> FakeCursor:
        self.documents = self.documents[:count]
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, object]]:
        if length is None:
            return deepcopy(self.documents)
        return deepcopy(self.documents[:length])


class FakeCollection:
    def __init__(self) -> None:
        self.documents: list[dict[str, object]] = []
        self.indexes: dict[str, tuple[tuple[str, ...], bool]] = {}
        self.unique_keys: set[tuple[str, ...]] = set()

    async def create_index(
        self,
        keys: list[tuple[str, int]],
        *,
        name: str,
        unique: bool = False,
        **_: object,
    ) -> str:
        field_names = tuple(key for key, _direction in keys)
        self.indexes[name] = (field_names, unique)
        if unique:
            self.unique_keys.add(field_names)
        return name

    def _unique_identities(
        self, document: Mapping[str, object], keys: tuple[str, ...]
    ) -> set[tuple[object, ...]]:
        choices = []
        for key in keys:
            value = document.get(key)
            choices.append(value if isinstance(value, list) else [value])
        return set(product(*choices))

    def _persist(
        self,
        document: Mapping[str, object],
        replacing: dict[str, object] | None = None,
    ) -> dict[str, object]:
        candidate = deepcopy(dict(document))
        for keys in self.unique_keys:
            identities = self._unique_identities(candidate, keys)
            for existing in self.documents:
                if existing is replacing:
                    continue
                if identities & self._unique_identities(existing, keys):
                    raise DuplicateKeyError(f"duplicate index {keys}")
        return candidate

    async def find_one(
        self, query: Mapping[str, object]
    ) -> dict[str, object] | None:
        found = next(
            (document for document in self.documents if matches(document, query)),
            None,
        )
        return deepcopy(found)

    async def insert_one(self, document: Mapping[str, object]) -> SimpleNamespace:
        stored = self._persist(document)
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored.get("_id"))

    def _updated_document(
        self,
        original: Mapping[str, object],
        update: Mapping[str, Mapping[str, object]],
        *,
        inserted: bool,
    ) -> dict[str, object]:
        changed = deepcopy(dict(original))
        if inserted:
            changed.update(deepcopy(dict(update.get("$setOnInsert", {}))))
        changed.update(deepcopy(dict(update.get("$set", {}))))
        for key, amount in update.get("$inc", {}).items():
            changed[key] = changed.get(key, 0) + amount  # type: ignore[operator]
        for key in update.get("$unset", {}):
            changed.pop(key, None)
        for key, value in update.get("$push", {}).items():
            changed.setdefault(key, [])
            changed[key].append(deepcopy(value))  # type: ignore[union-attr]
        for key, value in update.get("$addToSet", {}).items():
            changed.setdefault(key, [])
            if value not in changed[key]:  # type: ignore[operator]
                changed[key].append(deepcopy(value))  # type: ignore[union-attr]
        return changed

    async def update_one(
        self,
        query: Mapping[str, object],
        update: Mapping[str, Mapping[str, object]],
        upsert: bool = False,
    ) -> SimpleNamespace:
        original = next(
            (document for document in self.documents if matches(document, query)),
            None,
        )
        if original is None and not upsert:
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)
        base = original if original is not None else {
            key: deepcopy(value)
            for key, value in query.items()
            if not key.startswith("$") and not isinstance(value, Mapping)
        }
        changed = self._updated_document(base, update, inserted=original is None)
        stored = self._persist(changed, original)
        if original is None:
            self.documents.append(stored)
        else:
            self.documents[self.documents.index(original)] = stored
        return SimpleNamespace(
            matched_count=int(original is not None),
            modified_count=int(stored != original),
            upserted_id=None if original is not None else stored.get("_id"),
        )

    async def find_one_and_update(
        self,
        query: Mapping[str, object],
        update: Mapping[str, Mapping[str, object]],
        *,
        upsert: bool = False,
        return_document: ReturnDocument = ReturnDocument.BEFORE,
    ) -> dict[str, object] | None:
        original = next(
            (document for document in self.documents if matches(document, query)),
            None,
        )
        position = self.documents.index(original) if original is not None else None
        before = deepcopy(original)
        await self.update_one(query, update, upsert=upsert)
        if return_document == ReturnDocument.AFTER:
            if position is not None:
                return deepcopy(self.documents[position])
            return await self.find_one(query)
        return before

    async def delete_one(self, query: Mapping[str, object]) -> SimpleNamespace:
        original = next(
            (document for document in self.documents if matches(document, query)),
            None,
        )
        if original is not None:
            self.documents.remove(original)
        return SimpleNamespace(deleted_count=int(original is not None))

    async def count_documents(self, query: Mapping[str, object]) -> int:
        return sum(matches(document, query) for document in self.documents)

    def find(self, query: Mapping[str, object]) -> FakeCursor:
        return FakeCursor(
            [document for document in self.documents if matches(document, query)]
        )


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())
