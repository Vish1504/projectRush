from dataclasses import dataclass
from typing import Mapping

from app.models.campaign import Campaign


@dataclass(frozen=True)
# Tracks one possible pod version while we search for the best combination of campaign.
class PodState:
    campaigns: tuple[Campaign, ...]
    used_duration: int
    total_pacing_score: float
    competitive_groups: frozenset[str]


# This helps to find the best valid campaign combination for this ad break.
def build_pod(
    candidates: list[Campaign],
    break_duration_seconds: int,
    campaign_pacing_scores: Mapping[int, float] | None = None,
) -> list[Campaign]:
    if break_duration_seconds <= 0:
        return []

    scores = campaign_pacing_scores or {}

    # Incase the candidates arrive in a random order, we don't want identical requests returning different pods everytime 
    ordered_candidates = sorted(
        candidates,
        key=lambda campaign: campaign.id,
    )

    #These are all the useful pods we have found so far
    # Format :-
        # KEY: duration + competitive groups
        # VALUE: PodState
        
    states: dict[
        tuple[int, frozenset[str]],
        PodState,
    ] = {
        (0, frozenset()): PodState(
            campaigns=(),
            used_duration=0,
            total_pacing_score=0.0,
            competitive_groups=frozenset(),
        )
    }

    for campaign in ordered_candidates:
        # A campaign whose duration is longer than the entire break is invalid
        if campaign.duration_seconds > break_duration_seconds:
            continue

        # Snapshot the states that existed before considering this campaign.
        #
        # This preserves 0/1 semantics: a campaign cannot be selected
        # multiple times during the same iteration.
        existing_states = list(states.values())

        for state in existing_states:
            group = campaign.competitive_group

            # If another campaign from the same competitive group already exists in this pod:
            if (
                group is not None
                and group in state.competitive_groups
            ):
                continue
            
            # We add the duration of this new campaign to this current pod's calculated total duration
            new_duration = (
                state.used_duration
                + campaign.duration_seconds
            )

            if new_duration > break_duration_seconds:
                continue

            new_groups = state.competitive_groups

            if group is not None:
                new_groups = new_groups | {group}
            
            # We add the score of this new campaign to this current pod's calculated total score
            new_score = (
                state.total_pacing_score
                + scores.get(campaign.id, 0.0) #If the campaign pacing score doesn't exist then set it as 0
            )
            
            # Here we re-construct the pod using the new campaign
            new_state = PodState(
                campaigns=state.campaigns + (campaign,),
                used_duration=new_duration,
                total_pacing_score=new_score,
                competitive_groups=new_groups,
            )

            key = (
                new_duration,
                new_groups,
            )

            current = states.get(key)

            # Same duration + same restriction profile means both states
            # have identical future compatibility.
            #
            # Therefore the lower-scoring state is dominated and may
            # safely be discarded.
            if (
                current is None
                or new_state.total_pacing_score > current.total_pacing_score
            ):
                states[key] = new_state

    # We'd prefer maximum fill, then better pacing.
    #
    # 1. Fill as much of the break as possible.
    # 2. Among equally filled pods, prefer the higher pacing score.
    #
    # Because campaign processing order is deterministic, exact ties
    # also remain deterministic.
    best_state = max(
        states.values(),
        key=lambda state: (
            state.used_duration,
            state.total_pacing_score,
        ),
    )
    
    return list(best_state.campaigns)