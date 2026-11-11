# Project Rush

A real-time ad allocation backend for live streaming.

Rush takes a viewer, a set of eligible campaigns, and an ad-break duration, and tries to build the best valid ad pod it can.

The interesting part is not really *choosing an ad*.

It is doing that while multiple workers are competing for the same campaigns, viewers have frequency caps, campaign capacity is finite, ads have different durations, competing brands cannot appear together, state can become stale, and Redis can fail at exactly the wrong time.

The main rule behind the project is:

> **Optimization can speculate. Commitment cannot.**

---

## Why I built this

The idea came from looking at different parts of how large streaming and advertising systems work.

Google Ad Manager got me interested in things like targeting, frequency caps, video ad pods and PPID.

JioHotstar made me think about the scale of the problem. A live cricket match can push a huge number of viewers into an ad break at almost the same time, and the system still has to make a useful decision quickly.

Netflix got me thinking more about the viewer side of advertising: personalization, controlling ad load and making sure the same person is not repeatedly shown the same campaign.

I also came across Google's **SHALE** paper on guaranteed ad allocation. Rush does not implement SHALE, but it made me think about another problem: even if a campaign is eligible, you probably do not want to burn through its entire budget too early or let it fall far behind schedule.

That eventually led to Rush.

The question became:

> Given a viewer, a 30-second break and a large pool of campaigns, how do I build a valid ad pod quickly without races or failures creating extra campaign capacity?

---

## How it works

At a high level:
```
Viewer request
      |
      v
Find candidate campaigns
      |
      v
Check targeting + lifecycle
      |
      v
Calculate pacing
      |
      v
Build the best fitting pod
      |
      v
Reserve campaign capacity locally
      |
      v
Atomically commit in Redis
      |
      v
Return commercial / House / partial pod
```

The system uses three different kinds of state.
```
PostgreSQL -> durable campaign state and capacity

Redis      -> frequency caps, idempotency,
              pacing and atomic commitment

Worker RAM -> candidate index, local leases
              and pod optimization
```

I tried to keep each layer responsible for the kind of state it is actually good at handling instead of forcing everything into one database.

---

## Candidate discovery

Every request should not scan every campaign.

Each worker therefore keeps an in-memory inverted index.

Something like:
```
Mumbai   -> {1, 4, 8}
MOBILE   -> {1, 3, 8}
PREMIUM  -> {1, 6, 8}
```

For a Mumbai + MOBILE + PREMIUM viewer, Rush intersects those sets and works with the much smaller result.

The index is fast, but it can become stale.

So Redis also stores the current index version. A worker can read from a slightly old snapshot, but it cannot commit a decision if Redis says that snapshot is no longer current.

---

## Campaign capacity

Campaign capacity is durable in PostgreSQL.

I did not want every ad decision to require a PostgreSQL transaction, so workers lease capacity in chunks.

For example:
```
Campaign has 10,000 units

Worker A leases 100
Worker B leases 100
Worker C leases 100

PostgreSQL now has 9,700 unleased units left.
```

Those workers can spend their leased units locally until they need a refill.

This keeps the hot path cheap while still making PostgreSQL the source of truth for how much commercial authority actually exists.

Lease requests are idempotent as well, so a lost response cannot accidentally issue the same worker another block of capacity.

---

## Frequency caps

A viewer might be allowed to see a campaign only three times per hour.

A normal:
```
GET count
then
INCR count
```

is not safe under concurrency.

Two requests can both read the same value and both decide that one slot is still available.

Rush checks and updates frequency state atomically in Redis Lua.

The same commit also handles request idempotency and index-version validation.

---

## Pacing

Two campaigns can both be valid but have very different delivery progress.

Rush uses a simple pacing score:
```
expected delivery
-
actual delivery
```

If a campaign is halfway through its lifetime but has delivered only 30% of its capacity, it gets a positive score.

If it is already at 70%, it gets a negative one.

This is intentionally simple.

I looked at SHALE while thinking about this, but V1.1 does not try to implement a full guaranteed-delivery optimizer.

Pacing only helps choose between valid campaigns. It can never make an invalid campaign eligible.

---

## Building the ad pod

Say the break is 30 seconds.
```
A = 20s
B = 15s
C = 15s
```

Picking the first good-looking campaign gives:
```
A = 20 / 30 seconds
```

But:
```
B + C = 30 / 30 seconds
```

is better.

So Rush uses a bounded dynamic-programming style search rather than a simple greedy selection.

The objective is deliberately small:
```
1. Fill as much of the break as possible.
2. If two pods fill the same amount, prefer better pacing.
```

