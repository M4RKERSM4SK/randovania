from __future__ import annotations

import dataclasses

from randovania.game.game_enum import RandovaniaGame
from randovania.games.prime1.layout.prime_user_preferences import PrimeUserPreferences
from randovania.layout.base.cosmetic_patches import BaseCosmeticPatches

DEFAULT_HUD_COLOR = (102, 174, 225)


@dataclasses.dataclass(frozen=True)
class PrimeCosmeticPatches(BaseCosmeticPatches):
    open_map: bool = True
    pickup_markers: bool = True
    force_fusion: bool = False
    use_hud_color: bool = False
    hud_color: tuple[int, int, int] = DEFAULT_HUD_COLOR
    suit_color_rotations: tuple[int, int, int, int] = (0, 0, 0, 0)
    user_preferences: PrimeUserPreferences = dataclasses.field(default_factory=PrimeUserPreferences)

    @classmethod
    def default(cls) -> PrimeCosmeticPatches:
        return cls()

    @classmethod
    def game(cls) -> RandovaniaGame:
        return RandovaniaGame.METROID_PRIME

    def __post_init__(self) -> None:
        if len(self.suit_color_rotations) != 4:
            raise ValueError("Suit color rotations must be a tuple of 4 ints.")
        if len(self.hud_color) != 3:
            raise ValueError("HUD color must be a tuple of 3 ints.")
