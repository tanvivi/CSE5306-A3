# This file creates gRPC client stubs.
# The gateway calls these to communicate with backend services.

import os
import grpc

# This imports generated stub classes
from shared.gen import users_pb2_grpc, catalog_pb2_grpc, inventory_pb2_grpc, circulation_pb2_grpc, audit_pb2_grpc, twopc_pb2_grpc

# Creates and returns a gRPC stub for the Users service.
# addr comes from Docker environment var, and if not provided makes port 50051
def users_stub():
    addr = os.getenv("USERS_ADDR", "users:50051")
    channel = grpc.insecure_channel(addr)
    return users_pb2_grpc.UsersServiceStub(channel)

# Creates & returns a gRPC stub for Catalog service
def catalog_stub():
    addr = os.getenv("CATALOG_ADDR", "catalog:50051")
    channel = grpc.insecure_channel(addr)
    return catalog_pb2_grpc.CatalogServiceStub(channel)

def inventory_stub():
    addr = os.getenv("INVENTORY_ADDR", "inventory:50051")
    channel = grpc.insecure_channel(addr)
    return inventory_pb2_grpc.InventoryServiceStub(channel)

def circulation_stub():
    addr = os.getenv("CIRCULATION_ADDR", "circulation:50051")
    channel = grpc.insecure_channel(addr)
    return circulation_pb2_grpc.CirculationServiceStub(channel)

def audit_stub():
    addr = os.getenv("AUDIT_ADDR", "audit:50051")
    channel = grpc.insecure_channel(addr)
    return audit_pb2_grpc.AuditServiceStub(channel)

def twopc_coordinator_stub():
    addr = os.getenv("TWOPC_COORDINATOR_ADDR", "twopc-coordinator:50051")
    channel = grpc.insecure_channel(addr)
    return twopc_pb2_grpc.CoordinatorServiceStub(channel)

def raft_stub(addr: str = None):
    if addr is None:
        addr = os.getenv("RAFT_ADDR", "raft-node1:50060")
    from shared.gen import raft_pb2_grpc
    channel = grpc.insecure_channel(addr)
    return raft_pb2_grpc.RaftServiceStub(channel)
