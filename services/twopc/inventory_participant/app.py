"""
2PC Participant — Inventory.

Vote phase:     check stock via inventory service → COMMIT or ABORT
Decision phase: Commit → call DecrementCopy to actually reduce stock
                Abort  → do nothing (stock was never touched)
"""
from concurrent import futures
import logging
import os

import grpc
from shared.gen import twopc_pb2, twopc_pb2_grpc
from shared.gen import inventory_pb2, inventory_pb2_grpc

INVENTORY_ADDR = os.getenv("INVENTORY_ADDR", "inventory:50051")


class InventoryParticipant(twopc_pb2_grpc.ParticipantServiceServicer):

    # ── Phase 1 ───────────────────────────────────────────────────────────────

    def RequestVote(self, request, context):
        print(f"[inventory-participant] vote  txn={request.transaction_id} "
              f"book={request.book_id}")

        if request.operation == "CHECKOUT":
            try:
                with grpc.insecure_channel(INVENTORY_ADDR) as ch:
                    stub = inventory_pb2_grpc.InventoryServiceStub(ch)
                    res = stub.GetAvailability(
                        inventory_pb2.BookRequest(book_id=request.book_id),
                        timeout=5,
                    )
                if res.available > 0:
                    return twopc_pb2.VoteResponse(
                        transaction_id=request.transaction_id,
                        participant_id="inventory",
                        vote=twopc_pb2.VOTE_COMMIT,
                        reason=f"Book available: {res.available} "
                               f"cop{'y' if res.available == 1 else 'ies'} in stock",
                    )
                return twopc_pb2.VoteResponse(
                    transaction_id=request.transaction_id,
                    participant_id="inventory",
                    vote=twopc_pb2.VOTE_ABORT,
                    reason="Book out of stock",
                )
            except grpc.RpcError as e:
                return twopc_pb2.VoteResponse(
                    transaction_id=request.transaction_id,
                    participant_id="inventory",
                    vote=twopc_pb2.VOTE_ABORT,
                    reason=f"Cannot reach inventory service: {e.details()}",
                )

        return twopc_pb2.VoteResponse(
            transaction_id=request.transaction_id,
            participant_id="inventory",
            vote=twopc_pb2.VOTE_COMMIT,
            reason="Operation not inventory-specific",
        )

    # ── Phase 2 ───────────────────────────────────────────────────────────────

    def Commit(self, request, context):
        print(f"[inventory-participant] commit txn={request.transaction_id} "
              f"book={request.book_id}")
        try:
            with grpc.insecure_channel(INVENTORY_ADDR) as ch:
                stub = inventory_pb2_grpc.InventoryServiceStub(ch)
                res = stub.DecrementCopy(
                    inventory_pb2.BookRequest(book_id=request.book_id),
                    timeout=5,
                )
            return twopc_pb2.AckResponse(
                transaction_id=request.transaction_id,
                participant_id="inventory",
                ok=res.ok,
                message=f"Stock decremented — {res.available} remaining",
            )
        except grpc.RpcError as e:
            return twopc_pb2.AckResponse(
                transaction_id=request.transaction_id,
                participant_id="inventory",
                ok=False,
                message=f"Commit failed: {e.details()}",
            )

    def Abort(self, request, context):
        print(f"[inventory-participant] abort  txn={request.transaction_id} "
              f"book={request.book_id}")
        # Nothing was changed during vote phase, so nothing to undo
        return twopc_pb2.AckResponse(
            transaction_id=request.transaction_id,
            participant_id="inventory",
            ok=True,
            message="Aborted — no inventory changes made",
        )


def serve():
    port = int(os.getenv("PORT", "50051"))
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    twopc_pb2_grpc.add_ParticipantServiceServicer_to_server(InventoryParticipant(), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    print(f"[inventory-participant] listening on {port}")
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig()
    serve()
