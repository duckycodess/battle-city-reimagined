"""One campaign: the stages, the score, the lives, and the rules that join them.

:class:`CampaignRun` is the whole of the campaign phase's behaviour. It owns exactly the
rules the simulation deliberately does not: how many enemies a stage releases, when one
arrives, when a stage is cleared, when the campaign is won, and what a restart rewinds. It
owns nothing the simulation already owns -- lives, the damage ladder, score *values* and
both failure outcomes stay where they are -- and it adds no rule to the simulation by
other means.

It is a value. Advancing returns a new run, so a caller can hold the run it drew, a test
can fork a run, and nothing is ever half-advanced. It reads no clock, no file, no socket
and no environment, imports no display library, and draws only from a seeded
:class:`battle_city_sim.rng.Rng` derived from the campaign seed and the stage index. Given
one seed, one plan, one rules pair and one sequence of per-tick intents it produces one
answer, which is what ``tests/campaign`` asserts.

The tick
--------
Every tick the run does the same four things, in this order:

1. decide whether an enemy is due, and which legal spawn command expresses it,
2. ask the driver what the enemies do, and keep only the part of its answer that is legal,
3. add the local player's own commands and step the simulation once, and
4. add up the points the tick reported, then read the phase off the resulting state.

Spawn decisions are made against the *pre-tick* state, because that is the state the
simulation validates a tick input against. A tank spawned this tick is therefore first
visible to the driver on the next one.

Victory is a phase, not an outcome
----------------------------------
``battle_city_sim.RunOutcome`` has two values and both are failures. A cleared stage
leaves the simulation state perfectly ordinary and unfinished: "cleared" is a statement
about the campaign, not about the run. :class:`CampaignPhase` therefore holds the
campaign's answer, and its ``FAILED`` value is *derived* from
:attr:`battle_city_sim.SimulationState.outcome` rather than set independently, so the
simulation stays the only thing that can end a run.

Deviations from the historical runtime
--------------------------------------
Two, both accepted in ``openspec/changes/campaign-v1`` and both recorded in the product
specification:

* **The campaign can be won.** The original's win branch is unreachable -- clearing the
  final stage takes its ``level <= max(level)`` arm, and the level-completed handler then
  does nothing forever.
* **A campaign restart clears the score.** The original reset the stage and the lives and
  left the score alone, so points accumulated across restarts without bound.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from battle_city_sim import (
    DEFAULT_RULES,
    Command,
    Event,
    Faction,
    GridPos,
    Rect,
    Rng,
    Rules,
    RunOutcome,
    ScoreAwarded,
    SimulationState,
    SpawnEnemyCommand,
)

from ..intents import IDLE_INTENT, PlayerIntent
from ..session import DEFAULT_SEED, StageSession
from .driver import EnemyCommandDriver, IdleEnemyDriver, legal_enemy_commands
from .plan import StagePlan
from .rules import DEFAULT_CAMPAIGN_RULES, CampaignRules
from .seeding import stage_simulation_seed, stage_spawn_rng


class CampaignPhase(Enum):
    """Where a campaign stands. Only ``PLAYING`` consumes ticks."""

    PLAYING = "playing"
    STAGE_CLEARED = "stage_cleared"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CampaignRun:
    """A campaign in progress. Build one with :meth:`start`."""

    plan: tuple[StagePlan, ...]
    rules: CampaignRules
    sim_rules: Rules
    seed: int
    driver: EnemyCommandDriver
    stage_index: int
    session: StageSession
    score: int
    stage_start_score: int
    stage_start_lives: int
    pending_enemies: int
    next_spawn_tick: int
    rng: Rng
    phase: CampaignPhase
    last_events: tuple[Event, ...] = ()

    # -- construction ----------------------------------------------------------

    @classmethod
    def start(
        cls,
        plan: tuple[StagePlan, ...],
        *,
        seed: int = DEFAULT_SEED,
        rules: CampaignRules = DEFAULT_CAMPAIGN_RULES,
        sim_rules: Rules = DEFAULT_RULES,
        driver: EnemyCommandDriver | None = None,
        stage_index: int = 0,
    ) -> CampaignRun:
        """Begin ``plan`` at ``stage_index`` with a fresh score and the starting lives.

        ``stage_index`` is the whole of the checkpoint policy for now: there is no saved
        progress, so a player resumes by choosing where to begin. A campaign begun at a
        later stage still starts with :attr:`CampaignRules.starting_lives` and a score of
        zero, because nothing happened before it to carry.
        """
        if not plan:
            raise ValueError("a campaign needs at least one stage")
        if not 0 <= stage_index < len(plan):
            raise ValueError(f"stage index {stage_index} is outside the {len(plan)}-stage campaign")
        return cls._begin_stage(
            plan=plan,
            rules=rules,
            sim_rules=sim_rules,
            seed=seed,
            driver=IdleEnemyDriver() if driver is None else driver,
            stage_index=stage_index,
            score=0,
            lives=rules.starting_lives,
        )

    @classmethod
    def _begin_stage(
        cls,
        *,
        plan: tuple[StagePlan, ...],
        rules: CampaignRules,
        sim_rules: Rules,
        seed: int,
        driver: EnemyCommandDriver,
        stage_index: int,
        score: int,
        lives: int,
    ) -> CampaignRun:
        """Open one stage at tick zero. The only place a stage is ever created.

        Lives reach the simulation through a per-stage :class:`battle_city_sim.Rules`
        copy, because ``new_game`` reads ``rules.starting_lives`` and the state offers no
        other way in. Nothing else in the rules changes, and ``lives`` is always at least
        one: a run that reached zero has already ended.
        """
        stage_plan = plan[stage_index]
        stage_rules = replace(sim_rules, starting_lives=lives)
        session = StageSession.start(
            stage_plan.stage,
            seed=stage_simulation_seed(seed=seed, stage_index=stage_index),
            rules=stage_rules,
        )
        return cls(
            plan=plan,
            rules=rules,
            sim_rules=sim_rules,
            seed=seed,
            driver=driver.entering_stage(session.state, stage_rules),
            stage_index=stage_index,
            session=session,
            score=score,
            stage_start_score=score,
            stage_start_lives=lives,
            pending_enemies=stage_plan.enemy_quota,
            next_spawn_tick=0,
            rng=stage_spawn_rng(seed=seed, stage_index=stage_index),
            phase=CampaignPhase.PLAYING,
            last_events=(),
        )

    # -- queries ---------------------------------------------------------------

    @property
    def stage(self) -> StagePlan:
        """The stage being played."""
        return self.plan[self.stage_index]

    @property
    def stage_number(self) -> int:
        """The stage's one-based position, for a HUD that counts the way players do."""
        return self.stage_index + 1

    @property
    def stage_count(self) -> int:
        return len(self.plan)

    @property
    def on_last_stage(self) -> bool:
        return self.stage_index == len(self.plan) - 1

    @property
    def lives(self) -> int:
        """Lives the local player holds. Owned by the simulation, read here."""
        return self.session.player.lives

    @property
    def enemies_alive(self) -> int:
        return len(self.session.state.tanks_of(Faction.ENEMY))

    @property
    def enemies_remaining(self) -> int:
        """Enemies still to be destroyed before the stage can clear: queued plus alive."""
        return self.pending_enemies + self.enemies_alive

    @property
    def outcome(self) -> RunOutcome | None:
        """The failure the simulation recorded, if any. ``None`` while the run lives."""
        return self.session.state.outcome

    @property
    def consumes_ticks(self) -> bool:
        return self.phase is CampaignPhase.PLAYING

    # -- advancing -------------------------------------------------------------

    def with_session(self, session: StageSession) -> CampaignRun:
        """Adopt a session decided outside the campaign, and re-read the phase from it.

        :class:`~battle_city_client.shell.ClientShell` publishes its session as a writable
        attribute, and a caller that assembles a state -- a presentation test driving a
        terminal screen is the one in the tree -- writes it there rather than through the
        campaign. Dropping that write would make the campaign and the shell disagree about
        which run is live, so the campaign takes the session as given and derives its phase
        from it, exactly as it derives ``FAILED`` from
        :attr:`battle_city_sim.SimulationState.outcome` after a tick it ran itself.

        Identity is the test, so the ordinary case -- the shell handing back the session the
        campaign just produced -- costs nothing and changes nothing. Adoption rewrites no
        other field: the quota, the cadence and the generator belong to the stage, not to
        the state, and a caller that replaces the state is not claiming to have spawned
        anything.
        """
        if session is self.session:
            return self
        return replace(
            self,
            session=session,
            phase=self._phase_after(session, self.pending_enemies),
        )

    def advance(self, ticks: int, intent: PlayerIntent = IDLE_INTENT) -> CampaignRun:
        """Advance up to ``ticks`` ticks, holding ``intent`` for each of them.

        Stops early the moment the phase leaves ``PLAYING``, so the tick a stage is
        cleared on is the last one it runs and nothing is simulated past an ending.
        ``last_events`` collects every event of the whole call, as
        :meth:`StageSession.advance` does.
        """
        if ticks < 0:
            raise ValueError(f"ticks must not be negative, found {ticks}")
        run = self
        events: list[Event] = []
        for _ in range(ticks):
            if run.phase is not CampaignPhase.PLAYING:
                break
            run = run._advanced_one(intent)
            events.extend(run.last_events)
        return replace(run, last_events=tuple(events))

    def _advanced_one(self, intent: PlayerIntent) -> CampaignRun:
        """One tick: spawn decision, driver, player, step, score, phase."""
        state = self.session.state
        stage_rules = self.session.rules

        spawn, rng = self._spawn_for(state)
        driver, suggested = self.driver.commands(state, stage_rules)

        commands: list[Command] = []
        if spawn is not None:
            commands.append(spawn)
        commands.extend(legal_enemy_commands(tuple(suggested), state))
        commands.extend(self.session.commands_for(intent))

        session = self.session.stepped(tuple(commands))

        pending = self.pending_enemies
        if spawn is not None:
            pending -= 1
            next_spawn = state.tick + self.rules.spawn_interval_ticks
        elif self.pending_enemies > 0 and state.tick >= self.next_spawn_tick:
            # Every declared spawn cell was occupied. Keep the quota and try again next
            # tick: the blocker is usually a tank that is about to move, and waiting a
            # whole interval for a one-tick overlap would read as a stall.
            next_spawn = state.tick + 1
        else:
            next_spawn = self.next_spawn_tick

        score = self.score + sum(
            event.points for event in session.last_events if isinstance(event, ScoreAwarded)
        )
        return replace(
            self,
            driver=driver,
            session=session,
            score=score,
            pending_enemies=pending,
            next_spawn_tick=next_spawn,
            rng=rng,
            phase=self._phase_after(session, pending),
            last_events=session.last_events,
        )

    def _phase_after(self, session: StageSession, pending: int) -> CampaignPhase:
        """Read the phase off a stepped session. Failure outranks a clear."""
        if session.state.finished:
            return CampaignPhase.FAILED
        if not _stage_cleared(session.state, pending):
            return CampaignPhase.PLAYING
        return CampaignPhase.COMPLETED if self.on_last_stage else CampaignPhase.STAGE_CLEARED

    # -- spawning --------------------------------------------------------------

    def _spawn_for(self, state: SimulationState) -> tuple[SpawnEnemyCommand | None, Rng]:
        """The spawn command due on ``state``'s tick, and the successor generator.

        Three draws are made on every attempt, in a fixed order -- cell, facing, variant --
        and they are made before occupancy is resolved, so the generator's position is a
        function of the number of attempts alone and a restarted stage replays exactly.
        The historical runtime made the same three draws and then placed the tank whether
        or not the cell was free; the simulation refuses an overlapping spawn, and
        refusing is right, so the campaign retries instead.
        """
        if self.pending_enemies <= 0 or state.tick < self.next_spawn_tick:
            return None, self.rng
        cells = self.session.stage.enemy_spawns
        drawn, rng = self.rng.below(len(cells))
        facing, rng = rng.choice(self.rules.spawn_facings)
        variant, rng = rng.choice(self.rules.spawn_variants)
        cell = _first_free_cell(cells, drawn, state, self.session.rules)
        if cell is None:
            return None, rng
        return SpawnEnemyCommand(cell=cell, variant=variant, facing=facing), rng

    # -- transitions -----------------------------------------------------------

    def advanced_stage(self) -> CampaignRun:
        """Begin the next stage, carrying the score and the surviving lives.

        A no-op unless the current stage is cleared, so a caller cannot skip a stage by
        asking twice.
        """
        if self.phase is not CampaignPhase.STAGE_CLEARED:
            return self
        return CampaignRun._begin_stage(
            plan=self.plan,
            rules=self.rules,
            sim_rules=self.sim_rules,
            seed=self.seed,
            driver=self.driver,
            stage_index=self.stage_index + 1,
            score=self.score,
            lives=self.lives,
        )

    def restarted_stage(self) -> CampaignRun:
        """Replay the current stage from the score, lives and seed it began with.

        The attempt being discarded leaves nothing behind, points included: a stage that
        could be farmed by dying into it would make the score a measure of patience.
        """
        return CampaignRun._begin_stage(
            plan=self.plan,
            rules=self.rules,
            sim_rules=self.sim_rules,
            seed=self.seed,
            driver=self.driver,
            stage_index=self.stage_index,
            score=self.stage_start_score,
            lives=self.stage_start_lives,
        )

    def restarted(self) -> CampaignRun:
        """Begin the campaign again: first stage, starting lives, and a score of zero.

        The historical restart reset the stage and the lives and left the score alone. See
        the module docstring; the score is a measure of one run and is cleared with it.
        """
        return CampaignRun.start(
            self.plan,
            seed=self.seed,
            rules=self.rules,
            sim_rules=self.sim_rules,
            driver=self.driver,
            stage_index=0,
        )


def _stage_cleared(state: SimulationState, pending: int) -> bool:
    """The historical clear condition, kept verbatim.

    Nothing queued, nothing hostile alive, and *nothing in flight* -- including the
    player's own shot. The last term is easy to mistake for an oversight and is not one:
    it is what stops a stage ending while a bullet that could still destroy the base is
    on the board.
    """
    return pending == 0 and not state.tanks_of(Faction.ENEMY) and not state.projectiles


def _first_free_cell(
    cells: tuple[GridPos, ...], drawn: int, state: SimulationState, rules: Rules
) -> GridPos | None:
    """The drawn cell, or the next declared cell no live tank overlaps, or ``None``.

    Walking on from the drawn index rather than restarting from the first cell keeps the
    draw meaningful when several cells are free, and keeps the answer a pure function of
    the draw and the board.
    """
    bodies = tuple(tank.body(rules) for tank in state.tanks)
    for offset in range(len(cells)):
        cell = cells[(drawn + offset) % len(cells)]
        body = Rect(
            x=cell.x * rules.tile_size,
            y=cell.y * rules.tile_size,
            width=rules.tank_size,
            height=rules.tank_size,
        )
        if not any(body.overlaps(other) for other in bodies):
            return cell
    return None
