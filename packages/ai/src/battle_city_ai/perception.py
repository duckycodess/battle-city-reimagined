"""Pure questions a bot asks about a simulation snapshot.

Every function here is a pure function of a :class:`~battle_city_sim.state.SimulationState`
and a :class:`~battle_city_sim.rules.Rules`. Nothing is cached, nothing is stored on the
state, and nothing mutates: a bot, a test, and a server all get the same answer from the
same snapshot, which is what makes a bot's decision replayable.

Fidelity, and where it stops
----------------------------
Movement and shot prediction reproduce the rules engine's arithmetic rather than
approximating it with grid-level reasoning, because the engine works in pixels: a tank is
a ``rules.tank_size`` (16px) body that advances ``rules.tank_speed`` (2px) per tick, so it
spends most of its life straddling two tiles. A tile-resolution bot would believe in moves
the engine refuses. :func:`predicted_pose` therefore mirrors ``step._phase_move`` down to
the clamp at the world edge, and :func:`shot_target_id` walks a projectile at
``rules.projectile_speed`` from the real muzzle pixel.

Gimmick terrain is predicted, not planned around
------------------------------------------------
:func:`predicted_pose` mirrors the whole of ``step._phase_move``, which since the
accepted ``gimmicks-v1`` change includes a conveyor push and a teleport arrival. It reads
the engine's own arithmetic out of :mod:`battle_city_sim.gimmicks` rather than
reimplementing it, because a second copy of the displacement rules is one edit away from
a bot that believes in moves the engine refuses.

Both additions are guarded by single cell lookups that no classic tile satisfies, so a
grid with no gimmick terrain costs one dictionary miss per call and produces byte-identical
decisions. What this does *not* add is a route planner: the policy still roams and
measures clearance, so a bot rides a belt it happens to be standing on and declines an
arrival whose destination is occupied, but it does not go looking for a teleport.

Two limits are deliberate and are what keep per-tick work bounded:

* Prediction assumes every *other* entity holds its pose. Tracing the full cross product
  of possible futures is neither bounded nor more honest, since the other entities are
  themselves reacting.
* A shot that enters a mirror tile is reported as reaching nothing. The deflection is
  knowable, but a bot that followed it would be claiming a geometric line of fire that
  does not exist, and chaining deflections has no bounded answer. Refusing the shot is
  the conservative direction: the bot declines a shot it might have made, never claims
  one it cannot.

Concealment
-----------
Forest concealment is presentation-only in :mod:`battle_city_sim.visibility`, which says
in as many words that giving it a gameplay effect needs a proposal stating the AI
consequences. So perception here sees concealed tanks exactly like any other tank. Hiding
a target from a bot would be inventing that rule, which this package is not entitled to do.
"""

from __future__ import annotations

from dataclasses import replace

from battle_city_sim import (
    DEFAULT_RULES,
    DIRECTION_ORDER,
    Direction,
    Faction,
    GridPos,
    Projectile,
    Rect,
    Rules,
    SimulationState,
    Tank,
    Vec2,
    blocks_tank,
    passes_projectile,
)
from battle_city_sim.geometry import cell_of, cells_overlapping, clamp
from battle_city_sim.gimmicks import (
    centre_cell,
    conveyor_target,
    teleport_pads,
    teleport_target,
)
from battle_city_sim.tiles import MIRROR_REFLECTIONS, Tile

# ``clamp``, ``cell_of`` and ``cells_overlapping`` are not re-exported from the simulation
# package root. They are imported from their defining modules rather than reimplemented
# here: a local copy of the engine's clamp would be one edit away from predicting moves
# the engine does not make, and the shared-contract rule forbids this issue from editing
# the simulation's public surface to add the re-export.


def manhattan(first: Vec2, second: Vec2) -> int:
    """Return the Manhattan distance between two pixel coordinates."""
    return abs(first.x - second.x) + abs(first.y - second.y)


def hostiles_of(state: SimulationState, tank: Tank) -> tuple[Tank, ...]:
    """Return every tank of the opposing faction, in ascending identifier order.

    Concealed tanks are included; see the module note on forest concealment.
    """
    return tuple(
        other
        for other in sorted(state.tanks, key=lambda item: item.entity_id)
        if other.faction is not tank.faction
    )


