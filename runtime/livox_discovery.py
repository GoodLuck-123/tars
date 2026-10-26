"""Select a Livox broadcast code by its known IPv4 address; never change IPs."""
import ipaddress
import json
import socket
import struct
import sys
import time


def discover(ip,timeout_s=10):
    ip=str(ipaddress.IPv4Address(ip));deadline=time.monotonic()+timeout_s
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        try:sock.bind(('',55000))
        except OSError as e:raise RuntimeError('Livox UDP 55000 is occupied; close Viewer or another Livox SDK process') from e
        while time.monotonic()<deadline:
            sock.settimeout(max(.01,deadline-time.monotonic()))
            try:packet,address=sock.recvfrom(2048)
            except socket.timeout:break
            # SDK SdkPacket: 9-byte preamble, command set/id, 16-byte code.
            if address[0]!=ip or len(packet)<34 or packet[:2]!=b'\xaa\x01' or packet[9:11]!=b'\x00\x00':continue
            if struct.unpack_from('<H',packet,2)[0]!=len(packet):continue
            code=packet[11:27].split(b'\0')[0].decode('ascii')
            if not code.isalnum() or len(code)!=15:continue
            return dict(ip=ip,broadcast_code=code,device_type=packet[27])
    raise RuntimeError('No Livox discovery broadcast from '+ip+'; check power, subnet, UDP firewall and other connected clients')


if __name__=='__main__':print(json.dumps(discover(sys.argv[1])))
