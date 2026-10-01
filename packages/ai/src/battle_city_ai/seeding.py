"""Deriving a bot's private random stream from the session seed.

Why a bot cannot simply use ``state.rng``
-----------------------------------------
``battle_city_sim.step`` never advances ``state.rng``: it copies the generator from the
pre-tick state into the post-tick state untouched. Drawing from ``state.rng`` every tick
would therefore hand every bot the same value on every tick forever. The simulation's
contract is that a draw returns *both* a value and its successor, and whoever draws owns
the successor. Bots are outside the rules engine, so a bot owns its successor itself:
the stream lives in :class:`battle_city_ai.policy.BotMemory` and is threaded from tick to
tick alongside the rest of the bot's state.

The stream is still the simulation's generator. :class:`battle_city_sim.rng.Rng` is the
project's seeded, versioned SplitMix64 value, and nothing here implements an algorithm of
its own; derivation only chooses a *starting position* in that algorithm.

Why the stream is derived per bot
---------------------------------
Two bots created from one session seed must not draw the same numbers, or an aggression
roll for one would be perfectly correlated with the other's. The seed is therefore folded
with the bot's tank identifier and its profile name, so every ``(session, tank, profile)``
triple gets an independent position while the whole thing stays a pure function of the
session seed. Nothing consults a clock, a process identifier, or the environment.

Changing anything in this module changes every bot decision for an existing seed, so it
is a replay-compatibility change. :data:`BOT_SEEDING_VERSION` records the current scheme.
"""

from __future__ import annotations

from typing import Final

from battle_city_sim.rng import Rng

BOT_SEEDING_VERSION: Final[int] = 1
"""Version of the derivation below. Bump it only alongside a replay-compatibility note."""

BOT_RNG_DOMAIN: Final[int] = 0x42_4F_54_5F_41_49_5F_31
"""Domain separator (ASCII ``BOT_AI_1``) folded in before anything else.

It keeps bot draws from ever colliding with a future simulation-side stream derived from
the same session seed, even if that stream is derived with the same folding step.
"""

_MASK64: Final[int] = (1 << 64) - 1
_FNV_OFFSET: Final[int] = 0xCBF29CE484222325
_FNV_PRIME: Final[int] = 0x100000001B3


def profile_code(name: str) -> int:
    """Return a stable 64-bit code for a profile name.

    FNV-1a over the UTF-8 bytes, not :func:`hash`. The built-in ``hash`` of a string is
    randomised per process by ``PYTHONHASHSEED``, so using it would make a bot's
    behaviour depend on how the interpreter was started, which is exactly the kind of
    hidden input the determinism contract forbids.
    """
    digest = _FNV_OFFSET
    for byte in name.encode("utf-8"):
        digest = ((digest ^ byte) * _FNV_PRIME) & _MASK64
    return digest


def fold(stream: Rng, ingredient: int) -> Rng:
    """Return a new stream position mixing ``ingredient`` into ``stream``.

    One SplitMix64 output round does the mixing, so avalanche behaviour is the
    simulation's, not a hand-rolled one. The drawn value becomes the new position rather
    than being returned, because derivation produces a starting point and never a usable
    random value.
    """
    draw, _ = Rng.from_seed(stream.state ^ (ingredient & _MASK64)).next_u64()
    return Rng(state=draw)


def derive_bot_rng(*, seed: int, tank_id: int, profile_name: str) -> Rng:
    """Return the private stream for one bot, derived once when the bot is created.

    The result is a pure function of the three arguments. Callers derive it at bot
    construction and then thread the successor stream through every tick; re-deriving it
    mid-run would restart the sequence and silently replay old draws.
    """
    stream = Rng.from_seed(seed)
    stream = fold(stream, BOT_RNG_DOMAIN)
    stream = fold(stream, tank_id)
    return fold(stream, profile_code(profile_name))
