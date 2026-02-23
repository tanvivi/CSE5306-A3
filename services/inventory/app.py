import os
import uuid
from concurrent import futures

from concurrent import futures
import logging

import grpc
from shared.gen import inventory_pb2_grpc, inventory_pb2
#import inventory_pb2_grpc, inventory_pb2

BOOKSSTOCK: dict[str, int] = {}

class InventoryService(inventory_pb2_grpc.InventoryServiceServicer):
    def AddCopies(self, request, context):
        #book_id = str(uuid.uuid4())
        if(request.book_id in BOOKSSTOCK):
            BOOKSSTOCK[request.book_id] += request.count
        else:
            BOOKSSTOCK[request.book_id] = request.count

        return inventory_pb2.InventoryResponse(
            ok=True,
            message="Book added",
            book_id=request.book_id,
            available=BOOKSSTOCK[request.book_id]
        )
    
    def GetAvailability(self, request, context):
        #book_id = str(uuid.uuid4())
        if(request.book_id in BOOKSSTOCK):
            return inventory_pb2.InventoryResponse(
            ok=True,
            message="Available copies:",
            book_id=request.book_id,
            available=BOOKSSTOCK[request.book_id]
        )
        else:
            return inventory_pb2.InventoryResponse(
            ok=True,
            message="Book not in inventory:",
            book_id=request.book_id,
            available=0
            )
    
    def DecrementCopy(self, request, context):
        #book_id = str(uuid.uuid4())
        if(request.book_id in BOOKSSTOCK):
            if (BOOKSSTOCK[request.book_id] > 0):
                BOOKSSTOCK[request.book_id] -= 1
                return inventory_pb2.InventoryResponse(
                ok=True,
                message="Book taken",
                book_id=request.book_id,
                available=BOOKSSTOCK[request.book_id]
                )           
            else:
                return inventory_pb2.InventoryResponse(
                ok=True,
                message="Book out of stock",
                book_id=request.book_id,
                available=BOOKSSTOCK[request.book_id]
                )
        else:
            return inventory_pb2.InventoryResponse(
            ok=True,
            message="Book not in inventory:",
            book_id=request.book_id,
            available=0
            )




    


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
