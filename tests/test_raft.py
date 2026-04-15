#!/usr/bin/env python3
"""
=============================================================================
  Raft Failure Test Suite  –  5 test cases
=============================================================================

Test cases:
  TC1  New node joins a running cluster (log catch-up)
  TC2  Leader crash → re-election → new leader elected
  TC3  Single follower crash → cluster still makes progress (majority intact)
  TC4  Minority partition (2 nodes stopped) → cluster still commits → restore → catch-up
  TC5  Majority crash (4/5 followers stopped, leader alive) → commit blocked → restore → recovery

Requirements:
  pip install grpcio protobuf
  The Raft cluster must be running via:  docker compose up
  This script is run from the repo root:  python tests/test_raft.py

Nodes are exposed on localhost via docker-compose port-mappings
  raft-node1 → localhost:50061
  raft-node2 → localhost:50062
  ...
  raft-node5 → localhost:50065
  raft-node6 → localhost:50066   (TC1 only – started dynamically)

All five tests use `docker` CLI to pause/unpause/start/stop containers
so no code changes are needed to the Raft service itself.
=============================================================================
"""

import grpc
import json
import subprocess
import sys
import time
import os

# ── Add project root so we can import the generated proto stubs ──────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from shared.gen import raft_pb2, raft_pb2_grpc
except ImportError:
    print(
        "ERROR: Cannot import shared.gen.raft_pb2.\n"
        "  Run protoc first (see README) or run this script after docker compose build.\n"
        "  Alternatively, run:  python -m grpc_tools.protoc -I proto "
        "--python_out=shared/gen --grpc_python_out=shared/gen proto/raft.proto"
    )
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
#  Node address table  (host:port visible from the test runner machine)
# ─────────────────────────────────────────────────────────────────────────────
#  docker-compose exposes each node on a unique host port so the test runner
#  (running outside Docker) can reach them.  Update docker-compose.yml to add
#  host-port mappings like:
#    raft-node1:  ports: ["50061:50060"]
#    raft-node2:  ports: ["50062:50060"]
#    ...

NODE_ADDRS = {
    "raft-node1": "localhost:50061",
    "raft-node2": "localhost:50062",
    "raft-node3": "localhost:50063",
    "raft-node4": "localhost:50064",
    "raft-node5": "localhost:50065",
}
CONTAINER_NAMES = {k: k for k in NODE_ADDRS}   # container name == node id

# ─────────────────────────────────────────────────────────────────────────────
#  Colour / formatting helpers
# ─────────────────────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def hdr(msg: str):
    bar = "=" * 68
    print(f"\n{BOLD}{CYAN}{bar}{RESET}")
    print(f"{BOLD}{CYAN}  {msg}{RESET}")
    print(f"{BOLD}{CYAN}{bar}{RESET}")

def info(msg: str):
    print(f"  {CYAN}▶{RESET}  {msg}")

def ok(msg: str):
    print(f"  {GREEN}✔{RESET}  {msg}")

def warn(msg: str):
    print(f"  {YELLOW}⚠{RESET}  {msg}")

def fail(msg: str):
    print(f"  {RED}✘{RESET}  {msg}")

def section(msg: str):
    print(f"\n  {BOLD}── {msg} ──{RESET}")

# ─────────────────────────────────────────────────────────────────────────────
#  gRPC helpers
# ─────────────────────────────────────────────────────────────────────────────

def _stub(addr: str, timeout: float = 1.0):
    """Return (stub, channel) for addr."""
    ch = grpc.insecure_channel(addr)
    return raft_pb2_grpc.RaftServiceStub(ch), ch


def get_status(node_id: str) -> dict | None:
    """Return {state, leader_id} for a node, or None if unreachable."""
    addr = NODE_ADDRS.get(node_id)
    if not addr:
        return None
    try:
        stub, ch = _stub(addr)
        resp = stub.ClientRequest(
            raft_pb2.ClientRequestMessage(operation="_STATUS_"),
            timeout=1.5,
        )
        ch.close()
        return {"state": resp.result, "leader_id": resp.leader_id}
    except Exception:
        return None


