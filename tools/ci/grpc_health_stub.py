"""Stand-ins for Fibre's own port in CI, for the readiness probe (scrape job fibre_health).

  python tools/ci/grpc_health_stub.py CERT KEY

Three gRPC servers on 127.0.0.1 with TLS and a self-signed certificate (CERT, KEY, PEM), as Fibre
has its own:
  7981  the standard health service answers SERVING for the server as a whole (Ready)
  7982  it answers NOT_SERVING (Not ready)
  7983  no health service, as a Fibre older than v10.5.0: Unimplemented (Not reported)
Prints "ready" once all three listen, then runs until it is stopped. Needs grpcio and
grpcio-health-checking.
"""

import sys
from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc


def serve(port, creds, status):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    if status is not None:
        servicer = health.HealthServicer()
        servicer.set("", status)
        health_pb2_grpc.add_HealthServicer_to_server(servicer, server)
    server.add_secure_port(f"127.0.0.1:{port}", creds)
    server.start()
    return server


def main():
    with open(sys.argv[1], "rb") as f:
        cert = f.read()
    with open(sys.argv[2], "rb") as f:
        key = f.read()
    creds = grpc.ssl_server_credentials([(key, cert)])
    servers = [
        serve(7981, creds, health_pb2.HealthCheckResponse.SERVING),
        serve(7982, creds, health_pb2.HealthCheckResponse.NOT_SERVING),
        serve(7983, creds, None),
    ]
    print("ready", flush=True)
    servers[0].wait_for_termination()


if __name__ == "__main__":
    main()
