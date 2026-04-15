"""
Raft node implementation – Q3 (leader election) + Q4 (log replication)

Each node runs both a gRPC server (receiving RPCs) and a background
thread that drives the Raft state machine (election timer, heartbeat).
"""

import os
import random
import threading
import time
import grpc
from concurrent import futures

# Generated from raft.proto at Docker build time
from shared.gen import raft_pb2, raft_pb2_grpc

# ─────────────────────────────────────────────────────────────────────────────
#  Configuration helpers
# ─────────────────────────────────────────────────────────────────────────────

NODE_ID   = os.getenv("NODE_ID", "node1")
PORT      = int(os.getenv("PORT", "50060"))

# Comma-separated list of other peers: "node2:50060,node3:50060,..."
PEERS_ENV = os.getenv("PEERS", "")
PEERS: list[str] = [p.strip() for p in PEERS_ENV.split(",") if p.strip()]

# Dynamically registered nodes that are NOT in the static PEERS list
# (e.g. raft-node6 started by TC1).  node_id -> "host:port"
registered_members: dict[str, str] = {}

HEARTBEAT_INTERVAL = 1.0          # seconds – leader sends HB every 1 s
ELECTION_TIMEOUT   = random.uniform(1.5, 3.0)   # seconds – unique per node

print(f"[{NODE_ID}] starting  port={PORT}  peers={PEERS}  "
      f"election_timeout={ELECTION_TIMEOUT:.2f}s", flush=True)

# ─────────────────────────────────────────────────────────────────────────────
#  State
# ─────────────────────────────────────────────────────────────────────────────

STATE_FOLLOWER  = "follower"
STATE_CANDIDATE = "candidate"
STATE_LEADER    = "leader"

lock          = threading.Lock()
state         = STATE_FOLLOWER
current_term  = 0
voted_for: str | None = None     # node_id we voted for in current term
leader_id: str | None = None

# ── Timers ──
last_heartbeat = time.time()     # follower: when did we last hear from leader

# ── Q4 log + state machine ──
# log entries: list of dicts {term, index, operation}
log: list[dict] = []
commit_index = -1     # index of last *committed* entry  (c in spec, 0-based, -1=none)
last_applied = -1     # last entry applied to state machine

# Simple key/value state machine
kv_store: dict[str, str] = {}

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _stub(peer: str):
    """Create a short-lived gRPC stub for the given peer address."""
    ch = grpc.insecure_channel(peer)
    return raft_pb2_grpc.RaftServiceStub(ch), ch


def _log_entries_proto():
    """Convert in-memory log to proto repeated LogEntry."""
    return [
        raft_pb2.LogEntry(term=e["term"], index=e["index"], operation=e["operation"])
        for e in log
    ]


def _apply_entries_up_to(idx: int):
    """Apply all committed entries up to (and including) idx to the state machine."""
    global last_applied
    while last_applied < idx:
        last_applied += 1
        entry = log[last_applied]
        _execute_operation(entry["operation"])


def _execute_operation(operation: str) -> str:
    """Execute a KV operation and return the result string."""
    parts = operation.strip().split()
    if not parts:
        return "ERROR: empty operation"
    cmd = parts[0].upper()
    if cmd == "SET" and len(parts) >= 3:
        key, val = parts[1], parts[2]
        kv_store[key] = val
        return f"OK"
    elif cmd == "GET" and len(parts) >= 2:
        key = parts[1]
        return kv_store.get(key, "(nil)")
    elif cmd == "DEL" and len(parts) >= 2:
        key = parts[1]
        existed = kv_store.pop(key, None)
        return "1" if existed is not None else "0"
    else:
        return f"ERROR: unknown command '{operation}'"


