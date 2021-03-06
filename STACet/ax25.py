# Copyright (c) 2013 Christopher H. Casebeer. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   1. Redistributions of source code must retain the above copyright notice,
#      this list of conditions and the following disclaimer.
#
#   2. Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
#
# Modified by Frank Ong and Michael Lustig 2014
# Modified by Max Burns 2019
#
# Different Copyright than other files...


# --------------------------------------------------------------------------
#   ax25.py
#       Handles most AX.25 Stuff. Building and forming packets
# --------------------------------------------------------------------------
#
# --------------------------------------------------------------------------
# Imports
import var
from var import bitarray

import struct
import sys
import argparse


# --------------------------------------------------------------------------


#
# Stuffs bits. Add a 0 if there is ever a string of 5 1's
#
def bit_stuff(data):
    count = 0
    for bit in data:
        if bit:
            count += 1
        else:
            count = 0
        yield bit
        if count == 5:
            yield False
            count = 0


#
# Unstuff bits. Remove the 0 following any 5 1's
#
def bit_unstuff(data):
    count = 0
    skip = False
    ret_bits = bitarray.bitarray(endian="little")
    for bit in data:
        if not (skip):
            if bit:
                count += 1
            else:
                count = 0
            ret_bits.append(bit)

            if count == 5:
                skip = True;
                count = 0
        else:
            skip = False
    return ret_bits


#
# Class for Frame Check Sequence
#
class FCS(object):
    def __init__(self):
        self.fcs = 0xffff

    def update_bit(self, bit):
        check = (self.fcs & 0x1 == 1)
        self.fcs >>= 1
        if check != bit:
            self.fcs ^= 0x8408

    def update(self, bytes):
        for byte in (ord(b) for b in bytes):
            for i in range(7, -1, -1):
                self.update_bit((byte >> i) & 0x01 == 1)

    def digest(self):
        #        print(self.fcs)
        #        print("%r"%(struct.pack("<H", ~self.fcs % 2**16)))
        #        print("%r"%("".join([chr((~self.fcs & 0xff) % 256), chr((~self.fcs >> 8) % 256)])))
        # digest is two bytes, little endian
        return struct.pack("<H", ~self.fcs % 2 ** 16)


#
# For Frame Check Sequence
#
def fcs(bits):
    '''
    Append running bitwise FCS CRC checksum to end of generator
    '''
    fcs = FCS()
    for bit in bits:
        yield bit
        fcs.update_bit(bit)

    #    test = bitarray.bitarray()
    #    for byte in (digest & 0xff, digest >> 8):
    #        print byte
    #        for i in range(8):
    #            b = (byte >> i) & 1 == 1
    #            test.append(b)
    #            yield b

    # append fcs digest to bit stream

    # n.b. wire format is little-bit-endianness in addition to little-endian
    digest = bitarray.bitarray(endian="little")
    digest.frombytes(fcs.digest())
    for bit in digest:
        yield bit


#
# Confirms Valid FCS
#
def fcs_validate(bits):
    buffer = bitarray.bitarray()
    fcs = FCS()

    for bit in bits:
        buffer.append(bit)
        if len(buffer) > 16:
            bit = buffer.pop(0)
            fcs.update(bit)
            yield bit

    if buffer.tobytes() != fcs.digest():
        raise Exception("FCS checksum invalid.")


