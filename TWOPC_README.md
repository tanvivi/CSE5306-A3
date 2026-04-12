# CSE5306 – Two-Phase Commit (2PC) for Distributed Checkout

**Authors:** Evelyn Lopez Paz & Aadhitya Kumar

---

## Overview

This component extends the distributed library system with a **Two-Phase Commit (2PC)** protocol for the book checkout operation. Rather than sequentially calling each microservice and risking partial failure, 2PC coordinates an atomic transaction across the **Inventory** and **Users** services: either both commit or neither does.

---

## Why 2PC for Checkout?

The original checkout flow called three services sequentially:

```
1. inventory.GetAvailability()   ← check stock
2. circulation.CheckoutBook()    ← record loan
3. inventory.DecrementCopy()     ← reduce stock
```

This has two failure modes:

- **Partial failure** — if step 2 succeeds but step 3 crashes, the circulation record exists but inventory is never decremented, leaving the system in an inconsistent state with no rollback path.
- **TOCTOU race** — two concurrent checkouts both pass step 1 (stock = 1), then both execute steps 2 and 3, driving stock to −1.

2PC solves both problems by **separating the "can you do this?" vote from the "now do it" commit**, ensuring that the inventory decrement only happens when all participants have agreed.

---

## Architecture

```
Browser
   │  POST /2pc/checkout
   ▼
Gateway (FastAPI)
   │  BeginTransaction (gRPC)
   ▼
2PC Coordinator ──── Phase 1: RequestVote ────▶ twopc-inventory ──▶ inventory service
                                               ▶ twopc-users     ──▶ users service
                │
                │  (all COMMIT → Phase 2: Commit; any ABORT → Phase 2: Abort)
                │
                └── Phase 2: Commit / Abort ──▶ twopc-inventory
                                               ▶ twopc-users
```

Three containers handle 2PC:

| Container | Role |
|-----------|------|
| `twopc-coordinator` | Drives both phases; makes the global decision |
| `twopc-inventory` | Participant: checks stock in Phase 1, decrements in Phase 2 |
| `twopc-users` | Participant: verifies user exists in Phase 1, acknowledges in Phase 2 |

---

## Project Structure

```
.
├── proto/
│   └── twopc.proto                          # gRPC service + message definitions
├── services/
│   ├── twopc/
│   │   ├── coordinator/
│   │   │   ├── app.py                       # Coordinator: drives Phase 1 + Phase 2
│   │   │   └── Dockerfile
│   │   ├── inventory_participant/
│   │   │   ├── app.py                       # Participant: inventory vote + commit
│   │   │   └── Dockerfile
│   │   └── users_participant/
│   │       ├── app.py                       # Participant: user-exists vote
│   │       └── Dockerfile
│   └── gateway/
│       ├── app.py                           # /2pc and /2pc/checkout routes (modified)
│       └── templates/
│           └── twopc.html                   # 2PC UI: form + per-phase result tables
└── shared/
    └── gen/
        ├── twopc_pb2.py                     # Generated protobuf stubs
        └── twopc_pb2_grpc.py                # Generated gRPC stubs
```

---

## Protocol Details

### Proto: `proto/twopc.proto`

**Services:**

| Service | RPC | Direction | Purpose |
|---------|-----|-----------|---------|
| `CoordinatorService` | `BeginTransaction` | Gateway → Coordinator | Start a full 2PC transaction |
| `ParticipantService` | `RequestVote` | Coordinator → Participant | Phase 1: ask if participant can commit |
| `ParticipantService` | `Commit` | Coordinator → Participant | Phase 2: execute the change |
| `ParticipantService` | `Abort` | Coordinator → Participant | Phase 2: roll back (no-op if nothing written) |

### Phase 1 — Vote

The coordinator fans out `RequestVote` to both participants **concurrently**. Each participant independently decides:

- **`twopc-inventory`** — calls `inventory.GetAvailability(book_id)`. Votes `COMMIT` if `available > 0`, otherwise `ABORT`.
- **`twopc-users`** — calls `users.GetUser(user_id)`. Votes `COMMIT` if the user record exists, otherwise `ABORT`.

If either participant is **unreachable**, the coordinator treats it as `ABORT`.

### Phase 2 — Decision

| Phase 1 result | Phase 2 action |
|----------------|----------------|
| All participants voted `COMMIT` | Send `Commit` to all → `GLOBAL COMMIT` |
| Any participant voted `ABORT` | Send `Abort` to all → `GLOBAL ABORT` |

**On Commit:**
- `twopc-inventory` calls `inventory.DecrementCopy(book_id)` — stock is reduced only here.
- `twopc-users` acknowledges — no state change needed (checkout is recorded by the circulation service on the normal path).

**On Abort:**
- Both participants return immediately. No state was modified during the vote phase, so no rollback is needed.

---

## Running 2PC

### Step 1 — Start the system

```bash
docker compose build --no-cache
docker compose up
```

The `twopc-coordinator`, `twopc-inventory`, and `twopc-users` containers start alongside the existing library services.

### Step 2 — Open the 2PC UI

Navigate to **http://localhost:8080** and click **"2-Phase Commit"**, or go directly to **http://localhost:8080/2pc**.

### Step 3 — Submit a checkout transaction

Enter a **User ID** and **Book ID**, then click **Begin Transaction**. The page displays the full transaction result:

- **Transaction ID** — short UUID assigned by the coordinator
- **Phase 1 table** — each participant's vote and reason
- **Phase 2 table** — each participant's commit/abort acknowledgement
- **Banner** — green `GLOBAL COMMIT` or red `GLOBAL ABORT`

---

## Example Outcomes

### Successful checkout (both participants commit)

```
Transaction: a3f1b2c4

Phase 1 — Vote
  inventory   COMMIT   Book available: 3 copies in stock
  users       COMMIT   User 'alice' is registered

Phase 2 — Commit
  inventory   OK       Stock decremented — 2 remaining
  users       OK       User 'alice' authorized for checkout

Result: GLOBAL COMMIT
```

### Failed checkout — book out of stock

```
Transaction: 9d72e1a0

Phase 1 — Vote
  inventory   ABORT    Book out of stock
  users       COMMIT   User 'alice' is registered

Phase 2 — Abort
  inventory   OK       Aborted — no inventory changes made
  users       OK       Aborted — no user record changes made

Result: GLOBAL ABORT
```

### Failed checkout — user not found

```
Transaction: 5c8f3b11

Phase 1 — Vote
  inventory   COMMIT   Book available: 1 copy in stock
  users       ABORT    User 'unknown_user' not found

Phase 2 — Abort
  inventory   OK       Aborted — no inventory changes made
  users       OK       Aborted — no user record changes made

Result: GLOBAL ABORT
```

---

## Troubleshooting

**`twopc-coordinator` exits immediately**
Check that `twopc-inventory` and `twopc-users` are healthy first — the coordinator's `depends_on` waits for them to start but not for their gRPC servers to be ready. If the coordinator retries are exhausted, restart it:
```bash
docker compose restart twopc-coordinator
```

**UI shows no result after submitting**
Verify all three 2PC containers are running:
```bash
docker compose ps | grep twopc
```

**Proto stubs out of date**
If you modify `proto/twopc.proto`, regenerate the stubs:
```bash
python -m grpc_tools.protoc \
  -I proto \
  --python_out=shared/gen \
  --grpc_python_out=shared/gen \
  proto/twopc.proto
```
Then rebuild the affected containers:
```bash
docker compose build --no-cache twopc-coordinator twopc-inventory twopc-users gateway
```