def _majority() -> int:
    """Return the number of votes needed for a majority (including self)."""
    total = 1 + len(PEERS)
    return (total // 2) + 1


# ─────────────────────────────────────────────────────────────────────────────
#  gRPC Servicer
# ─────────────────────────────────────────────────────────────────────────────

class RaftServicer(raft_pb2_grpc.RaftServiceServicer):

    # ── RequestVote ─────────────────────────────────────────────────────────
    def RequestVote(self, request: raft_pb2.RequestVoteRequest, context):
        global current_term, voted_for, state, last_heartbeat

        print(f"Node {NODE_ID} runs RPC RequestVote called by Node {request.candidate_id}",
              flush=True)

        with lock:
            # Update term if needed
            if request.term > current_term:
                current_term = request.term
                voted_for    = None
                _become_follower()

            grant = False
            if request.term < current_term:
                grant = False
            elif voted_for in (None, request.candidate_id):
                # Check log is at least as up-to-date as ours
                my_last_term  = log[-1]["term"]  if log else 0
                my_last_index = log[-1]["index"] if log else -1
                cand_ok = (request.last_log_term > my_last_term) or \
                          (request.last_log_term == my_last_term and
                           request.last_log_index >= my_last_index)
                if cand_ok:
                    grant     = True
                    voted_for = request.candidate_id
                    last_heartbeat = time.time()   # reset election timer

            return raft_pb2.RequestVoteResponse(
                term=current_term,
                vote_granted=grant,
            )

    # ── AppendEntries (heartbeat + log replication) ──────────────────────────
    def AppendEntries(self, request: raft_pb2.AppendEntriesRequest, context):
        global current_term, state, voted_for, leader_id, last_heartbeat
        global log, commit_index

        caller = request.leader_id
        print(f"Node {NODE_ID} runs RPC AppendEntries called by Node {caller}",
              flush=True)

        with lock:
            if request.term < current_term:
                return raft_pb2.AppendEntriesResponse(
                    term=current_term, success=False, node_id=NODE_ID)

            # Valid leader — update term & reset timer
            if request.term > current_term:
                current_term = request.term
                voted_for    = None
            _become_follower()
            leader_id      = request.leader_id
            last_heartbeat = time.time()

            # ── Log replication (Q4) ──────────────────────────────────────
            if request.entries:
                # Overwrite our log with the leader's full log
                log = [
                    {"term": e.term, "index": e.index, "operation": e.operation}
                    for e in request.entries
                ]

            # Apply committed entries
            new_commit = request.commit_index
            if new_commit > commit_index:
                commit_index = min(new_commit, len(log) - 1)
                _apply_entries_up_to(commit_index)

            return raft_pb2.AppendEntriesResponse(
                term=current_term, success=True, node_id=NODE_ID)

    # ── ClientRequest (Q4) ───────────────────────────────────────────────────
    def ClientRequest(self, request: raft_pb2.ClientRequestMessage, context):
        global log, commit_index
        import json as _json

        op = request.operation

        # ── Internal status/log queries from gateway dashboard ────────────
        if op == "_STATUS_":
            with lock:
                return raft_pb2.ClientRequestResponse(
                    success=True,
                    result=state,
                    leader_id=leader_id or "",
                )

        if op == "_LOG_":
            with lock:
                payload = _json.dumps({
                    "log": [{"index": e["index"], "term": e["term"],
                              "operation": e["operation"]} for e in log],
                    "commit_index": commit_index,
                })
            return raft_pb2.ClientRequestResponse(
                success=True,
                result=payload,
                leader_id=leader_id or NODE_ID,
            )

        # ── Dynamic member registration (new nodes announce themselves) ──────
        if op.startswith("_REGISTER_ "):
            parts = op.split()
            if len(parts) == 3:
                reg_id, reg_addr = parts[1], parts[2]
                with lock:
                    registered_members[reg_id] = reg_addr
                print(f"[{NODE_ID}] registered new member {reg_id} @ {reg_addr}", flush=True)
            return raft_pb2.ClientRequestResponse(
                success=True, result="OK", leader_id=leader_id or "")

        # ── Return all known cluster members (used by gateway for discovery) ──
        if op == "_MEMBERS_":
            with lock:
                members: dict[str, str] = {NODE_ID: f"{NODE_ID}:{PORT}"}
                for peer in PEERS:
                    nid = peer.split(":")[0]
                    members[nid] = peer
                members.update(registered_members)
            return raft_pb2.ClientRequestResponse(
                success=True,
                result=_json.dumps(members),
                leader_id=leader_id or "",
            )

        print(f"Node {NODE_ID} runs RPC ClientRequest called by client  "
              f"op=\'{op}\'", flush=True)

        with lock:
            if state != STATE_LEADER:
                # Forward to leader if we know who it is
                if leader_id:
                    return _forward_to_leader(request.operation)
                return raft_pb2.ClientRequestResponse(
                    success=False,
                    result="No leader known – try again shortly",
                    leader_id="",
                )

            # ── We are the leader ─────────────────────────────────────────
            new_index = len(log)
            entry = {"term": current_term, "index": new_index, "operation": op}
            log.append(entry)
            print(f"[{NODE_ID}] appended entry index={new_index} op='{op}'",
                  flush=True)

        # Replicate to followers and wait for majority ACK
        acks      = 1   # count self
        needed    = _majority()
        peer_lock = threading.Lock()

        def replicate(peer: str):
            nonlocal acks
            try:
                print(f"Node {NODE_ID} sends RPC AppendEntries to Node {peer}", flush=True)
                stub, ch = _stub(peer)
                with lock:
                    entries_proto = _log_entries_proto()
                    ci            = commit_index
                    term          = current_term
                resp = stub.AppendEntries(
                    raft_pb2.AppendEntriesRequest(
                        term=term,
                        leader_id=NODE_ID,
                        entries=entries_proto,
                        commit_index=ci,
                    ),
                    timeout=2,
                )
                ch.close()
                if resp.success:
                    with peer_lock:
                        acks += 1
            except Exception as exc:
                print(f"[{NODE_ID}] replicate to {peer} failed: {exc}", flush=True)

        all_peers = list(dict.fromkeys(list(PEERS) + list(registered_members.values())))
        threads = [threading.Thread(target=replicate, args=(p,), daemon=True) for p in all_peers]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.5)

        if acks >= needed:
            with lock:
                # Commit and apply
                commit_index = new_index
                _apply_entries_up_to(commit_index)
                result = _execute_operation(op)
            print(f"[{NODE_ID}] committed index={new_index}  result='{result}'", flush=True)
            return raft_pb2.ClientRequestResponse(
                success=True,
                result=result,
                leader_id=NODE_ID,
            )
        else:
            # Roll back (simplified – remove uncommitted entry)
            with lock:
                if log and log[-1]["index"] == new_index:
                    log.pop()
            return raft_pb2.ClientRequestResponse(
                success=False,
                result=f"Failed to replicate (got {acks}/{needed} acks)",
                leader_id=NODE_ID,
            )


