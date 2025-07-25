from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from randovania.game_description.resources.resource_database import ResourceDatabase
    from randovania.game_description.resources.resource_info import ResourceGain, ResourceGainTuple, ResourceInfo


class ResourceCollection:
    __slots__ = (
        "resource_bitmask",
        "_resource_array",
        "_existing_resources",
        "add_self_as_requirement_to_resources",
        "_damage_reduction_cache",
    )
    resource_bitmask: int
    _resource_array: list[int]
    _existing_resources: dict[int, ResourceInfo]
    add_self_as_requirement_to_resources: bool
    _damage_reduction_cache: dict[int, float] | None

    def __init__(self) -> None:
        self.resource_bitmask = 0
        self._resource_array = [0]
        self._existing_resources = {}
        self.add_self_as_requirement_to_resources = False
        self._damage_reduction_cache = None

    @classmethod
    def with_database(cls, database: ResourceDatabase) -> ResourceCollection:
        result = cls()
        result._resource_array = [0] * len(database.resource_by_index)
        return result

    def _resize_array_to(self, size: int) -> None:
        self._resource_array.extend(0 for _ in range(len(self._resource_array), size + 1))

    def __getitem__(self, item: ResourceInfo) -> int:
        resource_index = item.resource_index
        try:
            return self._resource_array[resource_index]
        except IndexError:
            self._resize_array_to(resource_index)
            return self._resource_array[resource_index]

    def __str__(self) -> str:
        return f"<ResourceCollection with {self.num_resources} resources>"

    @property
    def _comparison_tuple(self) -> tuple[ResourceGainTuple, bool]:
        return tuple(self.as_resource_gain()), self.add_self_as_requirement_to_resources

    def __hash__(self) -> int:
        return hash(self._comparison_tuple)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ResourceCollection) and (self._comparison_tuple == other._comparison_tuple)

    @property
    def num_resources(self) -> int:
        return len(self._existing_resources)

    def has_resource(self, resource: ResourceInfo) -> bool:
        return self[resource] > 0

    def is_resource_set(self, resource: ResourceInfo) -> bool:
        """
        Checks if the given resource has a value explicitly set, instead of using the fallback of 0.
        :param resource:
        :return:
        """
        return resource.resource_index in self._existing_resources

    def set_resource(self, resource: ResourceInfo, quantity: int) -> None:
        """Sets the quantity of the given resource to be exactly the given value.
        This method should be used in exceptional cases only. For common usage, use `add_resource_gain`.
        """
        resource_index = resource.resource_index
        self._damage_reduction_cache = None
        try:
            self._resource_array[resource_index] = quantity
        except IndexError:
            self._resize_array_to(resource_index)
            self._resource_array[resource_index] = quantity
        self._existing_resources[resource_index] = resource

        mask = 1 << resource_index
        if quantity > 0:
            self.resource_bitmask |= mask
        elif self.resource_bitmask & mask:
            self.resource_bitmask -= mask

    @classmethod
    def from_dict(cls, db: ResourceDatabase, resources: dict[ResourceInfo, int]) -> ResourceCollection:
        result = cls.with_database(db)
        result.add_resource_gain(resources.items())
        return result

    @classmethod
    def from_resource_gain(cls, db: ResourceDatabase, resource_gain: ResourceGain) -> ResourceCollection:
        result = cls.with_database(db)
        result.add_resource_gain(resource_gain)
        return result

    def add_resource_gain(self, resource_gain: ResourceGain) -> None:
        self._damage_reduction_cache = None
        for resource, quantity in resource_gain:
            resource_index = resource.resource_index
            try:
                self._resource_array[resource_index] += quantity
            except IndexError:
                self._resize_array_to(resource_index)
                self._resource_array[resource_index] += quantity
            self._existing_resources[resource_index] = resource

            mask = 1 << resource_index
            if self._resource_array[resource_index] > 0:
                self.resource_bitmask |= mask
            elif self.resource_bitmask & mask:
                self.resource_bitmask -= mask

    def as_resource_gain(self) -> ResourceGain:
        for index, resource in self._existing_resources.items():
            yield resource, self._resource_array[index]

    def remove_resource(self, resource: ResourceInfo) -> None:
        """
        Removes the given resource, making `is_resource_set` return False for it.
        This should be used in exceptional cases only. Consider `add_resource_gain` with negative gain instead.
        """
        resource_index = resource.resource_index
        self._existing_resources.pop(resource_index, None)
        try:
            self._resource_array[resource_index] = 0
        except IndexError:
            pass

        mask = 1 << resource_index
        if self.resource_bitmask & mask:
            self.resource_bitmask -= mask

    def duplicate(self) -> ResourceCollection:
        result = ResourceCollection()
        result._existing_resources.update(self._existing_resources)
        result._resource_array = copy.copy(self._resource_array)
        result.resource_bitmask = self.resource_bitmask
        result.add_self_as_requirement_to_resources = self.add_self_as_requirement_to_resources
        return result

    def get_damage_reduction_cache(self, resource: ResourceInfo) -> float | None:
        if self._damage_reduction_cache is not None:
            return self._damage_reduction_cache.get(resource.resource_index)
        return None

    def add_damage_reduction_cache(self, resource: ResourceInfo, multiplier: float) -> None:
        if self._damage_reduction_cache is None:
            self._damage_reduction_cache = {}
        self._damage_reduction_cache[resource.resource_index] = multiplier

    def __copy__(self) -> ResourceCollection:
        return self.duplicate()
