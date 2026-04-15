"""
2PC Participant — Users.

Vote phase:     verify user exists via users service → COMMIT or ABORT
Decision phase: Commit → acknowledge (user record unchanged, checkout recorded by circulation)
                Abort  → do nothing
"""
from concurrent import futures
import logging
import os

import grpc
from shared.gen import twopc_pb2, twopc_pb2_grpc
from shared.gen import users_pb2, users_pb2_grpc

USERS_ADDR = os.getenv("USERS_ADDR", "users:50051")
NODE_ID = os.getenv("NODE_ID", "twopc-users")


class UsersParticipant(twopc_pb2_grpc.ParticipantServiceServicer):

    # ── Phase 1 ───────────────────────────────────────────────────────────────

    def RequestVote(self, request, context):
        print(f"Phase voting of Node {NODE_ID} receives RPC RequestVote "
              f"from Phase voting of Node twopc-coordinator")
        print(f"[users-participant] vote  txn={request.transaction_id} "
              f"user={request.user_id}")
        try:
            with grpc.insecure_channel(USERS_ADDR) as ch:
                stub = users_pb2_grpc.UsersServiceStub(ch)
                res = stub.GetUser(
                    users_pb2.GetUserRequest(user_id=request.user_id),
                    timeout=5,
                )
            if res.ok:
                return twopc_pb2.VoteResponse(
                    transaction_id=request.transaction_id,
                    participant_id="users",
                    vote=twopc_pb2.VOTE_COMMIT,
                    reason=f"User '{res.name}' is registered",
                )
            return twopc_pb2.VoteResponse(
                transaction_id=request.transaction_id,
                participant_id="users",
                vote=twopc_pb2.VOTE_ABORT,
                reason=f"User '{request.user_id}' not found",
            )
        except grpc.RpcError as e:
            return twopc_pb2.VoteResponse(
                transaction_id=request.transaction_id,
                participant_id="users",
                vote=twopc_pb2.VOTE_ABORT,
                reason=f"Cannot reach users service: {e.details()}",
            )

    # ── Phase 2 ───────────────────────────────────────────────────────────────

    def Commit(self, request, context):
        print(f"Phase decision of Node {NODE_ID} receives RPC Commit "
              f"from Phase decision of Node twopc-coordinator")
        print(f"[users-participant] commit txn={request.transaction_id} "
              f"user={request.user_id}")
        return twopc_pb2.AckResponse(
            transaction_id=request.transaction_id,
            participant_id="users",
            ok=True,
            message=f"User '{request.user_id}' authorized for checkout",
        )

    def Abort(self, request, context):
        print(f"Phase decision of Node {NODE_ID} receives RPC Abort "
              f"from Phase decision of Node twopc-coordinator")
        print(f"[users-participant] abort  txn={request.transaction_id} "
              f"user={request.user_id}")
        return twopc_pb2.AckResponse(
            transaction_id=request.transaction_id,
            participant_id="users",
            ok=True,
            message="Aborted — no user record changes made",
        )


def serve():
    port = int(os.getenv("PORT", "50051"))
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    twopc_pb2_grpc.add_ParticipantServiceServicer_to_server(UsersParticipant(), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    print(f"[users-participant] listening on {port}")
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig()
    serve()