#
# Class for the AX.25 Packet
#
class AX25(object):
    def __init__(
            self,
            destination=b"APRS",
            source=b"",
            digipeaters=(b"RELAY", b"WIDE2-1"),
            info=b"\""
    ):
        self.flag = b"\x7e"

        self.destination = destination
        self.source = source
        self.digipeaters = digipeaters

        self.info = info

    @classmethod
    def callsign_encode(self, callsign):
        callsign = callsign.upper()
        if callsign.find(b"-") > 0:
            callsign, ssid = callsign.split(b"-")
        else:
            ssid = b"0"

        assert (len(ssid) == 1)
        assert (len(callsign) <= 6)

        if len(callsign) < 6:
            for i in range(6 - len(callsign)):
                callsign = callsign + b" "

        callsign = b"".join([callsign, ssid])

        # now shift left one bit, arg
        return b"".join([bytes([char << 1]) for char in callsign])

    def callsign_decode(self, callbits):
        callstring = callbits.tobytes()
        code = [chr(char >> 1) for char in callstring]
        return "".join(code)

    def encoded_addresses(self):

        address_bytes = bytearray(b"".join([
            AX25.callsign_encode(self.destination),
            AX25.callsign_encode(self.source),
            b"".join([AX25.callsign_encode(digi) for digi in self.digipeaters])
        ]))

        # set the low order (first, with eventual little bit endian encoding) bit
        # in order to flag the end of the address string
        address_bytes[-1] |= 0x01
        return address_bytes

    def header(self):
        header = b"".join([
            self.encoded_addresses(),
            self.control_field,  # * 8,
            self.protocol_id
        ])
        return header

    def packet(self):
        return b"%b%s%b" % (
            self.flag,
            self.header(),
            self.info,
            self.fcs()
        )

    def unparse(self):
        flag = bitarray.bitarray(endian="little")
        flag.frombytes(self.flag)
        bits = bitarray.bitarray(endian="little")
        bits.frombytes(b"".join([self.header(), self.info, self.fcs()]))

        data = flag + bit_stuff(bits) + flag
        return data

    def parse(self, bits):
        flag = bitarray.bitarray(endian="little")
        flag.frombytes(self.flag)

        # extract bits from the first to second flag
        try:
            flag_loc = bits.search(flag)
            bits_noflag = bits[flag_loc[0] + 8:flag_loc[1]]

            # Bit unstuff
            bits_unstuff = bit_unstuff(bits_noflag)

            # Chop to length
            bits_bytes = bits_unstuff.tobytes()

            # Split bits

            #       header = bits_unstuff[:240]
            h_dest = bits_unstuff[:56]
            h_src = bits_unstuff[56:112]
            for n in range(14, len(bits_bytes) - 1):
                if bits_bytes[n:n + 2] == "\x03\xF0":
                    break
            if n == len(bits_bytes) - 1:
                self.destination = "no decode"
                self.source = "no decode"
                self.info = "no decode"
                self.digis = "no decode"
                return

            digilen = (n - 14) * 8 / 7
            h_digi = bits_unstuff[112:112 + (n - 14) * 8]
            h_len = 112 + (n - 14) * 8 + 16
            fcs = bits_unstuff[-16:]
            info = bits_unstuff[h_len:-16]

            # Split header
            #        protocol = header[-8:]
            #        control = header[-16:-8]
            #        address = header[:-16]

            # Decode addresses
            destination = self.callsign_decode(h_dest)
            source = self.callsign_decode(h_src)

            if digilen == 0:
                digipeaters = ()
            else:
                digipeters = self.callsign_decode(h_digi)
            #     digipeaters = (self.callsign_decode(header[112:168]), self.callsign_decode(header[168:224]))
            print("Destination:\t", destination[:-1])
            print("Source:\t\t", source[:-1])
            #       print "Digipeater1:\t", digipeaters[0][:-1], "-", digipeaters[0][-1]
            print("Digipeaters:\t", digipeaters)
            print("Info:\t\t", info.tobytes())

            self.destination = destination
            self.source = source
            self.info = info.tobytes()
            self.digis = digipeaters
        except:
            self.destination = "no decode"
            self.source = "no decode"
            self.info = "no decode"
            self.digis = "no decode"
            return

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        return "%s>%s,%s:%s" % (
            self.destination,
            self.source,
            b",".join(self.digipeaters),
            self.info
        )

    def fcs(self):
        content = bitarray.bitarray(endian="little")
        content.frombytes(b"".join([self.header(), self.info]))

        fcs = FCS()
        for bit in content:
            fcs.update_bit(bit)
        #        fcs.update(self.header())
        #        fcs.update(self.info)
        return fcs.digest()


#
# Class for UI, Builds AX.25 Packet
#
class UI(AX25):
    def __init__(
            self,
            destination=b"APRS",
            source=b"",
            digipeaters=(b"WIDE1-1", b"WIDE2-1"),
            info=b""
    ):
        AX25.__init__(
            self,
            destination,
            source,
            digipeaters,
            info
        )
        self.control_field = b"\x03"
        self.protocol_id = b"\xf0"
