from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

import randovania
from randovania.exporter import item_names, pickup_exporter
from randovania.exporter.hints import credits_spoiler, guaranteed_item_hint
from randovania.exporter.patch_data_factory import PatchDataFactory
from randovania.game.game_enum import RandovaniaGame
from randovania.game_description.db.dock_node import DockNode
from randovania.game_description.db.pickup_node import PickupNode
from randovania.games.prime1.exporter.hint_namer import PrimeHintNamer
from randovania.games.prime1.exporter.vanilla_maze_seeds import VANILLA_MAZE_SEEDS
from randovania.games.prime1.layout.prime_configuration import (
    LayoutCutsceneMode,
    PrimeConfiguration,
    RoomRandoMode,
)
from randovania.games.prime1.layout.prime_cosmetic_patches import PrimeCosmeticPatches
from randovania.games.prime1.patcher import prime1_elevators, prime_items
from randovania.generator.pickup_pool import pickup_creator
from randovania.layout.base.hint_configuration import SpecificPickupHintMode

if TYPE_CHECKING:
    from random import Random

    from randovania.exporter.patch_data_factory import PatcherDataMeta
    from randovania.game_description.db.area_identifier import AreaIdentifier
    from randovania.game_description.db.dock import DockType
    from randovania.game_description.db.node_identifier import NodeIdentifier
    from randovania.game_description.db.region_list import Region, RegionList
    from randovania.game_description.game_database_view import ResourceDatabaseView
    from randovania.game_description.pickup.pickup_entry import PickupEntry
    from randovania.game_description.resources.item_resource_info import ItemResourceInfo
    from randovania.game_description.resources.resource_collection import ResourceCollection
    from randovania.layout.layout_description import LayoutDescription
    from randovania.lib.json_lib import JsonObject

_EASTER_EGG_SHINY_MISSILE = 1024

_STARTING_ITEM_NAME_TO_INDEX = {
    "powerBeam": "Power",
    "ice": "Ice",
    "wave": "Wave",
    "plasma": "Plasma",
    "missiles": "Missile",
    "scanVisor": "Scan",
    "bombs": "Bombs",
    "powerBombs": "PowerBomb",
    "flamethrower": "Flamethrower",
    "thermalVisor": "Thermal",
    "charge": "Charge",
    "superMissile": "Supers",
    "grapple": "Grapple",
    "xray": "X-Ray",
    "iceSpreader": "IceSpreader",
    "spaceJump": "SpaceJump",
    "morphBall": "MorphBall",
    "combatVisor": "Combat",
    "boostBall": "Boost",
    "spiderBall": "Spider",
    "gravitySuit": "GravitySuit",
    "variaSuit": "VariaSuit",
    "phazonSuit": "PhazonSuit",
    "energyTanks": "EnergyTank",
    "wavebuster": "Wavebuster",
}

# The following locations have cutscenes that weren't removed
_LOCATIONS_WITH_MODAL_ALERT = {
    63,  # Artifact Temple
    23,  # Watery Hall (Charge Beam)
    50,  # Research Core
}

# Show a popup on collection if two or more is for another player.
# The location to the right is considered for the count, but it can't show a popup.
_LOCATIONS_GROUPED_TOGETHER = [
    ({0, 1, 2, 3}, None),  # Main Plaza
    ({5, 6, 7}, None),  # Ruined Shrine (all 3)
    ({94}, 97),  # Warrior shrine -> Fiery Shores Tunnel
    ({55}, 54),  # Gravity Chamber: Upper -> Lower
    ({19, 17}, None),  # Hive Totem + Transport Access North
    ({59}, 58),  # Alcove -> Landing Site
    ({62, 65}, None),  # Root Cave + Arbor Chamber
    ({15, 16}, None),  # Ruined Gallery
    ({52, 53}, None),  # Research Lab Aether
]


def _remove_empty(d: Any) -> Any:
    """recursively remove empty lists, empty dicts, or None elements from a dictionary"""

    def empty(x: Any) -> bool:
        return x is None or x == {} or x == []

    if not isinstance(d, dict | list):
        return d
    elif isinstance(d, list):
        return [v for v in (_remove_empty(v) for v in d) if not empty(v)]
    else:
        return {k: v for k, v in ((k, _remove_empty(v)) for k, v in d.items()) if not empty(v)}


def prime1_pickup_details_to_patcher(
    detail: pickup_exporter.ExportedPickupDetails, modal_hud_override: bool, pickup_markers: bool, rng: Random
) -> dict:
    model = detail.model.as_json
    original_model = detail.original_model.as_json

    name = detail.name
    collection_text = detail.collection_text[0]
    pickup_type = "Nothing"
    count = 0
    max_count = 0

    if detail.is_for_remote_player:
        pickup_type = "Unknown Item 1"
        count = detail.index.index + 1
        max_count = count
    else:
        for resource, quantity in detail.conditional_resources[0].resources:
            # Refill items
            if resource.extra.get("is_refill"):
                pickup_type = resource.extra.get("pickup_type", resource.long_name)
                count = quantity
                max_count = 0
                break
            # Regular items
            elif resource.extra["item_id"] < 1000:
                pickup_type = resource.long_name
                count = quantity
                max_count = count
                break

    if (
        model["name"] == "Missile"
        and not detail.is_for_remote_player
        and "Missile Expansion" in collection_text
        and rng.randint(0, _EASTER_EGG_SHINY_MISSILE) == 0
    ):
        model["name"] = "Shiny Missile"
        collection_text = collection_text.replace("Missile Expansion", "Shiny Missile Expansion")
        name = name.replace("Missile Expansion", "Shiny Missile Expansion")
        original_model = model

    result = {
        "type": pickup_type,
        "model": model,
        "original_model": original_model,
        "scanText": f"{name}. {detail.description}".strip(),
        "hudmemoText": collection_text,
        "currIncrease": count,
        "maxIncrease": max_count,
        "respawn": False,
        "showIcon": pickup_markers,
    }

    if detail.original_model.name == "UnlimitedMissiles":
        result["scale"] = [1.8, 1.8, 1.8]

    if modal_hud_override:
        result["modalHudmemo"] = True

    return result


