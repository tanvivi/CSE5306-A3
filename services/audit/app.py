# node 6
# This section handles LogEvents, ListEvents

from concurrent import futures
import logging
import os

import grpc
from shared.gen import audit_pb2_grpc, audit_pb2

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

events = []

class AuditService(audit_pb2_grpc.AuditServiceServicer):
    def LogEvent(self, request, context):
        if (request.event_type == "1"):
            events.append(request.description)
            return audit_pb2.SimpleResponse(ok = True, message = "event logged")
        else:
            ind = int(request.description)
            return audit_pb2.SimpleResponse(ok = True, message = events[ind])
    


def serve():
    port = int(os.getenv("PORT", "50051"))
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    audit_pb2_grpc.add_AuditServiceServicer_to_server(AuditService(), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    print(f"[audit] listening on {port}")
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig()
    serve()
