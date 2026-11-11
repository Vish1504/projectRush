from datetime import datetime, timedelta, timezone

from app.models.campaign import Campaign
from app.services.pod_optimizer import build_pod


def make_campaign(
    campaign_id: int,
    duration_seconds: int,
    competitive_group: str | None = None,
) -> Campaign:
    now = datetime.now(timezone.utc)

    return Campaign(
        id=campaign_id,
        name=f"Campaign {campaign_id}",
        capacity=100,
        remaining_unleased_capacity=100,
        start_time=now - timedelta(hours=1),
        end_time=now + timedelta(hours=1),
        status="ACTIVE",
        frequency_cap_per_hour=3,
        duration_seconds=duration_seconds,
        competitive_group=competitive_group,
    )


def test_optimizer_fills_break_with_best_combination():
    campaign_a = make_campaign(1, 20)
    campaign_b = make_campaign(2, 15)
    campaign_c = make_campaign(3, 10)
    campaign_d = make_campaign(4, 5)

    result = build_pod(
        candidates=[
            campaign_a,
            campaign_b,
            campaign_c,
            campaign_d,
        ],
        break_duration_seconds=30,
        campaign_pacing_scores={
            1: 100,
            2: 95,
            3: 70,
            4: 40,
        },
    )

    assert [campaign.id for campaign in result] == [2, 3, 4]
    assert sum(
        campaign.duration_seconds
        for campaign in result
    ) == 30


def test_optimizer_never_exceeds_break_duration():
    campaigns = [
        make_campaign(1, 20),
        make_campaign(2, 15),
        make_campaign(3, 10),
    ]

    result = build_pod(
        candidates=campaigns,
        break_duration_seconds=20,
    )

    assert sum(
        campaign.duration_seconds
        for campaign in result
    ) <= 20


def test_competing_campaigns_cannot_share_pod():
    nike = make_campaign(
        1,
        15,
        competitive_group="SPORTSWEAR",
    )

    adidas = make_campaign(
        2,
        15,
        competitive_group="SPORTSWEAR",
    )

    samsung = make_campaign(
        3,
        15,
        competitive_group="ELECTRONICS",
    )

    result = build_pod(
        candidates=[
            nike,
            adidas,
            samsung,
        ],
        break_duration_seconds=30,
        campaign_pacing_scores={
            1: 20,
            2: 10,
            3: 5,
        },
    )

    result_ids = {
        campaign.id
        for campaign in result
    }

    assert not ({1, 2} <= result_ids)
    assert len(result) == 2


def test_pacing_breaks_tie_between_equally_filled_pods():
    nike = make_campaign(
        1,
        15,
        competitive_group="SPORTSWEAR",
    )

    adidas = make_campaign(
        2,
        15,
        competitive_group="SPORTSWEAR",
    )

    samsung = make_campaign(
        3,
        15,
        competitive_group="ELECTRONICS",
    )

    result = build_pod(
        candidates=[
            nike,
            adidas,
            samsung,
        ],
        break_duration_seconds=30,
        campaign_pacing_scores={
            1: 20,
            2: 5,
            3: 0,
        },
    )

    assert [campaign.id for campaign in result] == [1, 3]


def test_campaign_longer_than_break_is_ignored():
    long_campaign = make_campaign(1, 45)
    fitting_campaign = make_campaign(2, 15)

    result = build_pod(
        candidates=[
            long_campaign,
            fitting_campaign,
        ],
        break_duration_seconds=30,
    )

    assert [campaign.id for campaign in result] == [2]


def test_empty_candidates_returns_empty_pod():
    result = build_pod(
        candidates=[],
        break_duration_seconds=30,
    )

    assert result == []