def _create_locations_with_modal_hud_memo(pickups: list[pickup_exporter.ExportedPickupDetails]) -> set[int]:
    result = set()

    for index in _LOCATIONS_WITH_MODAL_ALERT:
        if pickups[index].is_for_remote_player:
            result.add(index)

    for indices, extra in _LOCATIONS_GROUPED_TOGETHER:
        num_other = sum(pickups[i].is_for_remote_player for i in indices)
        if extra is not None:
            num_other += pickups[extra].is_for_remote_player

        if num_other > 1:
            for index in indices:
                if pickups[index].is_for_remote_player:
                    result.add(index)

    return result


def _starting_items_value_for(
    resource_database: ResourceDatabaseView, starting_items: ResourceCollection, index: str
) -> bool | int:
    item = resource_database.get_item(index)
    value = starting_items[item]
    if item.max_capacity > 1:
        return value
    else:
        return value > 0


def _name_for_location(region_list: RegionList, location: AreaIdentifier) -> str:
    loc = location.as_tuple
    if loc in prime1_elevators.RANDOMPRIME_CUSTOM_NAMES and loc != ("Frigate Orpheon", "Exterior Docking Hangar"):
        return prime1_elevators.RANDOMPRIME_CUSTOM_NAMES[loc]
    else:
        return region_list.area_name(region_list.area_by_area_location(location), separator=":")


def _name_for_start_location(region_list: RegionList, location: NodeIdentifier) -> str:
    # small helper function as long as teleporter nodes use AreaIdentifier and starting locations use NodeIdentifier
    area_loc = location.area_identifier
    return _name_for_location(region_list, area_loc)


def _create_results_screen_text(description: LayoutDescription) -> str:
    return f"{randovania.VERSION} | Seed Hash - {description.shareable_word_hash} ({description.shareable_hash})"


def _random_factor(rng: Random, min: float, max: float, target: float) -> float:
    # return a random float between (min, max) biased towards target (up to 1 re-roll to get closer)
    a = rng.uniform(min, max)
    b = rng.uniform(min, max)
    a_diff = abs(a - target)
    b_diff = abs(b - target)
    if a_diff > b_diff:
        return a
    return b


def _pick_random_point_in_aabb(rng: Random, aabb: list, room_name: str) -> list[float]:
    if room_name == "Artifact Temple":
        center = [-373.0, 47.0, -30.0]
        scale = [65.0, 50.0, 16.0]
        return [rng.uniform(center[i] - scale[i] / 2, center[i] + scale[i] / 2) for i in range(3)]

    if room_name == "Burn Dome":
        if bool(rng.getrandbits(1)):
            # Main Room
            center = [577.5, -13.1, 34.9]
            scale = [25, 22.4, 7.4]
        else:
            # Missile Room
            center = [588.8, 39.5, 33.9]
            scale = [9, 8, 5]
        return [rng.uniform(center[i] - scale[i] / 2, center[i] + scale[i] / 2) for i in range(3)]

    # return a quasi-random point within the provided aabb, but bias towards being closer to in-bounds
    offset_xy = 0.0
    offset_max_z = 0.0

    ROOMS_THAT_NEED_HELP = [
        "Landing Site",
        "Alcove",
        "Frigate Crash Site",
        "Sunchamber",
        "Triclops Pit",
        "Elite Quarters",
        "Quarantine Cave",
        "Research Lab Hydra",
        "Research Lab Aether",
    ]

    if room_name in ROOMS_THAT_NEED_HELP:
        offset_xy = 0.1
        offset_max_z = -0.3
    x_factor = _random_factor(rng, 0.15 + offset_xy, 0.85 - offset_xy, 0.5)
    y_factor = _random_factor(rng, 0.15 + offset_xy, 0.85 - offset_xy, 0.5)
    z_factor = _random_factor(rng, 0.1, 0.8 + offset_max_z, 0.35)

    return [
        aabb[0] + (aabb[3] - aabb[0]) * x_factor,
        aabb[1] + (aabb[4] - aabb[1]) * y_factor,
        aabb[2] + (aabb[5] - aabb[2]) * z_factor,
    ]


# ruff: noqa: C901