def muzzle_of(tank: Tank, rules: Rules = DEFAULT_RULES) -> Vec2:
    """Return the pixel a projectile leaving ``tank`` would start on.

    Mirrors ``step._muzzle``. The offsets matter: a shot leaves the body
    ``rules.muzzle_offset`` pixels off the body's centre line, which is the whole reason
    :data:`~battle_city_ai.profiles.MAX_HITTING_OFFSET_PX` is asymmetric about zero.
    """
    x, y = tank.position.x, tank.position.y
    inset = rules.muzzle_inset
    offset = rules.muzzle_offset
    size = rules.tank_size
    match tank.facing:
        case Direction.UP:
            return Vec2(x + offset, y + inset)
        case Direction.DOWN:
            return Vec2(x + offset, y + size - inset)
        case Direction.LEFT:
            return Vec2(x + inset, y + offset)
        case Direction.RIGHT:
            return Vec2(x + size - inset, y + offset)


def body_is_blocked(
    state: SimulationState,
    tank_id: int,
    body: Rect,
    rules: Rules = DEFAULT_RULES,
) -> bool:
    """Return whether ``body`` would overlap blocking terrain or another tank.

    Mirrors ``step._blocked``, including the half-open rectangle convention, so two
    tile-aligned bodies that merely touch do not count as overlapping.
    """
    for cell in cells_overlapping(body, rules.tile_size):
        if not state.grid.contains(cell) or blocks_tank(state.grid.at(cell)):
            return True
    for other in state.tanks:
        if other.entity_id == tank_id:
            continue
        if body.overlaps(other.body(rules)):
            return True
    return False


def predicted_pose(
    state: SimulationState,
    tank: Tank,
    direction: Direction | None,
    rules: Rules = DEFAULT_RULES,
) -> Tank:
    """Return the pose ``tank`` would hold after the movement phase.

    ``None`` means no command. That is not the same as "no movement": a tank standing on
    a conveyor is pushed whether or not it asked to go anywhere, so a bot that treated
    ``None`` as "stay put" would aim from a pose it will not be holding.

    A ``MoveCommand`` always turns the tank and only sometimes advances it, so the
    returned facing is ``direction`` even when the step is refused. Neither a conveyor
    push nor a teleport arrival changes the facing. The fire phase runs after the move
    phase in the same tick, which is why a bot must aim from this pose and not from the
    pre-move one.

    The three displacements are applied in the engine's own order -- command, push,
    arrival -- against the other entities as they stand now; see the module note on what
    prediction assumes about them.
    """
    start_cell = centre_cell(tank.position, rules)
    pose = tank if direction is None else _commanded_pose(state, tank, direction, rules)
    pose = _pushed_pose(state, pose, start_cell, rules)
    return _arrived_pose(state, pose, start_cell, rules)


def _commanded_pose(state: SimulationState, tank: Tank, direction: Direction, rules: Rules) -> Tank:
    """Mirror ``step._apply_command``: always turn, advance only when the step is free."""
    turned = replace(tank, facing=direction)
    dx, dy = direction.scaled(rules.tank_speed)
    width, height = state.world_size(rules)
    target = Vec2(
        clamp(turned.position.x + dx, 0, width - rules.tank_size),
        clamp(turned.position.y + dy, 0, height - rules.tank_size),
    )
    return _moved_if_free(state, turned, target, rules)


def _pushed_pose(state: SimulationState, pose: Tank, start_cell: GridPos, rules: Rules) -> Tank:
    """Mirror ``step._apply_conveyor``: one push from the cell the phase started on."""
    target = conveyor_target(state.grid, pose.position, start_cell, state.world_size(rules), rules)
    if target is None:
        return pose
    return _moved_if_free(state, pose, target, rules)


def _arrived_pose(state: SimulationState, pose: Tank, start_cell: GridPos, rules: Rules) -> Tank:
    """Mirror ``step._apply_teleport``: one jump when the phase ends on a pad it entered.

    The pad-pair scan is behind an ``is Tile.TELEPORT_PAD`` test that no classic tile
    passes, so a grid without pads never walks its 256 cells.
    """
    cell = centre_cell(pose.position, rules)
    if cell == start_cell or not state.grid.contains(cell):
        return pose
    if state.grid.at(cell) is not Tile.TELEPORT_PAD:
        return pose
    pads = teleport_pads(state.grid)
    if pads is None:
        return pose
    target = teleport_target(pose.position, cell, pads, rules)
    if target is None:
        return pose
    return _moved_if_free(state, pose, target, rules)


def _moved_if_free(state: SimulationState, pose: Tank, target: Vec2, rules: Rules) -> Tank:
    """Return ``pose`` at ``target``, or unchanged when the engine would refuse it.

    "Moves nothing" counts as refused, matching the engine: that is what a displacement
    clamped against the world edge comes out as.
    """
    if target == pose.position:
        return pose
    body = Rect(target.x, target.y, rules.tank_size, rules.tank_size)
    if body_is_blocked(state, pose.entity_id, body, rules):
        return pose
    return replace(pose, position=target)