def _forward_to_leader(operation: str) -> raft_pb2.ClientRequestResponse:
    """Forward a ClientRequest to the known leader (called while holding lock)."""
    target_peer = None
    for p in PEERS:
        # peer address format: "nodeX:PORT"  — match by prefix
        if p.startswith(leader_id + ":") or p == leader_id:
            target_peer = p
            break
    # Also check dynamically registered nodes (e.g. raft-node6)
    if not target_peer and leader_id in registered_members:
        target_peer = registered_members[leader_id]
    if not target_peer:
        return raft_pb2.ClientRequestResponse(
            success=False,
            result=f"Cannot reach leader {leader_id}",
            leader_id=leader_id or "",
        )
    try:
        print(f"Node {NODE_ID} sends RPC ClientRequest to Node {leader_id}", flush=True)
        stub, ch = _stub(target_peer)
        resp = stub.ClientRequest(
            raft_pb2.ClientRequestMessage(operation=operation), timeout=5)
        ch.close()
        return resp
    except Exception as exc:
        return raft_pb2.ClientRequestResponse(
            success=False,
            result=f"Forward failed: {exc}",
            leader_id=leader_id or "",
        )


# ─────────────────────────────────────────────────────────────────────────────
#  State transitions (must be called with lock held)
# ─────────────────────────────────────────────────────────────────────────────

