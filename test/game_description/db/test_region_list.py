from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from frozendict import frozendict

from randovania.game_description.db.area import Area
from randovania.game_description.db.dock import DockLock, DockLockType, DockType, DockWeakness
from randovania.game_description.db.dock_lock_node import DockLockNode
from randovania.game_description.db.dock_node import DockNode
from randovania.game_description.db.node import NodeContext
from randovania.game_description.db.node_identifier import NodeIdentifier
from randovania.game_description.db.region import Region
from randovania.game_description.db.region_list import RegionList
from randovania.game_description.requirements.base import Requirement
from randovania.game_description.requirements.requirement_and import RequirementAnd
from randovania.game_description.requirements.resource_requirement import ResourceRequirement
from randovania.game_description.resources.node_resource_info import NodeResourceInfo
from randovania.game_description.resources.resource_collection import ResourceCollection
from randovania.game_description.resources.resource_type import ResourceType
from randovania.game_description.resources.simple_resource_info import SimpleResourceInfo
from randovania.games.prime1.layout.prime_configuration import PrimeConfiguration
from randovania.layout import filtered_database

if TYPE_CHECKING:
    from randovania.game_description.game_description import GameDescription
    from randovania.game_description.game_patches import GamePatches


def test_connections_from_dock_blast_shield(empty_patches: GamePatches):
    # Setup
    db = empty_patches.game.resource_database
    trivial = Requirement.trivial()
    req_1 = ResourceRequirement.simple(SimpleResourceInfo(0, "Ev1", "Ev1", ResourceType.EVENT))
    req_2 = ResourceRequirement.simple(SimpleResourceInfo(1, "Ev2", "Ev2", ResourceType.EVENT))
    dock_type = DockType("Type", "Type", frozendict())
    weak_1 = DockWeakness(0, "Weak 1", frozendict(), req_1, None)
    weak_2 = DockWeakness(1, "Weak 2", frozendict(), trivial, DockLock(DockLockType.FRONT_BLAST_BACK_BLAST, req_2))

    node_1_identifier = NodeIdentifier.create("W", "Area 1", "Node 1")
    node_2_identifier = NodeIdentifier.create("W", "Area 2", "Node 2")

    node_1 = DockNode(
        node_1_identifier,
        0,
        False,
        None,
        "",
        ("default",),
        {},
        False,
        dock_type,
        node_2_identifier,
        weak_1,
        None,
        None,
        False,
        (),
        None,
    )
    node_1_lock = DockLockNode.create_from_dock(node_1, 1, db)
    node_2 = DockNode(
        node_2_identifier,
        2,
        False,
        None,
        "",
        ("default",),
        {},
        False,
        dock_type,
        node_1_identifier,
        weak_2,
        None,
        None,
        False,
        (),
        None,
    )
    node_2_lock = DockLockNode.create_from_dock(node_2, 3, db)

    area_1 = Area("Area 1", [node_1, node_1_lock], {}, {})
    area_2 = Area("Area 2", [node_2, node_2_lock], {}, {})

    region = Region("W", [area_1, area_2], {})
    region_list = RegionList([region])
    region_list.ensure_has_node_cache()

    game_mock: GameDescription = MagicMock()
    game_mock.region_list = region_list
    patches = dataclasses.replace(empty_patches, game=game_mock)

    context = NodeContext(
        patches=patches,
        current_resources=ResourceCollection(),
        database=patches.game.resource_database,
        node_provider=region_list,
    )

    # Run
    result_1 = list(node_1.connections_from(context))
    result_2 = list(node_2.connections_from(context))

    # Assert
    simple = ResourceRequirement.simple

    assert result_1 == [
        (node_2, RequirementAnd([req_1, simple(NodeResourceInfo.from_node(node_2, context))])),
        (node_1_lock, RequirementAnd([trivial, req_2])),
    ]
    assert result_2 == [
        (node_1, RequirementAnd([Requirement.trivial(), simple(NodeResourceInfo.from_node(node_2, context))])),
        (node_2_lock, req_2),
    ]


def test_node_index_multiple_games(default_prime_preset):
    default_config = default_prime_preset.configuration
    assert isinstance(default_config, PrimeConfiguration)
    alt_config = dataclasses.replace(default_config, items_every_room=True)

    alt_game = filtered_database.game_description_for_layout(alt_config)
    default_game = filtered_database.game_description_for_layout(default_config)

    all_nodes_default = default_game.region_list.all_nodes
    all_nodes_alt = alt_game.region_list.all_nodes

    for node in alt_game.region_list.iterate_nodes():
        assert all_nodes_alt[node.node_index] is node

    for node in default_game.region_list.iterate_nodes():
        assert all_nodes_default[node.node_index] is node


@pytest.mark.parametrize(
    ("feature_id", "expected_node_ids"),
    [
        ("boss", ["Intro/Boss Arena/Pickup (Free Loot)"]),
        ("ledge", ["Intro/Ledge Room/Pickup (Double Jump)"]),
    ],
)
def test_pickup_nodes_with_feature(feature_id: str, expected_node_ids: list[str], blank_game_description):
    feature = blank_game_description.hint_feature_database[feature_id]

    # test twice for coverage on the cache
    for i in range(2):
        nodes = blank_game_description.region_list.pickup_nodes_with_feature(feature)
        node_ids = [node.identifier.as_string for node in nodes]
        assert node_ids == expected_node_ids