def _serialize_dock_modifications(
    region_data: dict,
    regions: list[Region],
    room_rando_mode: RoomRandoMode,
    rng: Random,
    dock_types_to_ignore: list[DockType],
) -> None:
    if room_rando_mode == RoomRandoMode.NONE:
        return

    for region in regions:
        area_dock_nums: dict[str, list[int]] = {}
        attached_areas: dict[str, list[str]] = {}
        size_indices: dict[str, float] = {}
        candidates: list[tuple[str, int]] = []
        default_connections_node_name: dict[tuple[str, int], tuple[str, str]] = {}
        dock_num_by_area_node: dict[tuple[str, str], int] = {}
        is_nonstandard: dict[tuple[str, int], bool] = {}
        disabled_doors = set()

        # collect dock info for all areas
        for area in region.areas:
            area_dock_nums[area.name] = []
            attached_areas[area.name] = []
            dock_nodes = [node for node in area.nodes if isinstance(node, DockNode)]
            for node in dock_nodes:
                if node.dock_type in dock_types_to_ignore:
                    continue
                index: int = node.extra["dock_index"]
                dock_num_by_area_node[(area.name, node.name)] = index
                is_nonstandard[(area.name, index)] = node.extra["nonstandard"]
                default_connections_node_name[(area.name, index)] = (
                    node.default_connection.area,
                    node.default_connection.node,
                )

                if node.default_dock_weakness.name == "Permanently Locked":
                    disabled_doors.add((area.name, index))

                if node.extra["nonstandard"]:
                    continue
                area_dock_nums[area.name].append(index)
                attached_areas[area.name].append(node.default_connection.area)
                candidates.append((area.name, index))
            size_indices[area.name] = area.extra["size_index"]

        default_connections = {}
        for (src_name, src_dock), (dst_name_a, dst_node_name) in default_connections_node_name.items():
            try:
                dst_dock_a = dock_num_by_area_node[(dst_name_a, dst_node_name)]
            except KeyError:
                continue
            default_connections[(src_name, src_dock)] = (dst_name_a, dst_dock_a)

        for area_name, dock_num in candidates:
            room = region_data[region.name]["rooms"][area_name]
            if "doors" not in room:
                room["doors"] = {}

            def helper(_dock_num: int) -> None:
                dock_num_key = str(_dock_num)

                if dock_num_key not in room["doors"]:
                    room["doors"][dock_num_key] = {}

                if "destination" not in room["doors"][dock_num_key]:
                    room["doors"][dock_num_key]["destination"] = {}

            helper(dock_num)

            for dock_num in area_dock_nums[area.name]:
                helper(dock_num)

        # Shuffle order which candidates are processed
        rng.shuffle(candidates)

        used_room_pairings = []

        def are_rooms_compatible(
            src_name: str,
            src_dock: int | None,
            dst_name: str,
            dst_dock: int | None,
            mode: RoomRandoMode,
        ) -> bool:
            if src_name is None or dst_name is None:
                # print("none name")
                return False

            # both rooms must have patchable docks
            if len(area_dock_nums[src_name]) == 0 or len(area_dock_nums[dst_name]) == 0:
                # print("unpatchable room(s)")
                return False

            # destinations cannot be in the same room
            if src_name == dst_name:
                # print("same room")
                return False

            # src/dst must not be exempt
            if src_dock is not None and is_nonstandard[(src_name, src_dock)]:
                # print("src exempt")
                return False
            if dst_dock is not None and is_nonstandard[(dst_name, dst_dock)]:
                # print("dst exempt")
                return False

            # rooms cannot be neighbors
            if src_name in attached_areas[dst_name]:
                if mode == RoomRandoMode.ONE_WAY:
                    # print("neighbor")
                    return False

                # Unless it's a vanilla 2-way connection
                assert src_dock is not None
                if default_connections[(src_name, src_dock)] != (dst_name, dst_dock):
                    # print("two-way non-neighbor")
                    return False

            # rooms can only connect to another room up to once
            if {src_name, dst_name} in used_room_pairings:
                # Except for one-way in impact crater, this edge case works fine and is desireable
                if not (mode == RoomRandoMode.ONE_WAY and region.name == "Impact Crater"):
                    # print("double connection")
                    return False

            # The two rooms must not crash if drawn at the same time (size_index > 1.0)
            if size_indices[src_name] + size_indices[dst_name] >= 1.0:
                # print("too big")
                return False

            return True

        if room_rando_mode == RoomRandoMode.ONE_WAY:
            for area in region.areas:
                for dock_num in area_dock_nums[area.name]:
                    # First try each of the unused docks
                    dst_name: str | None = None
                    dst_dock: int | None = None
                    for name, dock in candidates:
                        if are_rooms_compatible(area.name, None, name, None, room_rando_mode):
                            dst_name = name
                            dst_dock = dock
                            break

                    # If that wasn't successful, pick random destinations until it works out
                    deadman_count = 1000
                    while (
                        dst_name is None
                        or dst_dock is None
                        or not are_rooms_compatible(area.name, dock_num, dst_name, dst_dock, room_rando_mode)
                    ):
                        deadman_count -= 1
                        if deadman_count == 0:
                            raise Exception(f"Failed to find suitible destination for {area.name}:{dock_num}")

                        dst_name = rng.choice(region.areas).name
                        dst_dock = None

                        if len(area_dock_nums[dst_name]) == 0:
                            continue

                        dst_dock = rng.choice(area_dock_nums[dst_name])

                    # Don't use this dock as a destination again unless there are no other options
                    try:
                        candidates.remove((dst_name, dst_dock))
                    except ValueError:
                        # print("re-used %s:%d" % (dst_name, dst_dock))
                        pass

                    used_room_pairings.append({area.name, dst_name})

                    d = region_data[region.name]["rooms"][area.name]["doors"][str(dock_num)]["destination"]
                    d["roomName"] = name
                    d["dockNum"] = dst_dock

        elif room_rando_mode == RoomRandoMode.TWO_WAY:
            # List containing:
            #   - set of len=2, each containing
            #       - tuple of len=2 for (room_name, dock)
            shuffled: list[set[tuple[str, int]]] = []

            def next_candidate(max_index: float) -> tuple[str, int] | tuple[None, None]:
                for src_name, src_dock in candidates:
                    if size_indices[src_name] > max_index:
                        return (src_name, src_dock)
                return (None, None)

            def pick_random_dst(src_name: str, src_dock: int) -> tuple[str, int] | tuple[None, None]:
                for dst_name, dst_dock in candidates:
                    if are_rooms_compatible(src_name, src_dock, dst_name, dst_dock, room_rando_mode):
                        return (dst_name, dst_dock)
                return (None, None)

            def remove_pair(shuffled_pair: set[tuple[str, int]]) -> None:
                shuffled.remove(shuffled_pair)

                sorted_pair = sorted(shuffled_pair)
                assert len(sorted_pair) == 2
                a = sorted_pair[0]
                b = sorted_pair[1]

                candidates.append(a)
                candidates.append(b)

                (a_name, a_dock) = a
                (b_name, b_dock) = b
                used_room_pairings.remove({a_name, b_name})

                region_data[region.name]["rooms"][a_name]["doors"][str(a_dock)]["destination"] = {}
                region_data[region.name]["rooms"][b_name]["doors"][str(b_dock)]["destination"] = {}

            # Randomly pick room sources, starting with the largest room first, then randomly
            # pick a compatible destination
            max_index = 1.01
            while len(candidates) != 0:
                assert len(candidates) % 2 == 0

                if max_index < -0.00001:
                    raise Exception(f"Failed to find pairings for {str(candidates)}")

                new_result = next_candidate(max_index)
                if new_result[0] is None:
                    # lower the room size criteria and try again
                    max_index -= 0.01
                    continue

                (src_name, src_dock) = new_result

                new_result = pick_random_dst(src_name, src_dock)
                if new_result[0] is None:
                    # This room have no valid destinations in the pool, randomly unpair two rooms and try again
                    remove_pair(rng.choice(shuffled))
                    continue

                (dst_name, dst_dock) = new_result
                assert {(src_name, src_dock), (dst_name, dst_dock)} not in shuffled

                candidates.remove((src_name, src_dock))
                candidates.remove((dst_name, dst_dock))
                shuffled.append({(src_name, src_dock), (dst_name, dst_dock)})
                used_room_pairings.append({src_name, dst_name})

                d = region_data[region.name]["rooms"][src_name]["doors"][str(src_dock)]["destination"]
                d["roomName"] = dst_name
                d["dockNum"] = dst_dock

                d = region_data[region.name]["rooms"][dst_name]["doors"][str(dst_dock)]["destination"]
                d["roomName"] = src_name
                d["dockNum"] = src_dock

                # print("%s:%d <--> %s:%d" % (src_name, src_dock, dst_name, dst_dock))

                # If we just finished placing all rooms, check if there are unconnected components
                # and if so, re-roll some rooms
                if len(candidates) == 0:
                    import networkx

                    # Model as networkx graph object
                    room_connections = []
                    for room_name in region_data[region.name]["rooms"]:
                        room = region_data[region.name]["rooms"][room_name]
                        if "doors" not in room:
                            continue

                        for dock_num in room["doors"]:
                            if "destination" not in room["doors"][dock_num]:
                                continue

                            if len(room["doors"][dock_num]["destination"]) == 0:
                                continue

                            if (room_name, int(dock_num)) in disabled_doors:
                                continue

                            dst_room_name = room["doors"][dock_num]["destination"]["roomName"]

                            assert {room_name, dst_room_name} in used_room_pairings

                            room_connections.append((room_name, dst_room_name))

                    # Handle unrandomized connections
                    for (src_name, src_dock), is_set in is_nonstandard.items():
                        if (src_name, src_dock) in disabled_doors:
                            continue

                        if is_set:
                            (dst_name, dst_dock) = default_connections[(src_name, src_dock)]
                            room_connections.append((src_name, dst_name))

                    # model this db's connections as a graph
                    graph = networkx.DiGraph()
                    graph.add_edges_from(room_connections)

                    if not networkx.is_strongly_connected(graph):
                        # Split graph into strongly connected components
                        strongly_connected_components = sorted(
                            networkx.strongly_connected_components(graph), key=len, reverse=True
                        )
                        assert len(strongly_connected_components) > 1

                        def component_number(name: str) -> int:
                            i = 0
                            for component in strongly_connected_components:
                                if name in list(component):
                                    return i
                                i += 1
                            raise KeyError("Name not found")

                        # randomly pick two room pairs which are not members of the same strongly connected
                        # component and # put back into pool for re-randomization (cross fingers that they
                        # connect the two strong components)
                        assert len(shuffled) > 2

                        # pick one randomly
                        rng.shuffle(shuffled)
                        a = shuffled[-1]
                        sorted_a = sorted(a)
                        (src_name_a, src_dock_a) = sorted_a[0]
                        (dst_name_a, dst_dock_a) = sorted_a[1]
                        a_component_num = component_number(src_name_a)

                        # pick a second which is not part of the same component
                        (src_name_b, src_dock_b, dst_name_b, dst_dock_b) = (None, None, None, None)
                        for b in shuffled:
                            sorted_b = sorted(b)
                            (src_name, src_dock) = sorted_b[0]
                            (dst_name, dst_dock) = sorted_b[1]
                            if component_number(src_name) == a_component_num:
                                continue
                            (src_name_b, src_dock_b, dst_name_b, dst_dock_b) = (src_name, src_dock, dst_name, dst_dock)
                            break

                        # If we could not find two rooms that were part of two different components, still
                        # remove a random room pairing (this can happen if rooms exempt from randomization
                        # are causing fractured connectivity)
                        if src_name_b is None:
                            sorted_b = sorted(shuffled[0])
                            (src_name_b, src_dock_b) = sorted_b[0]
                            (dst_name_b, dst_dock_b) = sorted_b[1]

                        assert src_dock_b is not None
                        assert dst_name_b is not None
                        assert dst_dock_b is not None

                        # put back into pool
                        remove_pair({(src_name_a, src_dock_a), (dst_name_a, dst_dock_a)})
                        remove_pair({(src_name_b, src_dock_b), (dst_name_b, dst_dock_b)})

                        # do something different this time
                        rng.shuffle(candidates)


