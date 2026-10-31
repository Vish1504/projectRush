
from app.services.inverted_index import InvertedIndex


def test_inverted_index():
    # Three campaigns with different targeting rules
    campaign_101 = {
        "id": 101,
        "regions": ["Mumbai", "Delhi"],
        "devices": ["Mobile"],
        "subscription_tiers": ["Premium"]
    }

    campaign_102 = {
        "id": 102,
        "regions": [],
        "devices": ["AndroidTV"],
        "subscription_tiers": []
    }

    campaign_103 = {
        "id": 103,
        "regions": ["Mumbai"],
        "devices": [],
        "subscription_tiers": ["Free"]
    }

    index = InvertedIndex()

    # 1. Empty index returns no campaigns
    assert index.find_candidates("Mumbai", "Mobile", "Premium")[0] == set()

    # 2. Add campaigns
    index.add_campaign(campaign_101)
    index.add_campaign(campaign_102)
    index.add_campaign(campaign_103)

    # 3. Exact and multi-value targeting
    assert index.find_candidates("Mumbai", "Mobile", "Premium")[0] == {101}
    assert index.find_candidates("Delhi", "Mobile", "Premium")[0] == {101}

    # 4. Unrestricted region and subscription
    assert index.find_candidates("Jaipur", "AndroidTV", "Free")[0] == {102}

    # 5. Unrestricted device
    assert index.find_candidates("Mumbai", "Laptop", "Free")[0] == {103}

    # 6. No matching campaigns
    assert index.find_candidates("Delhi", "Laptop", "Free")[0] == set()

    # 7. Duplicate additions must not create duplicate IDs
    index.add_campaign(campaign_101)
    assert index.find_candidates("Mumbai", "Mobile", "Premium")[0] == {101}

    # 8. Removal preserves the old snapshot
    old_snapshot = index.get_snapshot()
    index.remove_campaign(campaign_101)

    assert index.find_candidates("Mumbai", "Mobile", "Premium")[0] == set()
    assert old_snapshot["regions"]["Mumbai"] == {101, 103}

    # 9. Removing again should not crash
    index.remove_campaign(campaign_101)

    # 10. Update targeting with one snapshot publication
    new_campaign_103 = {
        "id": 103,
        "regions": ["Pune"],
        "devices": ["Mobile"],
        "subscription_tiers": ["Premium"]
    }

    before_update = index.get_snapshot()
    index.update_campaign(campaign_103, new_campaign_103)

    assert index.find_candidates("Pune", "Mobile", "Premium")[0] == {103}
    assert index.find_candidates("Mumbai", "Laptop", "Free")[0] == set()
    assert before_update["regions"]["Mumbai"] == {103}

    # 11. Full rebuild replaces previous campaigns
    index.build_index([campaign_101])

    assert index.find_candidates("Delhi", "Mobile", "Premium")[0] == {101}
    assert index.find_candidates("Pune", "Mobile", "Premium")[0] == set()
    assert index.find_candidates("Jaipur", "AndroidTV", "Free")[0] == set()

    # 12. Empty rebuild clears the index
    index.build_index([])

    assert index.find_candidates("Delhi", "Mobile", "Premium")[0] == set()

    # 13. Version is returned with candidate IDs
    index.build_index([campaign_101], version=5)

    assert index._state["version"] == 5

    candidate_ids, version = index.find_candidates(
        "Mumbai", "Mobile", "Premium"
    )

    assert candidate_ids == {101}
    assert version == 5

    # 14. Rebuild updates both targeting data and snapshot version
    index.build_index([campaign_103], version=6)

    candidate_ids, version = index.find_candidates(
        "Mumbai", "Laptop", "Free"
    )

    assert candidate_ids == {103}
    assert version == 6

    # Old targeting must no longer match
    candidate_ids, version = index.find_candidates(
        "Mumbai", "Mobile", "Premium"
    )

    assert candidate_ids == set()
    assert version == 6

    print("All 14 inverted index checks passed!")


if __name__ == "__main__":
    test_inverted_index()
