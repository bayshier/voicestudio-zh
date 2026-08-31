"""gRPC transport for the worker protocol.

A thin adapter over the domain layer, and deliberately thin: every rule about
what a message *means* lives in ``worker/`` proper, so the transport only
translates between protobuf and those objects, and can be swapped or
reimplemented (in Go, for the hosted platform) without the rules moving.

Imports of ``grpc`` are confined to this subpackage so the rest of the worker
domain stays importable in processes that never speak the protocol.
"""
