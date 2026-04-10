import os
import uuid
from concurrent import futures

import grpc
# gets the generated files from grpc
from shared.gen import catalog_pb2, catalog_pb2_grpc

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

# In-memory database
# BOOKS: dict[str, dict] = {}


class CatalogService(catalog_pb2_grpc.CatalogServiceServicer):
    def PublishBook(self, request, context):
        book_id = str(uuid.uuid4())[:8]
        _raft_set(f"book:{book_id}", {"title": request.title, "author": request.author})
        return catalog_pb2.BookResponse(ok=True, message="Book published",
            book_id=book_id, title=request.title, author=request.author)

    def GetBook(self, request, context):
        book = _raft_get(f"book:{request.book_id}")
        if not book:
            return catalog_pb2.BookResponse(ok=False, message="Book not found")
        return catalog_pb2.BookResponse(ok=True, message="OK",
            book_id=request.book_id, title=book["title"], author=book["author"])


def serve():
    port = int(os.getenv("PORT", "50051"))
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    catalog_pb2_grpc.add_CatalogServiceServicer_to_server(CatalogService(), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    print(f"[catalog] listening on {port}")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
