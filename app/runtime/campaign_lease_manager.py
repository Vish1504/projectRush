# /Users/vish1504/projectRush/app/runtime/campaign_lease_manager.py
# Here we will create a seperate short-lived DB session only for lease acquisition

from app.db.database import SessionLocal
from app.models.campaign import CampaignLease
from app.repositories.campaign_repository import CampaignRepository, CampaignLeaseResult

from dataclasses import dataclass, field
# Used to safely handle multiple requests touching the same local campaign state
from threading import Condition, Lock
from enum import Enum
from uuid import uuid4



class LeaseReservationResult(str, Enum):
    RESERVED = "RESERVED"
    NO_UNLEASED_CAPACITY = "NO_UNLEASED_CAPACITY"
    REFILL_FAILED = "REFILL_FAILED"
    
# This will help us store all local data for a campaign lease data 
# Stores everything this worker needs to remember about one campaign
@dataclass
class CampaignLeaseState:
    # How many leased units this worker still has left for this campaign
    remaining_units: int = 0

    # Tells us if another request is already refilling this campaign
    refill_in_progress: bool = False

    # Once Postgres says no unleased capacity is left, don't ask again
    no_more_refills: bool = False

    # Protects this campaign's local state from concurrent requests
    lock: Lock = field(default_factory=Lock)

    # The waiting requests sleep until an ongoing refill finishes, and then get notified with the change in this condition
    condition: Condition = field(init=False)

    def __post_init__(self):
        # The condition uses the same lock that protects this campaign's state
        self.condition = Condition(self.lock)


# Manages all campaign leases owned by one FastAPI worker
class CampaignLeaseManager:

    # Creates one lease manager for one worker process
    def __init__(
        self,
        worker_id: str,
        lease_size: int,
    ):

        # Unique ID for this specific worker process
        self.worker_id = worker_id
        # How much max capacity units can this worker asks Postgres for per refill for this campaign?
        self.lease_size = lease_size
        # Stores local lease state separately for every campaign this worker sees
        self.campaign_states: dict[int, CampaignLeaseState] = {}
        # Protects creation/access of entries inside campaign_states
        self.campaign_states_lock = Lock()


    # To get a new durable lease from db 
    def _acquire_durable_lease(
        self,
        campaign_id: int,
        lease_request_id: str,
    ):

        # Create a separate smaller DB session only for this lease transaction
        lease_db_session = SessionLocal()
        
        try:
            # Give the repository this dedicated lease DB session
            campaign_repository = CampaignRepository(lease_db_session)

            # Ask Postgres to safely issue a lease to this worker
            lease_result = campaign_repository.acquire_lease(
                campaign_id=campaign_id,
                worker_id=self.worker_id,
                lease_request_id=lease_request_id,
                requested_units=self.lease_size,
            )

            # Make the capacity deduction + lease row permanent together
            lease_db_session.commit()
            
            # Give the lease result back to the lease manager
            return lease_result
        
        except Exception:
            # Something failed, so undo anything from this lease transaction
            lease_db_session.rollback()
            # Let the original error continue upward
            raise

        finally:
            # Always close this temporary lease DB session
            lease_db_session.close()
            
    # Gets this worker's local lease state for a campaign
    def _get_campaign_state(
    self,
    campaign_id: int,
    ) -> CampaignLeaseState:

        # Make sure two requests don't create two states for the same campaign
        with self.campaign_states_lock:

            # Incase it's a brand new campaign which isn't yet part of campaign_states
            if campaign_id not in self.campaign_states:
                self.campaign_states[campaign_id] = CampaignLeaseState()

        return self.campaign_states[campaign_id]

    # Tries to reserve exactly one campaign capacity unit for this request
    def reserve_one_unit(
        self,
        campaign_id: int,
    ) -> LeaseReservationResult:

        # Get this worker's local lease state for this campaign
        state = self._get_campaign_state(campaign_id)

        while True:

            # it protects this campaign's local capacity/refill values
            with state.lock:

                # If we already have capacity locally, we can consume one unit & return
                if state.remaining_units > 0:
                    state.remaining_units -= 1
                    return LeaseReservationResult.RESERVED

                # If postgres has already told us (in a previous request) there is no more remaining capacity
                if state.no_more_refills:
                    return LeaseReservationResult.NO_UNLEASED_CAPACITY

                # If someone else is already refilling this campaign, we wait for them
                if state.refill_in_progress:
                    state.condition.wait()

                    # Once we wake up, go back and check everything again
                    continue

                # remaining_units = 0, so this worker will have to refill from db, since no one else is doing it right now
                state.refill_in_progress = True
                break

        # We are outside the lock now.
        # Before calling the db, Create one ID for this logical refill and reuse it if we retry.
        lease_request_id = str(uuid4())

        lease_result = None

        # Try Postgres maximum twice using the SAME lease_request_id
        for attempt in range(2):
            try:
                lease_result = self._acquire_durable_lease(
                    campaign_id=campaign_id,
                    lease_request_id=lease_request_id,
                )
                break

            except Exception:
                if attempt == 1:
                    lease_result = None

        # We are changing local state once again now, so use the campaign lock again
        with state.lock:

            # This request is no longer refilling
            state.refill_in_progress = False

            # Checking if lease_result is an actual CampaignLease row
            if isinstance(lease_result, CampaignLease):

                # Add the new leased units into this worker's local pool
                state.remaining_units += lease_result.granted_units

                # Reserve one of them immediately for this request
                state.remaining_units -= 1

                # Wake up requests that were waiting for this refill
                state.condition.notify_all()

                return LeaseReservationResult.RESERVED

            # Postgres confirmed that it has nothing more left to lease
            if lease_result == CampaignLeaseResult.NO_UNLEASED_CAPACITY:

                # Remember this permanently so future requests fast-fail locally
                state.no_more_refills = True

                # Wake up everyone waiting
                state.condition.notify_all()

                return LeaseReservationResult.NO_UNLEASED_CAPACITY

            # DB failed twice or returned some other unusable result
            state.condition.notify_all()

            return LeaseReservationResult.REFILL_FAILED
    
    # Gives one reserved unit back when the allocation definitely did not happen
    def refund_one_unit(
        self,
        campaign_id: int,
    ) -> None:

        # Get this worker's local state for this campaign
        state = self._get_campaign_state(campaign_id)

        # Safely update the local counter
        with state.lock:
            state.remaining_units += 1
            
    