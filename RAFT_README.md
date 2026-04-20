# CSE5306 – Project 3: Distributed Library System with Raft Consensus

---

## Overview

This project extends the original distributed library management system with a full implementation of the **Raft consensus algorithm** (Q3 + Q4), running as a 5-node cluster alongside the existing library microservices.

- **Q3** – Leader election with heartbeat and election timeouts
- **Q4** – Log replication with a key/value state machine and client forwarding

All nodes are containerized and communicate via gRPC. A test suite of **5 failure scenarios** validates correctness under real failures using `docker pause`, `docker stop`, and dynamic container creation.

---

## Project Structure

```
.
├── proto/
│   ├── raft.proto              # Q3+Q4 gRPC service definition (new)
│   ├── users.proto
│   ├── catalog.proto
│   ├── inventory.proto
│   ├── circulation.proto
│   └── audit.proto
├── services/
│   ├── raft/
│   │   ├── app.py              # Raft node implementation (new)
│   │   └── Dockerfile
│   ├── gateway/
│   │   ├── app.py              # Extended with /raft routes (modified)
│   │   ├── dockerfile
│   │   └── templates/
│   │       └── raft.html       # Raft dashboard UI (new)
│   ├── users/
│   ├── catalog/
│   ├── inventory/
│   ├── circulation/
│   └── audit/
├── shared/
│   └── gen/                    # Auto-generated protobuf stubs (at build time)
├── tests/
│   └── test_raft.py            # 5 failure test cases (new)
└── docker-compose.yml
```

---

## Tech Stack

- **Python 3.11** — all services
- **gRPC / Protocol Buffers** — inter-node communication
- **FastAPI + Jinja2** — HTTP gateway and web UI
- **Docker + Docker Compose** — containerization (11 containers total)

---

## Raft Implementation Details

### Proto file: `proto/raft.proto`

Three RPC methods defined in `RaftService`:

| RPC | Purpose |
|-----|---------|
| `RequestVote` | Q3 – Candidate requests votes during election |
| `AppendEntries` | Q3+Q4 – Leader sends heartbeats and replicates log entries |
| `ClientRequest` | Q4 – Client submits a KV operation; non-leaders forward to leader |

### Node behavior (`services/raft/app.py`)

**Q3 – Leader Election:**
- All nodes start as **follower**
- Each node picks a random election timeout in **[1.5s, 3.0s]**
- Heartbeat interval is fixed at **1.0s**
- If no heartbeat received within timeout → transitions to **candidate**, increments term, votes for self, sends `RequestVote` to all peers
- If majority votes received → becomes **leader**, starts sending `AppendEntries` heartbeats every 1s
- If higher term seen → steps back down to follower

**Q4 – Log Replication:**
- Leader appends `<operation, term, index>` to its log on each `ClientRequest`
- Leader sends its **full log** + `commit_index` to all followers on next heartbeat
- Followers overwrite their log and apply all committed entries to their KV state machine
- Leader commits when a **majority of ACKs** received, then returns result to client
- Non-leader nodes **forward** `ClientRequest` to the known leader automatically

**KV State Machine** supports three operations:
```
SET <key> <value>   → stores value, returns OK
GET <key>           → returns value or (nil)
DEL <key>           → deletes key, returns 1 or 0
```

**RPC logging format** (printed on every call):
```
# Client side:
Node <node_id> sends RPC <rpc_name> to Node <node_id>

# Server side:
Node <node_id> runs RPC <rpc_name> called by Node <node_id>
```

**Bootstrap (late-joining nodes):**
When a new node starts with a non-empty `PEERS` list, it immediately contacts peers to pull the current committed log before entering the election loop. This allows a late-joining 6th node to catch up without disrupting the existing leader.

---

## Cluster Architecture