def clearance_ticks(
    state: SimulationState,
    tank: Tank,
    direction: Direction,
    horizon: int,
    rules: Rules = DEFAULT_RULES,
) -> int:
    """Return how many of the next ``horizon`` ticks ``tank`` could advance in ``direction``.

    Counting stops at the first tick that makes no progress *along* ``direction``, so the
    result is "free run length", not "free ticks somewhere ahead". ``horizon`` is the
    profile's planning horizon and is what bounds the work: the probe is at most
    ``horizon`` collision tests.

    Progress rather than "moved at all", because terrain can now move a tank that did not
    get where it asked to go. A tank whose commanded step is refused by a wall while a
    belt shoves it the other way has *moved*, and counting that as clearance would let the
    bot commit a whole plan to a direction it is being carried away from. A belt pushing
    the way the bot is driving counts double, which is also true.

    On a grid with no gimmick terrain the two readings are the same measurement: the
    commanded step is the only displacement there is, and it either advances along
    ``direction`` or leaves the tank exactly where it was.
    """
    step_x, step_y = direction.delta
    pose = tank
    free = 0
    for _ in range(horizon):
        advanced = predicted_pose(state, pose, direction, rules)
        progress = (advanced.position.x - pose.position.x) * step_x + (
            advanced.position.y - pose.position.y
        ) * step_y
        if progress <= 0:
            return free
        pose = advanced
        free += 1
    return free


def aim_candidates(
    shooter: Tank,
    target: Tank,
    tolerance_px: int,
) -> tuple[Direction, ...]:
    """Return the facings from which ``shooter`` is lined up on ``target``.

    A facing qualifies when the perpendicular offset between the two bodies is within
    ``tolerance_px`` and the target is actually on that side. Results are ordered by
    tighter alignment first and then by :data:`~battle_city_sim.geometry.DIRECTION_ORDER`,
    so a bot that is lined up on both axes always picks the same one.

    Alignment alone is not a shot: the caller still has to confirm the line is clear with
    :func:`shot_target_id`.
    """
    dx = target.position.x - shooter.position.x
    dy = target.position.y - shooter.position.y
    scored: list[tuple[int, int, Direction]] = []
    if abs(dy) <= tolerance_px and dx != 0:
        horizontal = Direction.RIGHT if dx > 0 else Direction.LEFT
        scored.append((abs(dy), DIRECTION_ORDER.index(horizontal), horizontal))
    if abs(dx) <= tolerance_px and dy != 0:
        vertical = Direction.DOWN if dy > 0 else Direction.UP
        scored.append((abs(dx), DIRECTION_ORDER.index(vertical), vertical))
    return tuple(direction for _, _, direction in sorted(scored))


def max_flight_ticks(state: SimulationState, rules: Rules = DEFAULT_RULES) -> int:
    """Return a bound on how long a projectile can stay inside the playfield.

    A projectile travels in a straight line at ``rules.projectile_speed`` and is removed
    the tick it leaves the world, so the longest possible flight is the playfield's
    diagonal span. The ``+ 1`` covers the partial final step.
    """
    width, height = state.world_size(rules)
    return (width + height) // rules.projectile_speed + 1


def shot_target_id(
    state: SimulationState,
    *,
    origin: Vec2,
    direction: Direction,
    faction: Faction,
    rules: Rules = DEFAULT_RULES,
    max_ticks: int | None = None,
) -> int | None:
    """Return the tank a shot from ``origin`` would hit, or ``None``.

    The walk is the rules engine's own resolution order, one tick at a time: advance by
    ``rules.projectile_speed``, leave the world, resolve the tile, then test tank bodies
    in ascending identifier order. A shot stops at a mirror (see the module note), at
    anything that is not empty ground, water or forest, and at the home base, whose tile
    absorbs a friendly shot and ends the run on a hostile one. A bot therefore never
    reports the base as a firing solution and never emits a shot aimed at it: destroying a
    base on a bot's own initiative is a campaign rule nobody has written.

    Tanks of ``faction`` are transparent, matching ``step._phase_projectile_vs_tank``,
    which only resolves a projectile against the opposing faction. Invincible tanks are
    skipped for exactly as long as their timer outlives the flight.
    """
    limit = max_flight_ticks(state, rules) if max_ticks is None else max_ticks
    width, height = state.world_size(rules)
    span = 2 * rules.projectile_radius + 1
    dx, dy = direction.scaled(rules.projectile_speed)
    position = origin
    ordered = sorted(state.tanks, key=lambda item: item.entity_id)
    for elapsed in range(1, limit + 1):
        position = position.translated(dx, dy)
        if not (0 <= position.x < width and 0 <= position.y < height):
            return None
        tile = state.grid.at(cell_of(position, rules.tile_size))
        if tile in MIRROR_REFLECTIONS or not passes_projectile(tile):
            return None
        body = Rect(
            position.x - rules.projectile_radius,
            position.y - rules.projectile_radius,
            span,
            span,
        )
        for candidate in ordered:
            if candidate.faction is faction or candidate.invincible_ticks > elapsed:
                continue
            if body.overlaps(candidate.body(rules)):
                return candidate.entity_id
    return None