class PrimePatchDataFactory(PatchDataFactory[PrimeConfiguration, PrimeCosmeticPatches]):
    def game_enum(self) -> RandovaniaGame:
        return RandovaniaGame.METROID_PRIME

    @override
    @classmethod
    def hint_namer_type(cls) -> type[PrimeHintNamer]:
        return PrimeHintNamer

    def get_default_game_options(self) -> dict:
        cosmetic_patches = self.cosmetic_patches
        return {
            "screenBrightness": cosmetic_patches.user_preferences.screen_brightness,
            "screenOffsetX": cosmetic_patches.user_preferences.screen_x_offset,
            "screenOffsetY": cosmetic_patches.user_preferences.screen_y_offset,
            "screenStretch": cosmetic_patches.user_preferences.screen_stretch,
            "soundMode": cosmetic_patches.user_preferences.sound_mode,
            "sfxVolume": cosmetic_patches.user_preferences.sfx_volume,
            "musicVolume": cosmetic_patches.user_preferences.music_volume,
            "visorOpacity": cosmetic_patches.user_preferences.hud_alpha,
            "helmetOpacity": cosmetic_patches.user_preferences.helmet_alpha,
            "hudLag": cosmetic_patches.user_preferences.hud_lag,
            "reverseYAxis": cosmetic_patches.user_preferences.invert_y_axis,
            "rumble": cosmetic_patches.user_preferences.rumble,
            "swapBeamControls": cosmetic_patches.user_preferences.swap_beam_controls,
        }

    def create_visual_nothing(self) -> PickupEntry:
        """The model of this pickup replaces the model of all pickups when PickupModelDataSource is ETM"""
        return pickup_creator.create_visual_nothing(self.game_enum(), "Nothing")

    def create_game_specific_data(self, randovania_meta: PatcherDataMeta) -> dict:
        # Setup
        db = self.game
        namer = PrimeHintNamer(self.description.all_patches, self.players_config)

        ammo_with_mains = [
            ammo.name
            for ammo, state in self.configuration.ammo_pickup_configuration.pickups_state.items()
            if state.requires_main_item
        ]
        if ammo_with_mains:
            raise ValueError(
                "Preset has {} with required mains enabled. This is currently not supported.".format(
                    " and ".join(ammo_with_mains)
                )
            )

        scan_visor = self.game.resource_database.get_item_by_display_name("Scan Visor")
        pickup_list = self.export_pickup_list()
        modal_hud_override = _create_locations_with_modal_hud_memo(pickup_list)
        regions = [region for region in db.region_list.regions if region.name != "End of Game"]
        elevator_dock_types = self.game.dock_weakness_database.all_teleporter_dock_types

        # Initialize serialized db data
        level_data: dict = {}

        for region in regions:
            level_data[region.name] = {
                "transports": {},
                "rooms": {},
            }

            for area in region.areas:
                level_data[region.name]["rooms"][area.name] = {
                    "pickups": [],
                    "doors": {},
                }

        # serialize elevator modifications
        for region in regions:
            for area in region.areas:
                for node in area.nodes:
                    if not isinstance(node, DockNode):
                        continue
                    if node.dock_type not in elevator_dock_types:
                        continue

                    identifier = node.identifier.area_identifier
                    target = _name_for_location(
                        db.region_list, self.patches.get_dock_connection_for(node).identifier.area_identifier
                    )

                    source_name = prime1_elevators.RANDOMPRIME_CUSTOM_NAMES[
                        (
                            identifier.region,
                            identifier.area,
                        )
                    ]
                    level_data[region.name]["transports"][source_name] = target

        # serialize pickup modifications
        for region in regions:
            for area in region.areas:
                pickup_nodes_gen = (node for node in area.nodes if isinstance(node, PickupNode))
                pickup_nodes = sorted(pickup_nodes_gen, key=lambda n: n.pickup_index)
                for node in pickup_nodes:
                    pickup_index = node.pickup_index.index
                    pickup = prime1_pickup_details_to_patcher(
                        pickup_list[pickup_index],
                        pickup_index in modal_hud_override,
                        self.cosmetic_patches.pickup_markers,
                        self.rng,
                    )

                    if self.configuration.shuffle_item_pos or node.extra.get("position_required"):
                        aabb = area.extra["aabb"]
                        pickup["position"] = _pick_random_point_in_aabb(self.rng, aabb, area.name)
                        pickup["jumboScan"] = True

                    level_data[region.name]["rooms"][area.name]["pickups"].append(pickup)

        if self.configuration.shuffle_item_pos:
            # Allow temple cutscene without collecting item
            level_data["Tallon Overworld"]["rooms"]["Artifact Temple"]["triggers"] = [
                {
                    "id": 0x00100470,
                    "active": True,
                }
            ]

        # Remove Bars in Great Tree Hall
        if self.configuration.remove_bars_great_tree_hall:
            level_data["Tallon Overworld"]["rooms"]["Great Tree Hall"]["deleteIds"] = [
                2359733,  # 0x002401B5 - bar
                2359744,  # 0x002401C0 - spinner auto-enable timer
                2359830,  # 0x00240216 - scan front
                2359829,  # 0x00240215 - scan back
            ]

        # serialize room modifications
        if self.configuration.superheated_probability != 0:
            probability = self.configuration.superheated_probability / 1000.0
            for region in regions:
                for area in region.areas:
                    level_data[region.name]["rooms"][area.name]["superheated"] = self.rng.random() < probability

        if self.configuration.submerged_probability != 0:
            probability = self.configuration.submerged_probability / 1000.0
            for region in regions:
                for area in region.areas:
                    level_data[region.name]["rooms"][area.name]["submerge"] = self.rng.random() < probability

        # Replace vanilla missile blast shields with the new ones
        if not self.configuration.legacy_mode:
            for node in db.region_list.iterate_nodes_of_type(DockNode):
                if node.dock_type not in elevator_dock_types:
                    if node.default_dock_weakness.name != "Missile Blast Shield (randomprime)":
                        continue

                    dock_num = str(node.extra["dock_index"])
                    world_name = node.identifier.region
                    room_name = node.identifier.area
                    doors_in_node = level_data[world_name]["rooms"][room_name]["doors"]

                    if dock_num not in doors_in_node:
                        doors_in_node[dock_num] = {}

                    doors_in_node[dock_num]["shieldType"] = node.default_dock_weakness.extra["shieldType"]
                    doors_in_node[dock_num]["blastShieldType"] = node.default_dock_weakness.extra["blastShieldType"]

        # serialize door modifications
        for region in regions:
            for area in region.areas:
                dock_nodes: list[DockNode] = sorted(
                    (
                        node
                        for node in area.nodes
                        if isinstance(node, DockNode) and node.dock_type not in elevator_dock_types
                    ),
                    key=lambda n: n.extra["dock_index"],
                )
                for node in dock_nodes:
                    if self.patches.has_default_weakness(node):
                        continue

                    weakness = self.patches.get_dock_weakness_for(node)
                    dock_index = node.extra["dock_index"]
                    dock_data = {
                        "shieldType": weakness.extra["shieldType"],
                        "blastShieldType": weakness.extra.get("blastShieldType", "None"),
                    }

                    level_data[region.name]["rooms"][area.name]["doors"][str(dock_index)] = dock_data

        # serialize dock destination modifications
        dock_types_to_ignore = self.game.dock_weakness_database.all_teleporter_dock_types
        _serialize_dock_modifications(
            level_data, regions, self.configuration.room_rando, self.rng, dock_types_to_ignore
        )

        # serialize text modifications
        if self.configuration.hints.specific_pickup_hints["phazon_suit"] != SpecificPickupHintMode.DISABLED:
            try:
                phazon_suit_resource_info = self.game.resource_database.get_item_by_display_name("Phazon Suit")

                hint_texts: dict[ItemResourceInfo, str] = guaranteed_item_hint.create_guaranteed_hints_for_resources(
                    self.description.all_patches,
                    self.players_config,
                    namer,
                    self.configuration.hints.specific_pickup_hints["phazon_suit"] == SpecificPickupHintMode.HIDE_AREA,
                    [phazon_suit_resource_info],
                    True,
                )

                phazon_hint_text = hint_texts[phazon_suit_resource_info]

                if "Impact Crater" not in level_data:
                    level_data["Impact Crater"] = {
                        "transports": {},
                        "rooms": {},
                    }

                if "Crater Entry Point" not in level_data["Impact Crater"]["rooms"]:
                    level_data["Impact Crater"]["rooms"]["Crater Entry Point"] = {"pickups": [], "doors": {}}

                level_data["Impact Crater"]["rooms"]["Crater Entry Point"]["extraScans"] = [
                    {
                        "position": [-19.4009, 41.001, 2.805],
                        "combatVisible": True,
                        "text": phazon_hint_text,
                        "rotation": 45.0,
                        "isRed": True,
                        "logbookTitle": "Phazon Suit",
                        "logbookCategory": 5,  # Artifacts
                    }
                ]
            except ValueError:
                pass  # Skip making the hint if Phazon Suit is not in the seed

        # strip extraneous info
        level_data = _remove_empty(level_data)
        for region_item in level_data.values():
            if "rooms" not in region_item:
                region_item["rooms"] = {}

        extra_starting = item_names.additional_starting_equipment(self.configuration, db, self.patches)
        if extra_starting:
            starting_memo = ", ".join(extra_starting)
        else:
            starting_memo = None

        if self.cosmetic_patches.open_map:
            map_default_state = "Always"
        else:
            map_default_state = "MapStationOrVisit"

        credits_string = credits_spoiler.prime_trilogy_credits(
            self.configuration.standard_pickup_configuration,
            self.description.all_patches,
            self.players_config,
            namer,
            "&push;&font=C29C51F1;&main-color=#89D6FF;Major Item Locations&pop;",
            "&push;&font=C29C51F1;&main-color=#33ffd6;{}&pop;",
        )

        artifacts = [db.resource_database.get_item(index) for index in prime_items.ARTIFACT_ITEMS]
        hint_config = self.configuration.hints
        if hint_config.specific_pickup_hints["artifacts"] == SpecificPickupHintMode.DISABLED:
            resulting_hints = {art: f"{art.long_name} is lost somewhere on Tallon IV." for art in artifacts}
        else:
            resulting_hints = guaranteed_item_hint.create_guaranteed_hints_for_resources(
                self.description.all_patches,
                self.players_config,
                namer,
                hint_config.specific_pickup_hints["artifacts"] == SpecificPickupHintMode.HIDE_AREA,
                [db.resource_database.get_item(index) for index in prime_items.ARTIFACT_ITEMS],
                True,
            )

        # Tweaks
        ctwk_config: JsonObject = {}
        if self.configuration.small_samus:
            ctwk_config["playerSize"] = 0.3
            ctwk_config["morphBallSize"] = 0.3
            ctwk_config["easyLavaEscape"] = True

        if self.configuration.large_samus:
            ctwk_config["playerSize"] = 1.75

        if self.cosmetic_patches.use_hud_color:
            ctwk_config["hudColor"] = [
                self.cosmetic_patches.hud_color[0] / 255,
                self.cosmetic_patches.hud_color[1] / 255,
                self.cosmetic_patches.hud_color[2] / 255,
            ]

        SUIT_ATTRIBUTES = ["powerDeg", "variaDeg", "gravityDeg", "phazonDeg"]
        suit_colors = {}
        for attribute, hue_rotation in zip(SUIT_ATTRIBUTES, self.cosmetic_patches.suit_color_rotations):
            if hue_rotation != 0:
                suit_colors[attribute] = hue_rotation

        starting_room = _name_for_start_location(db.region_list, self.patches.starting_location)

        starting_resources = self.patches.starting_resources()
        starting_items = {
            name: _starting_items_value_for(db.resource_database, starting_resources, index)
            for name, index in _STARTING_ITEM_NAME_TO_INDEX.items()
        }

        if not self.configuration.legacy_mode:
            idrone_config = {
                "eyeWaitInitialRandomTime": 0.0,
                "eyeWaitRandomTime": 0.0,
                "eyeStayUpRandomTime": 0.0,
                "resetContraptionRandomTime": 0.0,
                # ~~~ Justification for Divide by 2 ~~~
                # These Timer RNG values are normally re-rolled inbetween each of the 4 phases,
                # turning the zoid fight duration probability into a bell curve. With /2 we manipulate
                # the (now linear) probability characteristic to more often generate "average zoid fights"
                # while erring on the side of faster.
                "eyeWaitInitialMinimumTime": 8.0 + self.rng.random() * 5.0 / 2.0,
                "eyeWaitMinimumTime": 15.0 + self.rng.random() * 10.0 / 2.0,
                "eyeStayUpMinimumTime": 8.0 + self.rng.random() * 3.0 / 2.0,
                "resetContraptionMinimumTime": 3.0 + self.rng.random() * 3.0 / 2.0,
            }
        else:
            idrone_config = None

        if not self.configuration.legacy_mode:
            maze_seeds = [self.rng.choice(VANILLA_MAZE_SEEDS)]
        else:
            maze_seeds = None

        if self.configuration.legacy_mode:
            qol_cutscenes = LayoutCutsceneMode.ORIGINAL.value
        else:
            qol_cutscenes = self.configuration.qol_cutscenes.value

        random_enemy_sizes = False
        if self.configuration.enemy_attributes is not None:
            random_enemy_sizes = (
                self.configuration.enemy_attributes.enemy_rando_range_scale_low != 1.0
                or self.configuration.enemy_attributes.enemy_rando_range_scale_low != 1.0
            )

        if self.configuration.random_boss_sizes and not random_enemy_sizes:

            def get_random_size(minimum: float, maximum: float) -> float:
                if self.rng.choice([True, False]):
                    temp = [self.rng.uniform(minimum, 1.0), self.rng.uniform(minimum, 1.0)]
                    return min(temp)
                else:
                    temp = [self.rng.uniform(1.0, maximum), self.rng.uniform(1.0, maximum)]
                    return max(temp)

            boss_sizes = {
                "parasiteQueen": get_random_size(0.1, 3.0),
                "incineratorDrone": get_random_size(0.2, 3.0),
                "adultSheegoth": get_random_size(0.1, 1.5),
                "thardus": get_random_size(0.05, 2.0),
                "elitePirate1": get_random_size(0.05, 2.3),
                "elitePirate2": get_random_size(0.05, 1.3),
                "elitePirate3": get_random_size(0.05, 2.0),
                "phazonElite": get_random_size(0.1, 2.0),
                "omegaPirate": get_random_size(0.05, 2.0),
                "ridley": get_random_size(0.2, 1.5),
                "exo": get_random_size(0.15, 2.0),
                "essence": get_random_size(0.05, 2.25),
                "flaahgra": get_random_size(0.15, 3.3),
                "platedBeetle": get_random_size(0.05, 6.0),
                "cloakedDrone": get_random_size(0.05, 6.0),
            }
        else:
            boss_sizes = {}

        if self.configuration.spring_ball:
            spring_ball_item = "Morph Ball Bomb"
        else:
            spring_ball_item = "Spring Ball"

        data: dict = {
            "$schema": "https://randovania.github.io/randomprime/randomprime.schema.json",
            "seed": self.description.get_seed_for_world(self.players_config.player_index),
            "preferences": {
                "defaultGameOptions": self.get_default_game_options(),
                "qolGameBreaking": not self.configuration.legacy_mode,
                "qolCosmetic": not self.configuration.legacy_mode,
                "qolPickupScans": not self.configuration.legacy_mode,
                "qolGeneral": not self.configuration.legacy_mode,
                "qolCutscenes": qol_cutscenes,
                "mapDefaultState": map_default_state,
                "artifactHintBehavior": "All",
                "automaticCrashScreen": True,
                # "trilogyDiscPath": None,
                "quickplay": False,
                "quiet": False,
                "suitColors": suit_colors,
                "forceFusion": self.cosmetic_patches.force_fusion,
            },
            "gameConfig": {
                "resultsString": _create_results_screen_text(self.description),
                "bossSizes": boss_sizes,
                "noDoors": self.configuration.no_doors,
                "startingRoom": starting_room,
                "warpToStart": self.configuration.warp_to_start,
                "springBallItem": spring_ball_item,
                "incineratorDroneConfig": idrone_config,
                "mazeSeeds": maze_seeds,
                "nonvariaHeatDamage": not self.configuration.legacy_mode,
                "missileStationPbRefill": not self.configuration.legacy_mode,
                "staggeredSuitDamage": self.configuration.damage_reduction.value,
                "heatDamagePerSec": self.configuration.heat_damage,
                "autoEnabledElevators": not starting_resources.has_resource(scan_visor),
                "multiworldDolPatches": True,
                "doorOpenMode": "PrimaryBlastShield",
                "difficultyBehavior": self.configuration.ingame_difficulty.randomprime_value,
                "blastShieldLockon": self.configuration.blast_shield_lockon,
                "disableItemLoss": True,  # Item Loss in Frigate
                "startingItems": starting_items,
                "etankCapacity": self.configuration.energy_per_tank,
                "itemMaxCapacity": {
                    "Energy Tank": db.resource_database.get_item("EnergyTank").max_capacity,
                    "Power Bomb": db.resource_database.get_item("PowerBomb").max_capacity,
                    "Missile": db.resource_database.get_item("Missile").max_capacity,
                    "Unknown Item 1": db.resource_database.get_item(prime_items.MULTIWORLD_ITEM).max_capacity,
                },
                "mainPlazaDoor": self.configuration.main_plaza_door,
                "backwardsFrigate": self.configuration.backwards_frigate,
                "backwardsLabs": self.configuration.backwards_labs,
                "backwardsUpperMines": self.configuration.backwards_upper_mines,
                "backwardsLowerMines": self.configuration.backwards_lower_mines,
                "phazonEliteWithoutDynamo": self.configuration.phazon_elite_without_dynamo,
                "gameBanner": {
                    "gameName": "Metroid Prime: Randomizer",
                    "gameNameFull": f"Metroid Prime: Randomizer - {self.description.shareable_hash}",
                    "description": f"Seed Hash: {self.description.shareable_word_hash}",
                },
                "mainMenuMessage": f"Randovania v{randovania.VERSION}\n{self.description.shareable_word_hash}",
                "creditsString": credits_string,
                "artifactHints": {artifact.long_name: text for artifact, text in resulting_hints.items()},
                "artifactTempleLayerOverrides": {
                    artifact.long_name: not starting_resources.has_resource(artifact) for artifact in artifacts
                },
                "requiredArtifactCount": (12 - self.configuration.artifact_target.value)
                + self.configuration.artifact_required.value,
            },
            "tweaks": ctwk_config,
            "levelData": level_data,
            "hasSpoiler": not randovania_meta["in_race_setting"],
            "roomRandoMode": self.configuration.room_rando.value,
            "randEnemyAttributes": (
                self.configuration.enemy_attributes.as_json if self.configuration.enemy_attributes is not None else None
            ),
            "uuid": list(
                self.players_config.get_own_uuid().bytes,
            ),
        }

        if starting_memo:
            data["gameConfig"]["startingMemo"] = starting_memo

        return data
