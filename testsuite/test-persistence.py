#!/usr/bin/env python3
import os
import socket
import subprocess
import shutil
import tempfile
import time

BINARY = os.path.abspath(os.environ.get("DPMASTER", "../src/dpmaster"))

with tempfile.TemporaryDirectory(prefix="dpmaster-state-test-") as directory:
    os.chmod(directory, 0o777)
    state = os.path.join(directory, "servers.state")
    test_binary = BINARY
    if os.geteuid() == 0:
        test_binary = os.path.join(directory, "dpmaster")
        shutil.copy2(BINARY, test_binary)
        os.chmod(test_binary, 0o755)
    command = [test_binary, "--allow-loopback", "--hash-ports", "--port", "29150",
               "--state-file", state, "--verbose", "0"]
    if os.geteuid() == 0:
        command = ["runuser", "-u", "dpmaster", "--"] + command

    def start():
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
        time.sleep(0.2)
        if process.poll() is not None:
            raise RuntimeError(process.stdout.read())
        return process

    def stop(process):
        process.terminate()
        process.wait(timeout=3)

    endpoint = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    endpoint.bind(("127.0.0.1", 29160))
    endpoint.settimeout(2)
    process = start()
    endpoint.sendto(b"\xff\xff\xff\xffheartbeat QuakeArena-1\n", ("127.0.0.1", 29150))
    challenge_packet, _ = endpoint.recvfrom(2048)
    challenge = challenge_packet.split(b"getinfo ", 1)[1].strip(b"\x00\r\n")
    response = (b"\xff\xff\xff\xffinfoResponse\n\\challenge\\" + challenge +
                b"\\protocol\\68\\gamename\\Quake3Arena\\sv_maxclients\\16"
                b"\\clients\\1\\gametype\\0")
    endpoint.sendto(response, ("127.0.0.1", 29150))
    time.sleep(0.2)
    assert os.path.exists(state)
    stop(process)

    process = start()
    endpoint.sendto(b"\xff\xff\xff\xffgetservers Quake3Arena 68 full empty", ("127.0.0.1", 29150))
    reply, _ = endpoint.recvfrom(2048)
    expected = b"\\\x7f\x00\x00\x01" + (29160).to_bytes(2, "big")
    assert expected in reply
    stop(process)

print("PASS: validated server restored immediately after restart")