def _become_follower():
    global state
    if state != STATE_FOLLOWER:
        print(f"[{NODE_ID}] → FOLLOWER  term={current_term}", flush=True)
    state = STATE_FOLLOWER


def _become_candidate():
    global state, current_term, voted_for, last_heartbeat
    state        = STATE_CANDIDATE
    current_term += 1
    voted_for    = NODE_ID
    last_heartbeat = time.time()
    print(f"[{NODE_ID}] → CANDIDATE  term={current_term}", flush=True)


def _become_leader():
    global state, leader_id
    state     = STATE_LEADER
    leader_id = NODE_ID
    print(f"[{NODE_ID}] → LEADER  term={current_term}  🎉", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Election runner (background thread)
# ─────────────────────────────────────────────────────────────────────────────

def _run_election():
    """Called when a follower's election timer fires. Returns True if we won."""
    global current_term, voted_for

    with lock:
        _become_candidate()
        term           = current_term
        last_idx       = log[-1]["index"] if log else -1
        last_term_val  = log[-1]["term"]  if log else 0

    votes_received = 1   # vote for self

    def request_vote(peer: str):
        nonlocal votes_received
        try:
            print(f"Node {NODE_ID} sends RPC RequestVote to Node {peer}", flush=True)
            stub, ch = _stub(peer)
            resp = stub.RequestVote(
                raft_pb2.RequestVoteRequest(
                    term=term,
                    candidate_id=NODE_ID,
                    last_log_index=last_idx,
                    last_log_term=last_term_val,
                ),
                timeout=1,
            )
            ch.close()
            with lock:
                if resp.term > current_term:
                    # Step down immediately
                    globals()["current_term"] = resp.term
                    globals()["voted_for"]    = None
                    _become_follower()
                    return
            if resp.vote_granted:
                nonlocal votes_received
                votes_received += 1
        except Exception as exc:
            print(f"[{NODE_ID}] RequestVote to {peer} failed: {exc}", flush=True)

    threads = [threading.Thread(target=request_vote, args=(p,), daemon=True) for p in PEERS]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=1.5)

    with lock:
        if state != STATE_CANDIDATE:
            return False    # lost candidacy (someone else won or higher term seen)
        if votes_received >= _majority():
            _become_leader()
            return True
        else:
            print(f"[{NODE_ID}] lost election (got {votes_received} votes)", flush=True)
            _become_follower()
            return False


# ─────────────────────────────────────────────────────────────────────────────
#  Heartbeat sender (leader background thread)
# ─────────────────────────────────────────────────────────────────────────────

def _send_heartbeats():
    """Leader periodically sends AppendEntries (with full log for replication)."""
    with lock:
        term          = current_term
        entries_proto = _log_entries_proto()
        ci            = commit_index

    def hb_to(peer: str):
        try:
            print(f"Node {NODE_ID} sends RPC AppendEntries to Node {peer}", flush=True)
            stub, ch = _stub(peer)
            resp = stub.AppendEntries(
                raft_pb2.AppendEntriesRequest(
                    term=term,
                    leader_id=NODE_ID,
                    entries=entries_proto,
                    commit_index=ci,
                ),
                timeout=1,
            )
            ch.close()
            with lock:
                if resp.term > current_term:
                    globals()["current_term"] = resp.term
                    globals()["voted_for"]    = None
                    _become_follower()
        except Exception as exc:
            print(f"[{NODE_ID}] heartbeat to {peer} failed: {exc}", flush=True)

    all_peers = list(dict.fromkeys(list(PEERS) + list(registered_members.values())))
    threads = [threading.Thread(target=hb_to, args=(p,), daemon=True) for p in all_peers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=1.5)


# ─────────────────────────────────────────────────────────────────────────────
#  Main loop (background thread)
# ─────────────────────────────────────────────────────────────────────────────