```
                    ┌─────────────────────────────────┐
                    │     Docker Compose Network        │
                    │                                   │
  Browser/Test ───▶ │  Gateway (HTTP :8080)             │
                    │      │                            │
                    │      ▼ gRPC                       │
                    │  ┌──────────────────────────┐     │
                    │  │   Raft Cluster (5 nodes) │     │
                    │  │  node1  node2  node3     │     │
                    │  │  node4  node5            │     │
                    │  └──────────────────────────┘     │
                    │                                   │
                    │  Library Services (users, catalog,│
                    │  inventory, circulation, audit)   │
                    └─────────────────────────────────────┘
```

**Port mappings** (host → container):

| Container | Host Port | Purpose |
|-----------|-----------|---------|
| `library-gateway` | 8080 | Web UI + REST API |
| `raft-node1` | 50061 | gRPC (for test runner) |
| `raft-node2` | 50062 | gRPC |
| `raft-node3` | 50063 | gRPC |
| `raft-node4` | 50064 | gRPC |
| `raft-node5` | 50065 | gRPC |

---

## Prerequisites

- **Docker Desktop** (with Linux containers)
- **Python 3.11+** with pip
- **Git**

Install Python test dependencies:

```bash
pip install grpcio grpcio-tools protobuf
```

---

## Running the Project

### Step 1 – Build and start all containers

```bash
docker compose build --no-cache
docker compose up
```

Wait until you see all 5 raft nodes printing heartbeat/election messages in the logs. Leader election completes within ~3 seconds.

### Step 2 – Access the web UI

Open **http://localhost:8080** in a browser. Click **"⚡ Raft Cluster (KV Store)"** to interact with the cluster, submit KV commands, and view the replicated log and cluster status.

### Step 3 – Generate local proto stubs (required for test runner)

The test runner runs **outside Docker** and needs local stubs to talk gRPC:

```bash
python -m grpc_tools.protoc \
  -I proto \
  --python_out=shared/gen \
  --grpc_python_out=shared/gen \
  proto/raft.proto
```

---

## Running the Failure Tests

### Run all 5 tests at once

```bash
docker rm -f raft-node6   # clean up any leftover from TC1
python tests/test_raft.py
```

Expected total runtime: **~90 seconds**. Expected result: **19/19 assertions passed**.

### Run a single test

Open `tests/test_raft.py`, scroll to `main()` at the bottom, and comment out the tests you don't want:

```python
tc1_new_node_joins()
# tc2_leader_crash()
# tc3_follower_crash()
# tc4_minority_partition()
# tc5_concurrent_requests()
```

### Reset the cluster between test runs

```bash
docker compose down
docker rm -f raft-node6
docker compose up -d
```

Wait ~5 seconds for leader election, then re-run.

---

## Test Case Descriptions

### TC1 — New Node Joins Running Cluster

**What it tests:** A 6th Raft node (`raft-node6`) starts after the cluster already has a committed log. Since the existing leader doesn't know about node6 (peers are fixed at startup), node6 must **bootstrap** by actively pulling the log from a peer on startup.

**Steps:**
1. Commits 3 entries (`SET tc1_key0..2`) on the existing 5-node cluster
2. Starts `raft-node6` as a new Docker container on the same network
3. Waits 10 seconds for node6 to bootstrap and sync
4. Asserts node6's `commit_index` and log length match the leader's
5. Removes the node6 container

**Assertions:** node6 commit_index ≥ leader's commit_index; log lengths match

**Failure simulated:** Late node join with no prior state

---

### TC2 — Leader Crash → Re-election

**What it tests:** The current leader is hard-stopped. The remaining 4 nodes must detect the missing heartbeat, hold an election, and elect a new leader — all within the maximum election timeout window (≤8s). The new leader must also accept and commit new entries.

**Steps:**
1. Identifies current leader
2. Stops the leader container (`docker stop`)
3. Waits up to 8 seconds for a new leader to be elected
4. Commits a new entry on the new leader
5. Restarts the crashed node and verifies it rejoins as follower

**Assertions:** New leader elected within 8s; new leader ≠ crashed leader; new leader can commit; crashed node rejoins as follower

**Failure simulated:** Leader crash / hard stop

