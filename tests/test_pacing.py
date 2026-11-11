from datetime import datetime, timedelta, timezone

import pytest

from app.models.campaign import Campaign
from app.services.pacing import calculate_pacing_score


def make_campaign() -> Campaign:
    start = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)

    return Campaign(
        id=1,
        name="Nike",
        capacity=1000,
        remaining_unleased_capacity=1000,
        start_time=start,
        end_time=start + timedelta(hours=10),
        status="ACTIVE",
        frequency_cap_per_hour=3,
        duration_seconds=15,
        competitive_group="SPORTSWEAR",
    )


def test_campaign_behind_pace_gets_positive_score():
    campaign = make_campaign()

    now = campaign.start_time + timedelta(hours=5)

    score = calculate_pacing_score(
        campaign=campaign,
        committed_allocations=350,
        now=now,
    )

    assert score == pytest.approx(0.15)


def test_campaign_ahead_of_pace_gets_negative_score():
    campaign = make_campaign()

    now = campaign.start_time + timedelta(hours=5)

    score = calculate_pacing_score(
        campaign=campaign,
        committed_allocations=650,
        now=now,
    )

    assert score == pytest.approx(-0.15)


def test_campaign_exactly_on_pace_gets_zero():
    campaign = make_campaign()

    now = campaign.start_time + timedelta(hours=5)

    score = calculate_pacing_score(
        campaign=campaign,
        committed_allocations=500,
        now=now,
    )

    assert score == pytest.approx(0.0)


def test_expected_progress_is_zero_before_campaign_starts():
    campaign = make_campaign()

    now = campaign.start_time - timedelta(hours=2)

    score = calculate_pacing_score(
        campaign=campaign,
        committed_allocations=0,
        now=now,
    )

    assert score == pytest.approx(0.0)


def test_expected_progress_is_capped_after_campaign_ends():
    campaign = make_campaign()

    now = campaign.end_time + timedelta(hours=3)

    score = calculate_pacing_score(
        campaign=campaign,
        committed_allocations=800,
        now=now,
    )

    assert score == pytest.approx(0.20)


def test_negative_committed_allocations_are_rejected():
    campaign = make_campaign()

    with pytest.raises(
        ValueError,
        match="committed_allocations cannot be negative",
    ):
        calculate_pacing_score(
            campaign=campaign,
            committed_allocations=-1,
        )