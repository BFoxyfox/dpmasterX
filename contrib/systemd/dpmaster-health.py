#!/usr/bin/env python3
import socket
import sys

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(2)
sock.sendto(b"\xff\xff\xff\xffgetservers 68 full empty", ("127.0.0.1", 27950))
try:
    data, _ = sock.recvfrom(2048)
except socket.timeout:
    sys.exit("dpmaster did not answer")
if not data.startswith(b"\xff\xff\xff\xffgetserversResponse"):
    sys.exit("invalid dpmaster response")