def _raft_loop():
    global ELECTION_TIMEOUT
    while True:
        with lock:
            s = state

        if s == STATE_LEADER:
            _send_heartbeats()
            time.sleep(HEARTBEAT_INTERVAL)

        elif s == STATE_FOLLOWER:
            elapsed = time.time() - last_heartbeat
            if elapsed >= ELECTION_TIMEOUT:
                print(f"[{NODE_ID}] election timeout ({elapsed:.2f}s >= "
                      f"{ELECTION_TIMEOUT:.2f}s) — starting election", flush=True)
                _run_election()
                # Pick a new random timeout for next round
                ELECTION_TIMEOUT = random.uniform(1.5, 3.0)
            else:
                time.sleep(0.05)   # poll at 50 ms

        elif s == STATE_CANDIDATE:
            # _run_election already started in follower branch; just yield
            time.sleep(0.1)



# ─────────────────────────────────────────────────────────────────────────────
#  Bootstrap: new node pulls log from any reachable peer on startup
# ─────────────────────────────────────────────────────────────────────────────

def _bootstrap_from_peers():
    """
    Called once at startup (before the main loop).
    Contacts each known peer and requests a full AppendEntries so we receive
    the current committed log immediately — rather than waiting for the leader
    to discover us (which it never will, since we're not in its PEERS list).
    """
    global log, commit_index, last_applied, last_heartbeat, leader_id, current_term

    if not PEERS:
        return

    print(f"[{NODE_ID}] bootstrapping from peers…", flush=True)

    for peer in PEERS:
        try:
            stub, ch = _stub(peer)
            # Ask the peer for its full log via the _LOG_ introspection command
            resp = stub.ClientRequest(
                raft_pb2.ClientRequestMessage(operation="_LOG_"),
                timeout=3,
            )
            ch.close()

            if not resp.success:
                continue

            import json as _json
            data = _json.loads(resp.result)
            peer_log    = data.get("log", [])
            peer_ci     = data.get("commit_index", -1)
            peer_leader = resp.leader_id

            if not peer_log and peer_ci < 0:
                # Peer has nothing — try next
                continue

            with lock:
                # Copy the log
                log = [
                    {"term": e["term"], "index": e["index"], "operation": e["operation"]}
                    for e in peer_log
                ]
                # Apply committed entries
                if peer_ci >= 0:
                    commit_index = peer_ci
                    _apply_entries_up_to(commit_index)

                if peer_leader:
                    leader_id = peer_leader

                # Reset heartbeat timer so we don't immediately start an election
                last_heartbeat = time.time()

            print(
                f"[{NODE_ID}] bootstrap done via {peer}: "
                f"log_len={len(log)}  commit_index={commit_index}  leader={leader_id}",
                flush=True,
            )
            break   # stop after first peer that has data

        except Exception as exc:
            print(f"[{NODE_ID}] bootstrap from {peer} failed: {exc}", flush=True)

    # Announce this node to every peer so they add us to their registered_members.
    # This lets the gateway discover us via _MEMBERS_ even though we are not in
    # any existing node's static PEERS list.
    reg_op = f"_REGISTER_ {NODE_ID} {NODE_ID}:{PORT}"
    for peer in PEERS:
        try:
            stub, ch = _stub(peer)
            stub.ClientRequest(
                raft_pb2.ClientRequestMessage(operation=reg_op), timeout=1)
            ch.close()
        except Exception:
            pass

    print(f"[{NODE_ID}] bootstrap: announced self to peers", flush=True)

# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=20))
    raft_pb2_grpc.add_RaftServiceServicer_to_server(RaftServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{PORT}")
    server.start()
    print(f"[{NODE_ID}] gRPC server listening on {PORT}", flush=True)

    # Bootstrap: pull committed log from peers before entering election loop
    _bootstrap_from_peers()

    bg = threading.Thread(target=_raft_loop, daemon=True)
    bg.start()

    server.wait_for_termination()


if __name__ == "__main__":
    serve()