def get_log(node_id: str) -> tuple[list, int]:
    """Return (log_entries, commit_index) from a node, or ([], -1) if down."""
    addr = NODE_ADDRS.get(node_id)
    if not addr:
        return [], -1
    try:
        stub, ch = _stub(addr)
        resp = stub.ClientRequest(
            raft_pb2.ClientRequestMessage(operation="_LOG_"),
            timeout=1.5,
        )
        ch.close()
        data = json.loads(resp.result)
        return data.get("log", []), data.get("commit_index", -1)
    except Exception:
        return [], -1


def send_command(node_id: str, operation: str, timeout: float = 5.0) -> dict:
    """Send a ClientRequest; returns {success, result, leader_id}."""
    addr = NODE_ADDRS.get(node_id)
    try:
        stub, ch = _stub(addr)
        resp = stub.ClientRequest(
            raft_pb2.ClientRequestMessage(operation=operation),
            timeout=timeout,
        )
        ch.close()
        return {"success": resp.success, "result": resp.result, "leader_id": resp.leader_id}
    except Exception as exc:
        return {"success": False, "result": str(exc), "leader_id": ""}


def find_leader(exclude: list[str] | None = None) -> str | None:
    """Poll all known nodes and return the node_id that says it is leader."""
    for nid in NODE_ADDRS:
        if exclude and nid in exclude:
            continue
        s = get_status(nid)
        if s and s.get("state") == "leader":
            return nid
    return None


