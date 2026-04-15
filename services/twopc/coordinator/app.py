"""
2PC Coordinator — runs as its own container.

Phase 1 (Vote):   fan-out VoteRequest  → collect COMMIT / ABORT votes
Phase 2 (Decision): if all COMMIT → fan-out Commit; else → fan-out Abort
"""
from concurrent import futures
import logging
import os
import uuid

import grpc
from shared.gen import twopc_pb2, twopc_pb2_grpc

NODE_ID = os.getenv("NODE_ID", "twopc-coordinator")

PARTICIPANTS = {
    "inventory":   os.getenv("INVENTORY_PARTICIPANT_ADDR",   "twopc-inventory:50051"),
    "users":       os.getenv("USERS_PARTICIPANT_ADDR",       "twopc-users:50051"),
    "circulation": os.getenv("CIRCULATION_PARTICIPANT_ADDR", "twopc-circulation:50051"),
}


class CoordinatorService(twopc_pb2_grpc.CoordinatorServiceServicer):

    def BeginTransaction(self, request, context):
        txn_id = str(uuid.uuid4())[:8]
        print(f"[coordinator] txn={txn_id} op={request.operation} "
              f"book={request.book_id} user={request.user_id}")

        # ── Phase 1: Vote ─────────────────────────────────────────────────────
        raw_votes = []
        all_commit = True

        for name, addr in PARTICIPANTS.items():
            try:
                with grpc.insecure_channel(addr) as ch:
                    stub = twopc_pb2_grpc.ParticipantServiceStub(ch)
                    print(f"Phase voting of Node {NODE_ID} sends RPC RequestVote "
                          f"to Phase voting of Node twopc-{name}")
                    resp = stub.RequestVote(
                        twopc_pb2.VoteRequest(
                            transaction_id=txn_id,
                            operation=request.operation,
                            book_id=request.book_id,
                            user_id=request.user_id,
                        ),
                        timeout=5,
                    )
                    raw_votes.append(resp)
                    voted = "COMMIT" if resp.vote == twopc_pb2.VOTE_COMMIT else "ABORT"
                    print(f"[coordinator] vote  {name}: {voted} — {resp.reason}")
                    if resp.vote == twopc_pb2.VOTE_ABORT:
                        all_commit = False
            except grpc.RpcError as e:
                raw_votes.append(twopc_pb2.VoteResponse(
                    transaction_id=txn_id,
                    participant_id=name,
                    vote=twopc_pb2.VOTE_ABORT,
                    reason=f"Unreachable: {e.details()}",
                ))
                all_commit = False

        vote_results = [
            twopc_pb2.VoteResult(
                participant=v.participant_id,
                committed=(v.vote == twopc_pb2.VOTE_COMMIT),
                reason=v.reason,
            )
            for v in raw_votes
        ]

        # ── Phase 2: Decision ─────────────────────────────────────────────────
        decision = "GLOBAL COMMIT" if all_commit else "GLOBAL ABORT"
        print(f"[coordinator] decision → {decision}")

        raw_acks = []
        for name, addr in PARTICIPANTS.items():
            req = twopc_pb2.DecisionRequest(
                transaction_id=txn_id,
                operation=request.operation,
                book_id=request.book_id,
                user_id=request.user_id,
            )
            try:
                with grpc.insecure_channel(addr) as ch:
                    stub = twopc_pb2_grpc.ParticipantServiceStub(ch)
                    if all_commit:
                        print(f"Phase decision of Node {NODE_ID} sends RPC Commit "
                              f"to Phase decision of Node twopc-{name}")
                        ack = stub.Commit(req, timeout=5)
                    else:
                        print(f"Phase decision of Node {NODE_ID} sends RPC Abort "
                              f"to Phase decision of Node twopc-{name}")
                        ack = stub.Abort(req, timeout=5)
                    raw_acks.append(ack)
                    print(f"[coordinator] ack   {name}: ok={ack.ok} — {ack.message}")
            except grpc.RpcError as e:
                raw_acks.append(twopc_pb2.AckResponse(
                    transaction_id=txn_id,
                    participant_id=name,
                    ok=False,
                    message=f"Unreachable: {e.details()}",
                ))

        ack_results = [
            twopc_pb2.AckResult(
                participant=a.participant_id,
                ok=a.ok,
                message=a.message,
            )
            for a in raw_acks
        ]

        return twopc_pb2.BeginResponse(
            transaction_id=txn_id,
            all_commit=all_commit,
            votes=vote_results,
            acks=ack_results,
            summary=decision,
        )


def serve():
    port = int(os.getenv("PORT", "50051"))
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    twopc_pb2_grpc.add_CoordinatorServiceServicer_to_server(CoordinatorService(), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    print(f"[coordinator] listening on {port}")
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig()
    serve()