---

### TC3 — Single Follower Crash (Majority Intact)

**What it tests:** One follower is paused (simulating a crash or network partition to that node). With 4/5 nodes active the cluster still has a majority (3 needed) and must continue committing. When the follower is unpaused, it must catch up via the next heartbeat.

**Steps:**
1. Identifies leader and selects a follower as victim
2. Pauses the victim container (`docker pause`)
3. Commits 2 new entries — verifies both succeed with 4/5 nodes
4. Unpauses the victim
5. Waits for heartbeat propagation and verifies victim's commit_index matches

**Assertions:** Both entries committed during outage; victim catches up after rejoin

**Failure simulated:** Single node failure (still above majority threshold)

---

### TC4 — Minority Partition (2 Followers Isolated)

**What it tests:** Two followers are paused simultaneously, leaving 3/5 nodes active (still a majority). The leader must keep committing. When both paused nodes are restored, they must receive the full log and converge to the correct state.

**Steps:**
1. Pauses 2 followers simultaneously
2. Commits 3 entries on the 3-node majority — verifies all succeed
3. Unpauses both nodes
4. Waits 5 seconds for catch-up
5. Verifies both nodes reach the correct commit_index

**Assertions:** All 3 entries committed during partition; both partitioned nodes catch up

**Failure simulated:** Minority network partition (2 of 5 nodes isolated)

---

### TC5 — Concurrent Client Requests (Log Consistency)

**What it tests:** 8 client threads simultaneously send `SET tc5_shared clientN` to the leader. Raft must serialize all requests into a consistent log order. After all commits complete, every node must agree on the same `commit_index`, the same final value for the key, and the same log length — proving no split-brain occurred.

**Steps:**
1. Fires 8 concurrent `ClientRequest` RPCs from separate threads
2. Verifies all 8 committed successfully
3. Waits for heartbeat propagation
4. Checks all 5 nodes have identical `commit_index`
5. Checks all 5 nodes return the same value for `GET tc5_shared`
6. Checks all 5 nodes have identical log length

**Assertions:** 8/8 commits succeed; all nodes agree on commit_index, final value, and log length

**Failure simulated:** Race condition / concurrent write contention

---

## Expected Test Output

```
Raft Failure Test Suite
Connecting to cluster…
  ✔ raft-node1 (localhost:50061) → state=leader
  ✔ raft-node2 (localhost:50062) → state=follower
  ✔ raft-node3 (localhost:50063) → state=follower
  ✔ raft-node4 (localhost:50064) → state=follower
  ✔ raft-node5 (localhost:50065) → state=follower

... (test output) ...

  TEST SUMMARY
  TC1  [PASS]   ✔ raft-node6 caught up
  TC2  [PASS]   ✔ New leader elected in 2.2s
  TC3  [PASS]   ✔ Both entries committed with 4/5 nodes
  TC4  [PASS]   ✔ Both partitioned nodes caught up
  TC5  [PASS]   ✔ All nodes agree on commit_index and final value

  19/19 assertions passed
```

---

## Troubleshooting

**Gateway fails to start (grpcio version mismatch)**
The `services/gateway/dockerfile` must regenerate proto stubs at build time using the same grpcio version installed. The current dockerfile handles this — if you see a version mismatch error, run:
```bash
docker compose build --no-cache gateway
docker compose up -d
```

**Test runner can't reach nodes (UNREACHABLE)**
Verify the cluster is running and ports are exposed:
```bash
docker compose ps
```
All 5 raft-node containers should show `running` with ports `0.0.0.0:5006X->50060/tcp`.

**TC1 fails (node6 doesn't catch up)**
Ensure you're using the correct Docker image name. Check with:
```bash
docker images | grep raft
```
In `tests/test_raft.py` inside `run_new_container()`, the image name must match what Docker Compose built — typically `<project-folder-name>-raft-node1`.

**Cluster in bad state after a failed test run**
```bash
docker compose down
docker rm -f raft-node6
docker compose up -d
```