def wait_for_leader(
    exclude: list[str] | None = None,
    timeout: float = 10.0,
    poll: float = 0.5,
) -> str | None:
    """Block until a leader is found (or timeout). Returns leader node_id or None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ldr = find_leader(exclude=exclude)
        if ldr:
            return ldr
        time.sleep(poll)
    return None


def print_cluster_status(label: str = ""):
    if label:
        section(label)
    for nid in NODE_ADDRS:
        s = get_status(nid)
        if s:
            role  = s["state"]
            color = GREEN if role == "leader" else (YELLOW if role == "candidate" else RESET)
            print(f"    {nid:14s} {color}{role:10s}{RESET}  leader_id={s['leader_id']}")
        else:
            print(f"    {nid:14s} {RED}UNREACHABLE{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
#  Docker helpers
# ─────────────────────────────────────────────────────────────────────────────

def docker(cmd: list[str]) -> tuple[int, str]:
    """Run a docker CLI command; return (returncode, output)."""
    result = subprocess.run(
        ["docker"] + cmd,
        capture_output=True, text=True
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def pause_container(name: str):
    rc, out = docker(["pause", name])
    if rc == 0:
        info(f"Paused container  '{name}'")
    else:
        warn(f"Could not pause '{name}': {out}")
    return rc == 0


def unpause_container(name: str):
    rc, out = docker(["unpause", name])
    if rc == 0:
        info(f"Unstopped container '{name}'")
    else:
        warn(f"Could not unpause '{name}': {out}")
    return rc == 0


def stop_container(name: str):
    rc, out = docker(["stop", "--time", "2", name])
    if rc == 0:
        info(f"Stopped container  '{name}'")
    else:
        warn(f"Could not stop '{name}': {out}")
    return rc == 0


def start_container(name: str):
    rc, out = docker(["start", name])
    if rc == 0:
        info(f"Started container  '{name}'")
    else:
        warn(f"Could not start '{name}': {out}")
    return rc == 0


def run_new_container(
    node_id: str,
    host_port: int,
    peers: list[str],
    network: str = "cse5306-project2_default",
    image: str = "cse5306-project2-raft-node1",
):
    """Start a brand-new raft node container (TC1)."""
    peer_str = ",".join(peers)
    cmd = [
        "run", "-d",
        "--name", node_id,
        "--network", network,
        "-e", f"NODE_ID={node_id}",
        "-e", "PORT=50060",
        "-e", f"PEERS={peer_str}",
        "-p", f"{host_port}:50060",
        image,
    ]
    rc, out = docker(cmd)
    if rc == 0:
        info(f"Started NEW container '{node_id}' on host port {host_port}")
    else:
        warn(f"Could not start new container: {out}")
    return rc == 0


def remove_container(name: str):
    docker(["rm", "-f", name])


# ─────────────────────────────────────────────────────────────────────────────
#  Test result tracker
# ─────────────────────────────────────────────────────────────────────────────

results: list[tuple[str, bool, str]] = []   # (name, passed, reason)

def assert_true(cond: bool, msg_pass: str, msg_fail: str, test_name: str):
    if cond:
        ok(msg_pass)
    else:
        fail(msg_fail)
    results.append((test_name, cond, msg_pass if cond else msg_fail))
    return cond


# =============================================================================
#  TC1 – New Node Joins Running Cluster
# =============================================================================
def tc1_new_node_joins():
    hdr("TC1: New Node Joins Running Cluster")
    print(
        "  Scenario: The cluster is running with 5 nodes. A 6th node (raft-node6)\n"
        "  is started late. It should receive the full log via heartbeat replication\n"
        "  and eventually have the same commit_index as the leader.\n"
    )
    PASS = True

    # ── 1. Commit some data so there's something to replicate ────────────────
    section("Step 1 – commit entries on the existing cluster")
    ldr = wait_for_leader(timeout=12)
    if not assert_true(ldr is not None, f"Leader found: {ldr}", "No leader found", "TC1"):
        return

    for i in range(3):
        r = send_command(ldr, f"SET tc1_key{i} value{i}")
        info(f"  SET tc1_key{i} → {r['result']}")

    time.sleep(1.5)  # let heartbeats propagate commits

    log_before, ci_before = get_log(ldr)
    info(f"Leader log length={len(log_before)}  commit_index={ci_before}")

    # ── 2. Start node6 ───────────────────────────────────────────────────────
    section("Step 2 – start raft-node6 (new node)")
    NODE_ADDRS["raft-node6"] = "localhost:50066"

    existing_peers = [
        "raft-node1:50060", "raft-node2:50060", "raft-node3:50060",
        "raft-node4:50060", "raft-node5:50060",
    ]

    # Detect the docker network and image from an existing running node.
    # docker compose names the network <project>_default; inspect an existing
    # container to find both values reliably instead of guessing.
    # Fall back: list all networks containing "default" and pick the one that
    # shares a name prefix with any raft container.
    network = "cse5306-project2_default"  # correct fallback from docker network ls
    rc_nl, nl_out = docker(["network", "ls", "--format", "{{.Name}}"])
    if rc_nl == 0:
        # Find network whose name ends with _default and appears in compose output
        candidates = [ln.strip() for ln in nl_out.splitlines()
                      if ln.strip().endswith("_default")]
        # Prefer a network that shares a prefix with the running containers
        rc_ci, ci_out = docker(["inspect", "--format",
                                 "{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}",
                                 "raft-node1"])
        if rc_ci == 0 and ci_out.strip():
            detected = ci_out.strip().split()[0]
            network = detected
        elif candidates:
            network = candidates[0]
    info(f"Docker network: {network}")

    # Detect image name from an existing raft container instead of hardcoding it.
    image_name = "cse5306-project2-raft-node1"  # correct fallback
    rc_img, img_out = docker(["inspect", "--format", "{{.Config.Image}}", "raft-node1"])
    if rc_img == 0 and img_out.strip():
        image_name = img_out.strip()
    info(f"Using image: {image_name}")

    # Remove any leftover container from a previous run
    remove_container("raft-node6")
    started = run_new_container("raft-node6", 50066, existing_peers, network, image_name)  # noqa: E501
    if not started:
        warn("Skipping node6 catch-up checks (container failed to start)")
        del NODE_ADDRS["raft-node6"]
        results.append(("TC1", False, "Could not start raft-node6 container"))
        return

    # ── 3. Wait for node6 to bootstrap and receive committed log ─────────────
    section("Step 3 – wait for node6 to bootstrap and sync log")
    info("Waiting 10s for raft-node6 to bootstrap and sync…")
    time.sleep(10)  # bootstrap pulls log immediately; extra time for safety

    log_node6, ci_node6 = get_log("raft-node6")
    info(f"raft-node6 log length={len(log_node6)}  commit_index={ci_node6}")

    caught_up = ci_node6 >= ci_before
    assert_true(
        caught_up,
        f"raft-node6 caught up (commit_index={ci_node6} >= leader's {ci_before})",
        f"raft-node6 did NOT catch up (commit_index={ci_node6} < leader's {ci_before})",
        "TC1",
    )

    log_matches = len(log_node6) == len(log_before)
    assert_true(
        log_matches,
        f"raft-node6 log length matches leader ({len(log_node6)} entries)",
        f"raft-node6 log length mismatch: got {len(log_node6)}, expected {len(log_before)}",
        "TC1",
    )

    # ── 4. Clean up ──────────────────────────────────────────────────────────
    section("Step 4 – remove raft-node6")
    remove_container("raft-node6")
    del NODE_ADDRS["raft-node6"]
    ok("raft-node6 removed")


# =============================================================================
#  TC2 – Leader Crash → Re-election
# =============================================================================
def tc2_leader_crash():
    hdr("TC2: Leader Crash → Re-election")
    print(
        "  Scenario: The current leader is stopped hard. The remaining 4 nodes\n"
        "  must elect a new leader within 2× max election timeout (6 s).\n"
        "  The new leader must also be able to commit new entries.\n"
    )

    # ── 1. Find current leader ────────────────────────────────────────────────
    section("Step 1 – find current leader")
    ldr = wait_for_leader(timeout=12)
    if not assert_true(ldr is not None, f"Leader found: {ldr}", "No leader found", "TC2"):
        return

    info(f"Current leader: {ldr}")
    print_cluster_status("Before crash")

    # ── 2. Stop the leader ───────────────────────────────────────────────────
    section("Step 2 – stop leader container")
    stop_container(ldr)
    t_crash = time.time()

    # ── 3. Wait for new leader ───────────────────────────────────────────────
    section("Step 3 – wait for re-election (max 8 s)")
    new_ldr = wait_for_leader(exclude=[ldr], timeout=8)
    elapsed = time.time() - t_crash

    assert_true(
        new_ldr is not None,
        f"New leader elected: {new_ldr}  (in {elapsed:.1f}s)",
        f"No new leader elected within 8 s",
        "TC2",
    )

    assert_true(
        new_ldr != ldr,
        f"New leader ({new_ldr}) is different from crashed leader ({ldr})",
        f"Same node was re-elected (unexpected)",
        "TC2",
    )

    # ── 4. Verify new leader can commit ──────────────────────────────────────
    section("Step 4 – commit entry on new leader")
    if new_ldr:
        r = send_command(new_ldr, "SET tc2_key after_crash")
        assert_true(
            r["success"],
            f"Entry committed on new leader {new_ldr}: {r['result']}",
            f"Entry commit failed: {r['result']}",
            "TC2",
        )

    print_cluster_status("After re-election")

    # ── 5. Restore crashed node ───────────────────────────────────────────────
    section("Step 5 – restart crashed node")
    start_container(ldr)
    time.sleep(4)   # let it rejoin and receive heartbeats

    rejoined_status = get_status(ldr)
    assert_true(
        rejoined_status is not None and rejoined_status["state"] in ("follower", "leader"),
        f"{ldr} rejoined as {rejoined_status['state'] if rejoined_status else '?'}",
        f"{ldr} did not rejoin cluster",
        "TC2",
    )
    info(f"{ldr} status after restart: {rejoined_status}")


# =============================================================================
#  TC3 – Single Follower Crash (Majority Intact)
# =============================================================================
def tc3_follower_crash():
    hdr("TC3: Single Follower Crash – Cluster Keeps Committing")
    print(
        "  Scenario: One follower is stopped (simulating a crash). With 4/5 nodes\n"
        "  still running the cluster has a majority and must continue to commit.\n"
        "  After un-pausing, the follower must catch up via heartbeat log replication.\n"
    )

    # ── 1. Find leader and a non-leader follower ─────────────────────────────
    section("Step 1 – identify leader and pick a follower to crash")
    ldr = wait_for_leader(timeout=12)
    if not assert_true(ldr is not None, f"Leader found: {ldr}", "No leader", "TC3"):
        return

    followers = [n for n in NODE_ADDRS if n != ldr]
    victim = followers[0]
    info(f"Leader: {ldr}  |  Victim follower: {victim}")

    # Commit one entry before crash so we know the baseline commit_index
    send_command(ldr, "SET tc3_before crash")
    time.sleep(1.5)
    _, ci_before = get_log(ldr)
    info(f"Commit index before crash: {ci_before}")

    # ── 2. Pause the follower ────────────────────────────────────────────────
    section("Step 2 – pause follower")
    pause_container(victim)
    time.sleep(1)

    # ── 3. Commit new entries while follower is down ─────────────────────────
    section("Step 3 – commit entries while follower is stopped")
    ok1 = send_command(ldr, "SET tc3_during outage1")
    ok2 = send_command(ldr, "SET tc3_during outage2")

    assert_true(
        ok1["success"] and ok2["success"],
        "Both entries committed with only 4/5 nodes active",
        f"Commit failed while follower down: {ok1['result']} / {ok2['result']}",
        "TC3",
    )

    time.sleep(1.5)
    _, ci_after_outage = get_log(ldr)
    info(f"Commit index after outage entries: {ci_after_outage}")

    # ── 4. Unpause follower and wait for catch-up ────────────────────────────
    section("Step 4 – unpause follower and wait for log catch-up")
    unpause_container(victim)
    time.sleep(15)   # ≥ 1 heartbeat cycle

    _, ci_victim = get_log(victim)
    info(f"Victim commit index after rejoin: {ci_victim}")

    assert_true(
        ci_victim >= ci_after_outage,
        f"{victim} caught up: commit_index={ci_victim} >= {ci_after_outage}",
        f"{victim} did NOT catch up: commit_index={ci_victim} < {ci_after_outage}",
        "TC3",
    )

    print_cluster_status("After follower rejoins")


# =============================================================================
#  TC4 – Minority Partition (2 followers isolated) → Restore & Catch-up
# =============================================================================
def tc4_minority_partition():
    hdr("TC4: Minority Partition – 2 Followers Isolated")
    print(
        "  Scenario: Two followers are stopped (minority partition). The leader\n"
        "  still has 3/5 nodes (majority) and must keep committing. When the two\n"
        "  partitioned nodes are restored they must receive the full log.\n"
    )

    # ── 1. Identify leader and two followers to isolate ──────────────────────
    section("Step 1 – identify nodes to partition")
    ldr = wait_for_leader(timeout=12)
    if not assert_true(ldr is not None, f"Leader found: {ldr}", "No leader", "TC4"):
        return

    others = [n for n in NODE_ADDRS if n != ldr]
    partitioned = others[:2]
    remaining   = others[2:]
    info(f"Leader: {ldr}")
    info(f"Partitioned (stopped): {partitioned}")
    info(f"Remaining active:     {remaining + [ldr]}")

    # Baseline
    send_command(ldr, "SET tc4_base before_partition")
    time.sleep(1.5)
    _, ci_base = get_log(ldr)
    info(f"Commit index before partition: {ci_base}")

    # ── 2. Pause the two minority nodes ──────────────────────────────────────
    section("Step 2 – pause 2 minority nodes")
    for n in partitioned:
        pause_container(n)
    time.sleep(1)

    # ── 3. Commit entries on the majority side ───────────────────────────────
    section("Step 3 – commit entries while partition is active")
    entries_committed = []
    for i in range(3):
        r = send_command(ldr, f"SET tc4_partition entry{i}")
        entries_committed.append(r)
        info(f"  SET tc4_partition entry{i} → success={r['success']}  result={r['result']}")

    majority_committed = all(r["success"] for r in entries_committed)
    assert_true(
        majority_committed,
        "All entries committed with 3/5 active nodes (majority)",
        "Some entries failed during partition",
        "TC4",
    )

    time.sleep(1.5)
    _, ci_partition = get_log(ldr)
    info(f"Commit index after partition entries: {ci_partition}")

    # ── 4. Restore partitioned nodes ─────────────────────────────────────────
    section("Step 4 – restore partitioned nodes")
    for n in partitioned:
        unpause_container(n)
    time.sleep(15)  # several heartbeat cycles for full catch-up

    # ── 5. Verify catch-up ───────────────────────────────────────────────────
    section("Step 5 – verify partitioned nodes caught up")
    all_caught_up = True
    for n in partitioned:
        _, ci_n = get_log(n)
        caught = ci_n >= ci_partition
        all_caught_up = all_caught_up and caught
        if caught:
            ok(f"  {n} caught up: commit_index={ci_n}")
        else:
            fail(f"  {n} did NOT catch up: commit_index={ci_n} (expected {ci_partition})")

    assert_true(
        all_caught_up,
        "Both partitioned nodes caught up to full log",
        "One or more partitioned nodes failed to catch up",
        "TC4",
    )

    print_cluster_status("After partition healed")


# =============================================================================
#  TC5 – Majority Crash → Cluster Unavailable → Restore → Recovery
# =============================================================================
def tc5_majority_crash():
    hdr("TC5: Majority Crash – Cluster Unavailable Then Recovers")
    print(
        "  Scenario: The leader is kept alive but 3 of 4 followers are STOPPED\n"
        "  (majority lost). Only leader + 1 follower remain (2/5), so the leader\n"
        "  cannot gather the 3/5 ACKs needed to commit — new entries MUST be rejected.\n"
        "  After restarting the 3 stopped followers the cluster must resume commits\n"
        "  and all 5 nodes must converge on the same commit_index.\n"
        "\n"
        "  Design note: we keep the leader running (rather than stopping it) so\n"
        "  there is no election race during teardown. Stopped containers refuse\n"
        "  connections immediately (ECONNREFUSED); paused containers leave the\n"
        "  kernel TCP stack active, which can buffer gRPC data before the deadline.\n"
    )

    # ── 1. Find leader and record baseline commit_index ──────────────────────
    section("Step 1 – find leader and commit a baseline entry")
    ldr = wait_for_leader(timeout=12)
    if not assert_true(ldr is not None, f"Leader found: {ldr}", "No leader", "TC5"):
        return

    info(f"Current leader: {ldr}")
    r_base = send_command(ldr, "SET tc5_base before_majority_crash")
    info(f"Baseline SET → success={r_base['success']} result={r_base['result']}")
    time.sleep(1.5)
    _, ci_before = get_log(ldr)
    info(f"Commit index before majority crash: {ci_before}")

    print_cluster_status("Before majority crash")

    # ── 2. Stop 3 followers (keep leader + 1 follower running) ──────────────
    # With 3 stopped: leader + 1 follower = 2/5 alive < majority(3).
    # The leader cannot gather 3 ACKs, so commits must be rejected.
    section("Step 2 – stop 3 followers to break quorum (leader stays running)")
    all_nodes = list(NODE_ADDRS.keys())
    followers = [n for n in all_nodes if n != ldr]   # 4 followers
    stopped   = followers[:3]                         # stop 3 of 4
    info(f"Leader (stays running): {ldr}")
    info(f"Stopped followers:      {stopped}")

    for n in stopped:
        stop_container(n)
    time.sleep(2)   # let any in-flight heartbeats drain

    # ── 3. Snapshot leader's commit_index before the commit attempt ──────────
    section("Step 3 – snapshot commit_index on leader before attempt")
    _, ci_snapshot = get_log(ldr)
    info(f"  {ldr}: commit_index={ci_snapshot}")

    # ── 4. Commit attempt must fail – leader has only 2/5 ACKs available ─────
    section("Step 4 – verify commit attempt fails (leader cannot reach quorum)")
    r = send_command(ldr, "SET tc5_no_quorum should_fail", timeout=6)
    info(f"  Commit attempt on leader {ldr}: success={r['success']} result={r['result']}")

    assert_true(
        not r["success"],
        "Commit correctly rejected – leader could not gather majority ACKs",
        f"Commit unexpectedly succeeded despite only 2/5 nodes reachable: {r['result']}",
        "TC5",
    )

    # Verify commit_index did not advance on the leader.
    _, ci_after_attempt = get_log(ldr)
    info(f"  {ldr}: commit_index before={ci_snapshot}  after={ci_after_attempt}")
    assert_true(
        ci_after_attempt == ci_snapshot,
        f"commit_index unchanged on leader ({ci_snapshot}) – no phantom commit",
        f"commit_index advanced on leader ({ci_snapshot} → {ci_after_attempt}) without quorum",
        "TC5",
    )

    # ── 5. Restart the 3 stopped followers ──────────────────────────────────
    section("Step 5 – restart the 3 stopped followers (restore majority)")
    for n in stopped:
        start_container(n)
    info("Waiting 12 s for nodes to restart, bootstrap, and catch up…")
    time.sleep(12)

    # ── 6. Leader (still running) must be able to commit again ───────────────
    section("Step 6 – verify leader can commit after majority restored")
    ldr_now = wait_for_leader(timeout=10)
    assert_true(
        ldr_now is not None,
        f"Leader present after recovery: {ldr_now}",
        "No leader found after recovery",
        "TC5",
    )

    if ldr_now:
        r_post = send_command(ldr_now, "SET tc5_post recovery_ok")
        assert_true(
            r_post["success"],
            f"Entry committed after majority restored on {ldr_now}: {r_post['result']}",
            f"Commit failed after recovery: {r_post['result']}",
            "TC5",
        )

    # ── 7. All nodes must converge on the same commit_index ──────────────────
    section("Step 7 – verify all nodes agree on commit_index")
    time.sleep(5)   # let heartbeats propagate to recently-unstopped nodes
    commit_indices = {}
    for nid in NODE_ADDRS:
        _, ci = get_log(nid)
        commit_indices[nid] = ci
        info(f"  {nid}: commit_index={ci}")

    unique_ci = set(commit_indices.values())
    assert_true(
        len(unique_ci) == 1,
        f"All nodes agree on commit_index={list(unique_ci)[0]}",
        f"commit_index mismatch after recovery: {commit_indices}",
        "TC5",
    )

    print_cluster_status("After majority crash recovery")


# =============================================================================
#  Main runner
# =============================================================================

def print_summary():
    hdr("TEST SUMMARY")
    total  = len(results)
    passed = sum(1 for _, p, _ in results if p)
    failed = total - passed

    # Group by test name (each TC may have multiple assertions)
    from collections import defaultdict
    by_tc: dict[str, list[tuple[bool, str]]] = defaultdict(list)
    for name, p, msg in results:
        by_tc[name].append((p, msg))

    for tc, asserts in by_tc.items():
        tc_pass = all(p for p, _ in asserts)
        status = f"{GREEN}PASS{RESET}" if tc_pass else f"{RED}FAIL{RESET}"
        print(f"\n  {BOLD}{tc}{RESET}  [{status}]")
        for p, msg in asserts:
            sym = f"{GREEN}✔{RESET}" if p else f"{RED}✘{RESET}"
            print(f"      {sym} {msg}")

    bar = "─" * 40
    print(f"\n  {bar}")
    color = GREEN if failed == 0 else RED
    print(f"  {color}{BOLD}{passed}/{total} assertions passed{RESET}\n")


def main():
    print(f"\n{BOLD}Raft Failure Test Suite{RESET}")
    print("Connecting to cluster…\n")

    # Quick reachability check
    reachable = 0
    for nid, addr in NODE_ADDRS.items():
        s = get_status(nid)
        if s:
            reachable += 1
            print(f"  {GREEN}✔{RESET} {nid} ({addr}) → state={s['state']}")
        else:
            print(f"  {RED}✘{RESET} {nid} ({addr}) → UNREACHABLE")

    if reachable < 3:
        print(f"\n{RED}ERROR: Only {reachable}/5 nodes reachable. "
              f"Is the cluster running?  docker compose up{RESET}\n")
        sys.exit(1)

    print()

    try:
        tc1_new_node_joins()
    except Exception as e:
        fail(f"TC1 raised an exception: {e}")
        results.append(("TC1", False, str(e)))

    try:
        tc2_leader_crash()
    except Exception as e:
        fail(f"TC2 raised an exception: {e}")
        results.append(("TC2", False, str(e)))

    try:
        tc3_follower_crash()
    except Exception as e:
        fail(f"TC3 raised an exception: {e}")
        results.append(("TC3", False, str(e)))

    try:
        tc4_minority_partition()
    except Exception as e:
        fail(f"TC4 raised an exception: {e}")
        results.append(("TC4", False, str(e)))

    try:
        tc5_majority_crash()
    except Exception as e:
        fail(f"TC5 raised an exception: {e}")
        results.append(("TC5", False, str(e)))

    print_summary()


if __name__ == "__main__":
    main()
