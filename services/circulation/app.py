from concurrent import futures
import logging
from datetime import date, timedelta
import os

import grpc
from shared.gen import circulation_pb2_grpc, circulation_pb2

import grpc, json
from shared.gen import raft_pb2, raft_pb2_grpc

RAFT_ADDR = os.getenv("RAFT_ADDR", "raft-node1:50060")

def _raft_set(key, value):
    ch = grpc.insecure_channel(RAFT_ADDR)
    stub = raft_pb2_grpc.RaftServiceStub(ch)
    stub.ClientRequest(raft_pb2.ClientRequestMessage(
        operation=f"SET {key} {json.dumps(value, separators=(',', ':'))}"
    ), timeout=5)
    ch.close()

def _raft_get(key):
    ch = grpc.insecure_channel(RAFT_ADDR)
    stub = raft_pb2_grpc.RaftServiceStub(ch)
    resp = stub.ClientRequest(raft_pb2.ClientRequestMessage(
        operation=f"GET {key}"
    ), timeout=5)
    ch.close()
    return None if resp.result == "(nil)" else json.loads(resp.result)

def _raft_del(key):
    ch = grpc.insecure_channel(RAFT_ADDR)
    stub = raft_pb2_grpc.RaftServiceStub(ch)
    stub.ClientRequest(raft_pb2.ClientRequestMessage(
        operation=f"DEL {key}"
    ), timeout=5)
    ch.close()

#import circulation_pb2_grpc, circulation_pb2, inventory_pb2_grpc, inventory_pb2
"""
def getAvailable(arg_id):
    with grpc.insecure_channel("localhost:50052") as channel:
        stub = inventory_pb2_grpc.InventoryServiceStub(channel)
        response = stub.getAvailability(inventory_pb2.Get(book_id=arg_id))
        return response

def getBook(arg_id):
    with grpc.insecure_channel("localhost:50052") as channel:
        stub = inventory_pb2_grpc.InventoryServiceStub(channel)
        response = stub.decrementCopies(inventory_pb2.Get(book_id=arg_id))
        return response.available
    
def putBook(arg_id):
    with grpc.insecure_channel("localhost:50052") as channel:
        stub = inventory_pb2_grpc.InventoryServiceStub(channel)
        response = stub.addCopies(inventory_pb2.Get(book_id=arg_id))
        return response.available
"""
class CirculationService(circulation_pb2_grpc.CirculationServiceServicer):
    def CheckoutBook(self, request, context):
        _raft_set(f"circ:{request.user_id}:{request.book_id}",
            {"due_date": str(date.today() + timedelta(days=5))})
        due = str(date.today() + timedelta(days=5))
        return circulation_pb2.CheckoutResponse(ok=True,
            message="Book Checked Out", due_date=due)
        
    def CheckinBook(self, request, context):
        _raft_del(f"circ:{request.user_id}:{request.book_id}")
        return circulation_pb2.SimpleResponse(ok=True, message="Book Checked In")


def serve():
    port = int(os.getenv("PORT", "50051"))
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    circulation_pb2_grpc.add_CirculationServiceServicer_to_server(CirculationService(), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    print(f"[circulation] listening on {port}")
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig()
    serve()
