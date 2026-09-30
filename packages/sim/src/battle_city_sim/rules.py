"""Tunable rule constants for one simulation run.

A :class:`Rules` value is part of a session's contract: the client, the authoritative
server, and any bot must step with the same value or they will diverge. Rules are data,
never globals, so a mode can supply its own values without forking the rules engine.

Defaults reproduce the historical runtime's numbers, which were read off
``duckycodess/Battle-City`` at revision ``5c9d81cd0de89a05f5946448d19c40fb343b0a2d``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Rules:
    """Fixed integer constants governing one run."""

    tile_size: int = 16
    """Pixel size of one terrain cell. The historical stages are 16x16 cells of 16px."""

    tank_size: int = 16
    """Pixel size of a tank body. Historical ``Tank.width``/``height``."""

    tank_speed: int = 2
    """Pixels a tank advances per tick. Historical ``Tank.speed``."""

    projectile_speed: int = 3
    """Pixels a projectile advances per tick, applied exactly once per tick.

    The historical runtime advanced each bullet twice per frame: once in
    ``Bullets.update`` and again when ``check_bullet_block_collission`` re-appended the
    bullet at ``(bx + vx, by + vy)``. That double advance is an accident of the update
    order, not a designed 6px projectile, so the rebuild advances once per tick.
    """

    projectile_radius: int = 1
    """Half-extent of a projectile's collision box. Historical ``Bullets.r``.

    The projectile's stored position is a point used for terrain lookup; entity
    collisions inflate it to a ``2 * radius + 1`` square centred on that point.
    """

    muzzle_inset: int = 3
    """Distance from the firing edge of the tank body to the spawned projectile."""

    muzzle_offset: int = 7
    """Distance along the tank's other axis to the spawned projectile.

    The historical ``Bullets.fire`` used 7 for UP, LEFT and RIGHT but 8 for DOWN. The
    rebuild uses 7 everywhere; the lone 8 is treated as a typo, not a rule.
    """

    starting_lives: int = 3
    """Lives each player slot begins with. Historical ``BattleCity.lives``."""

    max_projectiles_per_tank: int = 1
    """Live projectiles one tank may own before its fire command is refused."""

    gatling_interval_ticks: int = 5
    """Gatling mode fires automatically on ticks divisible by this value."""

    powerup_duration_ticks: int = 300
    """Ticks that gatling and invincibility last. Historical ``activate_*(300)``."""

    extra_life_amount: int = 1
    """Lives granted by an extra-life pickup."""

    score_shield_break: int = 50
    """Points reported when a player shot strips a shielded enemy's shield."""

    score_normal_kill: int = 100
    """Points reported when a player shot destroys a normal enemy."""

    score_unshielded_kill: int = 200
    """Points reported when a player shot destroys an unshielded enemy."""

    def __post_init__(self) -> None:
        for name in (
            "tile_size",
            "tank_size",
            "tank_speed",
            "projectile_speed",
            "gatling_interval_ticks",
            "powerup_duration_ticks",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"rules.{name} must be positive")
        if self.projectile_radius < 0:
            raise ValueError("rules.projectile_radius must not be negative")
        if self.max_projectiles_per_tank <= 0:
            raise ValueError("rules.max_projectiles_per_tank must be positive")
        if self.starting_lives <= 0:
            raise ValueError("rules.starting_lives must be positive")
        if self.projectile_speed >= self.tile_size:
            # A projectile that outruns a tile would skip terrain without resolving it.
            raise ValueError("rules.projectile_speed must be smaller than rules.tile_size")


DEFAULT_RULES: Final[Rules] = Rules()
"""The classic ruleset. Modes override fields instead of mutating this value."""
