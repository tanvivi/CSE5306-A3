FROM python:3.11-slim

WORKDIR /app

# Install grpc + tools (needed to generate stubs at build time)
RUN pip install --no-cache-dir grpcio grpcio-tools protobuf

# Copy proto and shared stubs
COPY proto/raft.proto /app/proto/raft.proto
COPY shared/ /app/shared/

# Generate raft_pb2.py and raft_pb2_grpc.py into shared/gen
RUN python -m grpc_tools.protoc \
    -I proto \
    --python_out=shared/gen \
    --grpc_python_out=shared/gen \
    proto/raft.proto

# Copy service code
COPY services/raft/app.py /app/services/raft/app.py

# /app/shared/gen must be on PYTHONPATH so that the bare
# "import raft_pb2" inside raft_pb2_grpc.py resolves correctly
ENV PYTHONPATH=/app:/app/shared/gen

CMD ["python", "services/raft/app.py"]