def incoming_threat(
    state: SimulationState,
    tank: Tank,
    horizon: int,
    rules: Rules = DEFAULT_RULES,
) -> Projectile | None:
    """Return the first hostile projectile due to hit ``tank`` within ``horizon`` ticks.

    "First" is by ascending projectile identifier, not by time to impact, so the answer
    does not depend on how a tie between two simultaneous impacts is broken. ``horizon``
    is the profile's planning horizon, which is what makes threat awareness a difficulty
    setting: a bot that looks two ticks ahead sees six pixels of warning.

    The trace stops at a mirror for the same reason :func:`shot_target_id` does, and
    treats the tank as stationary; dodging is decided from the pose the bot currently has.
    """
    for projectile in sorted(state.projectiles, key=lambda item: item.entity_id):
        if projectile.faction is tank.faction:
            continue
        if projectile_reaches(state, projectile, tank, horizon, rules):
            return projectile
    return None


def projectile_reaches(
    state: SimulationState,
    projectile: Projectile,
    tank: Tank,
    horizon: int,
    rules: Rules = DEFAULT_RULES,
) -> bool:
    """Return whether ``projectile`` would strike ``tank`` within ``horizon`` ticks.

    The single-projectile core of :func:`incoming_threat`. Both bodies are held still:
    this answers "am I in the line as I stand now?". A bot evaluating an escape asks
    :func:`projectile_reaches_moving` instead, because standing still is not what it is
    about to do.

    An overlap while the tank is still invincible does not end the walk. The engine's
    ``_phase_projectile_vs_tank`` skips an invincible tank and leaves the projectile in
    flight, and ``_phase_expire_powerups`` runs first on every later tick, so the same
    projectile can be deep inside the same body when the timer reaches zero and kill it
    there. Stopping at the first overlap would report that tank as safe.
    """
    width, height = state.world_size(rules)
    span = 2 * rules.projectile_radius + 1
    dx, dy = projectile.direction.scaled(rules.projectile_speed)
    position = projectile.position
    target = tank.body(rules)
    for elapsed in range(1, horizon + 1):
        position = position.translated(dx, dy)
        if not (0 <= position.x < width and 0 <= position.y < height):
            return False
        tile = state.grid.at(cell_of(position, rules.tile_size))
        if tile in MIRROR_REFLECTIONS or not passes_projectile(tile):
            return False
        body = Rect(
            position.x - rules.projectile_radius,
            position.y - rules.projectile_radius,
            span,
            span,
        )
        if body.overlaps(target) and tank.invincible_ticks <= elapsed:
            return True
    return False


def projectile_reaches_moving(
    state: SimulationState,
    projectile: Projectile,
    tank: Tank,
    direction: Direction,
    horizon: int,
    rules: Rules = DEFAULT_RULES,
) -> bool:
    """Return whether ``projectile`` strikes ``tank`` while ``tank`` steps in ``direction``.

    The escape question, asked honestly. Comparing the projectile's whole flight against
    only the pose a tank ends on would miss the case the dodge exists to avoid: a body
    that is struck part-way through its run and never reaches that final pose at all. So
    both move together, one tick at a time, in the engine's own phase order - tanks move
    (``step._phase_move``) before projectiles advance and resolve against them - and the
    first overlap that would actually land ends the walk. An overlap the tank is still
    invincible for does not; see :func:`projectile_reaches`.

    The run stops advancing the tank once its path is refused, which is what makes this
    the full answer rather than :func:`clearance_ticks` plus a guess: a tank that runs
    into a wall after three ticks keeps being traced where it actually stands.
    """
    width, height = state.world_size(rules)
    span = 2 * rules.projectile_radius + 1
    dx, dy = projectile.direction.scaled(rules.projectile_speed)
    position = projectile.position
    pose = tank
    for elapsed in range(1, horizon + 1):
        pose = predicted_pose(state, pose, direction, rules)
        position = position.translated(dx, dy)
        if not (0 <= position.x < width and 0 <= position.y < height):
            return False
        tile = state.grid.at(cell_of(position, rules.tile_size))
        if tile in MIRROR_REFLECTIONS or not passes_projectile(tile):
            return False
        body = Rect(
            position.x - rules.projectile_radius,
            position.y - rules.projectile_radius,
            span,
            span,
        )
        if body.overlaps(pose.body(rules)) and tank.invincible_ticks <= elapsed:
            return True
    return False
