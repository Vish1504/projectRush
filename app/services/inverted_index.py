# /Users/vish1504/projectRush/app/services/inverted_index.py

#  Here we store and searche campaign IDs in memory


class InvertedIndex:
    def __init__(self):
        # One published state contains both the version and index registers
        self._state = {
            "version": 0,
            "snapshot": {
                "regions": {},
                "devices": {},
                "subscription_tiers": {}
            }
        }
    
    def get_snapshot(self):
        # Return the current snapshot (read-only by convention)
        return self._state["snapshot"]

    # READER: Find campaigns matching the viewer data
    def find_candidates(self, region, device, subscription):
        state = self._state
        snapshot = state["snapshot"]
        
        # Match the region or campaigns targeting ALL regions
        region_targeting = (
            snapshot["regions"].get(region, set())
            | snapshot["regions"].get("ALL", set())
        )

        # Match the device or campaigns targeting ALL devices
        device_targeting = (
            snapshot["devices"].get(device, set())
            | snapshot["devices"].get("ALL", set())
        )
        
        # Match the subscription or campaigns targeting ALL tiers
        subscription_targeting = (
            snapshot["subscription_tiers"].get(subscription, set())
            | snapshot["subscription_tiers"].get("ALL", set())
        )
        
        result_candiate_IDs=(region_targeting & device_targeting & subscription_targeting)
        # Keep only campaigns matching all three dimensions
        return result_candiate_IDs, state["version"]
    
    def _create_temp_snapshot(self):
        # Copy the snapshot so readers can keep using the old one
        snapshot = self._state["snapshot"]
        temp_snapshot = snapshot.copy()
    
        # Copy the dictionaries, but not all their sets
        for dimension in ("regions", "devices", "subscription_tiers"):
            temp_snapshot[dimension] = snapshot[dimension].copy()
    
        return temp_snapshot
    
    
    def _remove_from_temp_snapshot(self, temp_snapshot, campaign):
        # Remove campaign memberships from the private snapshot
        campaign_id = campaign["id"]

        # Regions
        campaign_regions = campaign["regions"]

        # Empty regions list means ALL regions
        if len(campaign_regions) == 0:
            temp_snapshot["regions"]["ALL"] = (
                temp_snapshot["regions"].get("ALL", set()) - {campaign_id}
            )
        else:
            for campaign_region in campaign_regions:
                temp_snapshot["regions"][campaign_region] = (
                    temp_snapshot["regions"].get(campaign_region, set())
                    - {campaign_id}
                )

        # Devices
        campaign_devices = campaign["devices"]
        
        # Empty devices list means ALL devices
        if len(campaign_devices) == 0:
            temp_snapshot["devices"]["ALL"] = (
                temp_snapshot["devices"].get("ALL", set()) - {campaign_id}
            )
        else:
            for campaign_device in campaign_devices:
                temp_snapshot["devices"][campaign_device] = (
                    temp_snapshot["devices"].get(campaign_device, set())
                    - {campaign_id}
                )

        # Subscription tiers
        campaign_subscriptions = campaign["subscription_tiers"]
        
        # Empty subscription list means ALL tiers
        if len(campaign_subscriptions) == 0:
            temp_snapshot["subscription_tiers"]["ALL"] = (
                temp_snapshot["subscription_tiers"].get("ALL", set()) - {campaign_id}
            )
        else:
            for campaign_subscription in campaign_subscriptions:
                temp_snapshot["subscription_tiers"][campaign_subscription] = (
                    temp_snapshot["subscription_tiers"].get(
                        campaign_subscription, set()
                    ) - {campaign_id}
                )

        return temp_snapshot

        
    def _add_to_temp_snapshot(self, temp_snapshot, campaign):
        # Return the updated temp_snapshot containing the campaign(s)
    
        # Add campaign memberships to the private snapshot
        campaign_id = campaign["id"]
        campaign_regions = campaign["regions"]

        # Add to each region, or ALL if unrestricted
        if len(campaign_regions) == 0:
            temp_snapshot["regions"]["ALL"] = (
            temp_snapshot["regions"].get("ALL", set()) | {campaign_id})
        else:
            for campaign_region in campaign_regions:
                temp_snapshot["regions"][campaign_region] = (
                    temp_snapshot["regions"].get(campaign_region, set()) | {campaign_id})
        
        # Add to each device, or ALL if unrestricted
        campaign_devices = campaign["devices"]
        if len(campaign_devices) == 0:
            temp_snapshot["devices"]["ALL"] = (
            temp_snapshot["devices"].get("ALL", set()) | {campaign_id}
            )
        else:
            for campaign_device in campaign_devices:
                temp_snapshot["devices"][campaign_device] = (
                    temp_snapshot["devices"].get(campaign_device, set())
                    | {campaign_id}
                )
        
        # Add to each tier, or ALL if unrestricted
        campaign_subscriptions = campaign["subscription_tiers"]
        if len(campaign_subscriptions) == 0:
            temp_snapshot["subscription_tiers"]["ALL"] = (
                temp_snapshot["subscription_tiers"].get("ALL", set()) | {campaign_id})
        else:
            for campaign_subscription in campaign_subscriptions:
                temp_snapshot["subscription_tiers"][campaign_subscription] = (
                    temp_snapshot["subscription_tiers"].get(campaign_subscription, set()) | {campaign_id})
                
        return temp_snapshot
    
    # Writer
    def add_campaign(self, campaign):
        temp_snapshot = self._create_temp_snapshot()
        self._add_to_temp_snapshot(temp_snapshot, campaign)

        # Publish new registers while preserving the current version
        self._state = {
            "version": self._state["version"],
            "snapshot": temp_snapshot
        }
        
    def remove_campaign(self,campaign):
        temp_snapshot = self._create_temp_snapshot()
        self._remove_from_temp_snapshot(temp_snapshot, campaign)
        # now to publish the version & snapshot after removing campaign. We're keeping the exisiting version for this snapshot
        self._state = {
            "version": self._state["version"],
            "snapshot": temp_snapshot
        }
        
    # WRITER: Change an existing campaign's targeting
    def update_campaign(self, old_campaign, new_campaign):
    
        temp_snapshot = self._create_temp_snapshot()
        
        # Remove old targeting and add new targeting privately
        self._remove_from_temp_snapshot(temp_snapshot, old_campaign)
        self._add_to_temp_snapshot(temp_snapshot, new_campaign)
        
        # Publish updated targeting registers and preserve the version
        self._state = {
            "version": self._state["version"],
            "snapshot": temp_snapshot
        }
    
    # WRITER: Rebuild the entire index from campaign records
    def build_index(self, campaigns, version=0):
    # Build a new set of targeting registers from scratch
        temp_snapshot = {
            "regions": {},
            "devices": {},
            "subscription_tiers": {}
        }

        # Populate the temporary snapshot
        for campaign in campaigns:
            self._add_to_temp_snapshot(temp_snapshot, campaign)

        # Publish the completed registers and their version together
        self._state = {
            "version": version,
            "snapshot": temp_snapshot
        }
            