import os
import uuid
from concurrent import futures

import grpc

#import generated protobuf
from shared.gen import users_pb2
from shared.gen import users_pb2_grpc

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
USERS = {}

# gRPC Service Call from users.proto
# Receives: request.name, request.email and Returns: UserResponse.
class UsersService(users_pb2_grpc.UsersServiceServicer):
    def RegisterUser(self, request, context):
        user_id = str(uuid.uuid4())[:8]
        _raft_set(f"user:{user_id}", {"name": request.name, "email": request.email})
        return users_pb2.UserResponse(ok=True, message="User registered",
            user_id=user_id, name=request.name, email=request.email)

    def GetUser(self, request, context):
        user = _raft_get(f"user:{request.user_id}")
        if not user:
            return users_pb2.UserResponse(ok=False, message="User not found")
        return users_pb2.UserResponse(ok=True, message="OK",
            user_id=request.user_id, name=user["name"], email=user["email"])

def serve():
    port = int(os.getenv("PORT", "50051"))
    
    # Create gRPC server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    # 
    users_pb2_grpc.add_UsersServiceServicer_to_server(
        UsersService(),
        server
    )
    server.add_insecure_port(f"0.0.0.0:{port}")
    print(f"[users] listening on {port}")
    server.start()
    server.wait_for_termination()

if __name__ == "__main__":
    serve()
