import os
import uuid
from concurrent import futures

from concurrent import futures
import logging

import grpc
from shared.gen import inventory_pb2_grpc, inventory_pb2
#import inventory_pb2_grpc, inventory_pb2

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

# BOOKSSTOCK: dict[str, int] = {}

class InventoryService(inventory_pb2_grpc.InventoryServiceServicer):
    def AddCopies(self, request, context):
        #book_id = str(uuid.uuid4())
        current = _raft_get(f"inv:{request.book_id}") or 0
        new_count = int(current) + request.count
        _raft_set(f"inv:{request.book_id}", new_count)
        return inventory_pb2.InventoryResponse(ok=True, message="Book added",
            book_id=request.book_id, available=new_count)
    
    def GetAvailability(self, request, context):
        count = _raft_get(f"inv:{request.book_id}") or 0
        return inventory_pb2.InventoryResponse(ok=True, message="Available copies:",
            book_id=request.book_id, available=int(count))
    
    def DecrementCopy(self, request, context):
        count = int(_raft_get(f"inv:{request.book_id}") or 0)
        if count <= 0:
            return inventory_pb2.InventoryResponse(ok=False, message="Book out of stock",
                book_id=request.book_id, available=0)
        new_count = count - 1
        _raft_set(f"inv:{request.book_id}", new_count)
        return inventory_pb2.InventoryResponse(ok=True, message="Book taken",
            book_id=request.book_id, available=new_count)
    def IncrementCopy(self, request, context):
        count = int(_raft_get(f"inv:{request.book_id}") or 0)
        new_count = count + 1
        _raft_set(f"inv:{request.book_id}", new_count)
        return inventory_pb2.InventoryResponse(ok=True, message="Book returned",
            book_id=request.book_id, available=new_count)




    


def serve():
    #port = "50051"
    port = int(os.getenv("PORT", "50051"))
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    inventory_pb2_grpc.add_InventoryServiceServicer_to_server(InventoryService(), server)
    #server.add_insecure_port("[::]:" + port)
    server.add_insecure_port(f"0.0.0.0:{port}")
    print(f"[inventory] listening on {port}")
    server.start()
    #print("Server started, listening on " + port)
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig()
    serve()
