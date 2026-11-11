from datetime import datetime, timezone

from app.models.campaign import Campaign


# Measures how far a campaign is ahead of or behind its expected delivery.
def calculate_pacing_score(
    campaign: Campaign,
    committed_allocations: int,
    now: datetime | None = None,
) -> float:
    if committed_allocations < 0:
        raise ValueError("committed_allocations cannot be negative")

    current_time = now or datetime.now(timezone.utc)

    total_window = (
        campaign.end_time - campaign.start_time
    ).total_seconds()

    if total_window <= 0:
        return 0.0

    elapsed = (
        current_time - campaign.start_time
    ).total_seconds()

    # Keep expected progress between 0% and 100%.
    expected_fraction = min(
        1.0,
        max(0.0, elapsed / total_window),
    )

    actual_fraction = min(
        1.0,
        committed_allocations / campaign.capacity,
    )

    return expected_fraction - actual_fraction