Campaigns can also belong to competitive groups.

Nike and Adidas, for example, can both belong to `SPORTSWEAR`, which prevents them from appearing in the same pod.

---

## Proposal vs commit

This is probably the most important part of Rush.

The optimizer might decide:
```
Nike + Samsung + Spotify
```

But that is only a proposal.

By the time the decision is committed:

- another request may have hit the viewer's frequency cap,
- the worker's index may be stale,
- local campaign capacity may be gone.

So Rush first reserves local capacity and then sends the pod to Redis for the final authoritative check.

The Redis Lua script atomically handles:
```
request idempotency
index version
frequency caps
frequency increments
pacing increments
final pod storage
```

A campaign can still be rejected at that point without necessarily killing the rest of the pod.

---

## Failure behaviour

This ended up being one of the parts I enjoyed most.

Suppose Rush reserves one unit of Nike locally and then Redis explicitly says:
```
FREQUENCY_CAPPED
```

Easy.

Redis proved Nike was not allocated, so the local unit can be refunded.

Now consider this:
```
Rush sends Redis commit
Redis commits it
network dies
Rush receives an error
```

Now Rush does not know whether the allocation happened.

Refunding Nike would be dangerous because Redis may already have committed it.

So V1.1 does something deliberately conservative:

> **If I cannot prove that commercial capacity is safe to reuse, I do not reuse it.**

That unit is burned and Rush falls back to House inventory.

It can cause under-delivery.

It cannot create extra campaign capacity.

The same idea applies to worker crashes. If a worker dies while still holding unused leased capacity, V1.1 leaves that capacity stranded instead of trying to reclaim it without proper fencing.

I would rather lose some delivery than accidentally spend the same authority twice.

---

## Example response
```
{
  "request_id": "req-123",
  "status": "ALLOCATED",
  "slots": [
    {
      "campaign_id": 42,
      "duration_seconds": 15,
      "source": "COMMERCIAL"
    },
    {
      "campaign_id": null,
      "duration_seconds": 15,
      "source": "HOUSE"
    }
  ],
  "filled_duration_seconds": 30,
  "unfilled_duration_seconds": 0
}
```

Rush also supports partial fill when there is no safe way to fill the entire break.

---

## What V1.1 currently has
```
Campaign lifecycle + targeting
In-memory inverted index
Index-version fencing

Hourly viewer frequency caps
Request idempotency

Durable hierarchical capacity leasing
Worker-local reservations
Single-flight lease refills

Campaign pacing

Duration-aware pod optimization
Competitive exclusion

Atomic Redis pod commitment
House fallback
Partial fill

Conservative handling of ambiguous failures
```

The regression suite currently has:
```
75 tests
75 passing
```

---

## Stack
```
Python
FastAPI
PostgreSQL
SQLAlchemy
Alembic
Redis
Redis Lua
Pytest
```

---

## What is intentionally not in V1.1

I stopped V1.1 before turning this into an endless ad-tech simulator.

Things I intentionally left for later include:
```
safe crashed-worker lease reclamation
worker epochs / fencing
durable allocation ledger
Redis reconstruction after total state loss
advanced pacing
creative-level selection
auctions / revenue optimization
CTR prediction
```

The biggest planned V1.2 change is a durable PostgreSQL allocation ledger so Redis can be reconstructed from committed evidence instead of being the only place some allocation state exists.

---

## Benchmarking

Formal load testing is still pending.

I plan to measure:
```
throughput
p50 latency
p95 latency
p99 latency
candidate-set scaling
concurrent request behaviour
```

I am leaving the numbers blank until I actually measure them.

---

## Inspiration

A few things that influenced the way I thought about Rush:

- **Google Ad Manager** — video ad pods, targeting and frequency caps
- **Google PPID** — publisher-controlled viewer identity
- **JioHotstar engineering** — advertising during large live-streaming events
- **Netflix advertising engineering** — personalization, frequency and ad-load thinking
- **SHALE: An Efficient Algorithm for Allocation of Guaranteed Display Advertising** — campaign delivery and pacing

Rush is not intended to reproduce the private architecture of any of these systems.

They were useful because they exposed different pieces of the same problem.

---

## The part I actually care about

Rush started as:

> “How do I choose ads for a live stream?”

By the end, the more interesting question became:

> **What does each component actually know, and what authority should it be allowed to exercise based on that knowledge?**

That question ended up shaping almost everything in the project:
```
leasing
fencing
idempotency
frequency caps
pacing
pod construction
atomic commitment
failure recovery
```

---

### Rush V1.1

**Feature complete**\
**75 / 75 tests passing**\
**Benchmarking pending**
