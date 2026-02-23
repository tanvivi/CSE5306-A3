# node 6
# This section handles LogEvents, ListEvents

from concurrent import futures
import logging

import grpc
#from ...shared.gen import audit_pb2_grpc, audit_pb2
import audit_pb2_grpc, audit_pb2


events = []

class AuditService(audit_pb2_grpc.AuditServiceServicer):
    def LogEvent(self, request, context):
        events.append(request.event_type + " : " + request.description)
        return audit_pb2.SimpleResponse(ok = True, message = "event logged")
    


def serve():
    port = "50051"
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    audit_pb2_grpc.add_AuditServiceServicer_to_server(AuditService(), server)
    server.add_insecure_port("[::]:" + port)
    server.start()
    print("Server started, listening on " + port)
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig()
    serve()
