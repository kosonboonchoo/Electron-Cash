#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# -*- mode: python3 -*-

# taken (with minor modifications) from pycoin
# https://github.com/richardkiss/pycoin/blob/01b1787ed902df23f99a55deb00d8cd076a906fe/pycoin/ecdsa/native/secp256k1.py

import os
import sys
import traceback
import ecdsa
from ctypes import (byref, c_size_t, create_string_buffer, cast, POINTER, c_char_p)

from .util import print_error, print_msg
from . import secp256k1


class _patched_functions:
    prepared_to_patch = False
    monkey_patching_active = False


def _prepare_monkey_patching_of_python_ecdsa_internals_with_libsecp256k1():
    if not secp256k1.secp256k1:
        return

    # save original functions so that we can undo patching (needed for tests)
    _patched_functions.orig_sign   = staticmethod(ecdsa.ecdsa.Private_key.sign)
    _patched_functions.orig_verify = staticmethod(ecdsa.ecdsa.Public_key.verifies)
    _patched_functions.orig_mul    = staticmethod(ecdsa.ellipticcurve.Point.__mul__)
    _patched_functions.orig_add    = staticmethod(ecdsa.ellipticcurve.Point.__add__)

    curve_secp256k1 = ecdsa.ecdsa.curve_secp256k1
    curve_order = ecdsa.curves.SECP256k1.order
    point_at_infinity = ecdsa.ellipticcurve.INFINITY

    def _get_ptr_to_well_formed_pubkey_string_buffer_from_ecdsa_point(point: ecdsa.ellipticcurve.Point):
        assert point.curve() == curve_secp256k1
        pubkey = create_string_buffer(64)
        public_pair_bytes = b'\4' + point.x().to_bytes(32, byteorder="big") + point.y().to_bytes(32, byteorder="big")
        r = secp256k1.secp256k1.secp256k1_ec_pubkey_parse(
            secp256k1.secp256k1.ctx, pubkey, public_pair_bytes, len(public_pair_bytes))
        if not r:
            raise Exception('public key could not be parsed or is invalid')
        return pubkey

    def _get_ecdsa_point_from_libsecp256k1_pubkey_object(pubkey) -> ecdsa.ellipticcurve.Point:
        pubkey_serialized = create_string_buffer(65)
        pubkey_size = c_size_t(65)
        secp256k1.secp256k1.secp256k1_ec_pubkey_serialize(
            secp256k1.secp256k1.ctx, pubkey_serialized, byref(pubkey_size), pubkey, secp256k1.SECP256K1_EC_UNCOMPRESSED)
        x = int.from_bytes(pubkey_serialized[1:33], byteorder="big")
        y = int.from_bytes(pubkey_serialized[33:], byteorder="big")
        return ecdsa.ellipticcurve.Point(curve_secp256k1, x, y, curve_order)

    def add(self: ecdsa.ellipticcurve.Point, other: ecdsa.ellipticcurve.Point) -> ecdsa.ellipticcurve.Point:
        if self.curve() != curve_secp256k1:
            # this operation is not on the secp256k1 curve; use original implementation
            return _patched_functions.orig_add(self, other)
        if self == point_at_infinity: return other
        if other == point_at_infinity: return self

        pubkey1 = _get_ptr_to_well_formed_pubkey_string_buffer_from_ecdsa_point(self)
        pubkey2 = _get_ptr_to_well_formed_pubkey_string_buffer_from_ecdsa_point(other)
        pubkey_sum = create_string_buffer(64)

        pubkey1 = cast(pubkey1, POINTER(c_char_p))
        pubkey2 = cast(pubkey2, POINTER(c_char_p))
        ptr_to_array_of_pubkey_ptrs = (POINTER(c_char_p) * 2)(pubkey1, pubkey2)
        r = secp256k1.secp256k1.secp256k1_ec_pubkey_combine(secp256k1.secp256k1.ctx, pubkey_sum, ptr_to_array_of_pubkey_ptrs, 2)
        if not r:
            return point_at_infinity
        return _get_ecdsa_point_from_libsecp256k1_pubkey_object(pubkey_sum)

    def mul(self: ecdsa.ellipticcurve.Point, other: int) -> ecdsa.ellipticcurve.Point:
        if self.curve() != curve_secp256k1:
            # this operation is not on the secp256k1 curve; use original implementation
            return _patched_functions.orig_mul(self, other)
        other %= curve_order
        if self == point_at_infinity or other == 0:
            return point_at_infinity
        pubkey = _get_ptr_to_well_formed_pubkey_string_buffer_from_ecdsa_point(self)
        r = secp256k1.secp256k1.secp256k1_ec_pubkey_tweak_mul(secp256k1.secp256k1.ctx, pubkey, other.to_bytes(32, byteorder="big"))
        if not r:
            return point_at_infinity
        return _get_ecdsa_point_from_libsecp256k1_pubkey_object(pubkey)

    def sign(self: ecdsa.ecdsa.Private_key, hash: int, random_k: int) -> ecdsa.ecdsa.Signature:
        # note: random_k is ignored
        if self.public_key.curve != curve_secp256k1:
            # this operation is not on the secp256k1 curve; use original implementation
            return _patched_functions.orig_sign(self, hash, random_k)
        secret_exponent = self.secret_multiplier
        nonce_function = None
        sig = create_string_buffer(64)
        sig_hash_bytes = hash.to_bytes(32, byteorder="big")
        r = secp256k1.secp256k1.secp256k1_ecdsa_sign(
            secp256k1.secp256k1.ctx, sig, sig_hash_bytes, secret_exponent.to_bytes(32, byteorder="big"), nonce_function, None)
        if not r:
            raise Exception('the nonce generation function failed, or the private key was invalid')
        compact_signature = create_string_buffer(64)
        secp256k1.secp256k1.secp256k1_ecdsa_signature_serialize_compact(secp256k1.secp256k1.ctx, compact_signature, sig)
        r = int.from_bytes(compact_signature[:32], byteorder="big")
        s = int.from_bytes(compact_signature[32:], byteorder="big")
        return ecdsa.ecdsa.Signature(r, s)

    def verify(self: ecdsa.ecdsa.Public_key, hash: int, signature: ecdsa.ecdsa.Signature) -> bool:
        if self.curve != curve_secp256k1:
            # this operation is not on the secp256k1 curve; use original implementation
            return _patched_functions.orig_verify(self, hash, signature)
        sig = create_string_buffer(64)
        input64 = signature.r.to_bytes(32, byteorder="big") + signature.s.to_bytes(32, byteorder="big")
        r = secp256k1.secp256k1.secp256k1_ecdsa_signature_parse_compact(secp256k1.secp256k1.ctx, sig, input64)
        if not r:
            return False
        r = secp256k1.secp256k1.secp256k1_ecdsa_signature_normalize(secp256k1.secp256k1.ctx, sig, sig)

        public_pair_bytes = b'\4' + self.point.x().to_bytes(32, byteorder="big") + self.point.y().to_bytes(32, byteorder="big")
        pubkey = create_string_buffer(64)
        r = secp256k1.secp256k1.secp256k1_ec_pubkey_parse(
            secp256k1.secp256k1.ctx, pubkey, public_pair_bytes, len(public_pair_bytes))
        if not r:
            return False

        return 1 == secp256k1.secp256k1.secp256k1_ecdsa_verify(secp256k1.secp256k1.ctx, sig, hash.to_bytes(32, byteorder="big"), pubkey)

    # save new functions so that we can (re-)do patching
    _patched_functions.fast_sign   = sign
    _patched_functions.fast_verify = verify
    _patched_functions.fast_mul    = mul
    _patched_functions.fast_add    = add

    _patched_functions.prepared_to_patch = True


def do_monkey_patching_of_python_ecdsa_internals_with_libsecp256k1():
    if not secp256k1.secp256k1:
        print_msg('[ecc] info: libsecp256k1 library not available, falling back to python-ecdsa. '
                  'This means signing operations will be slower. '
                  'Try running:\n\n  $  contrib/make_secp\n\n(You need to be running from the git sources for contrib/make_secp to be available)'
                  )
        return
    if not _patched_functions.prepared_to_patch:
        raise Exception("can't patch python-ecdsa without preparations")
    ecdsa.ecdsa.Private_key.sign      = _patched_functions.fast_sign
    ecdsa.ecdsa.Public_key.verifies   = _patched_functions.fast_verify
    ecdsa.ellipticcurve.Point.__mul__ = _patched_functions.fast_mul
    ecdsa.ellipticcurve.Point.__add__ = _patched_functions.fast_add

    _patched_functions.monkey_patching_active = True
    #print_error('[ecc] info: libsecp256k1 library found and will be used for ecdsa signing operations.')


def undo_monkey_patching_of_python_ecdsa_internals_with_libsecp256k1():
    if not secp256k1.secp256k1:
        return
    if not _patched_functions.prepared_to_patch:
        raise Exception("can't patch python-ecdsa without preparations")
    ecdsa.ecdsa.Private_key.sign      = _patched_functions.orig_sign
    ecdsa.ecdsa.Public_key.verifies   = _patched_functions.orig_verify
    ecdsa.ellipticcurve.Point.__mul__ = _patched_functions.orig_mul
    ecdsa.ellipticcurve.Point.__add__ = _patched_functions.orig_add

    _patched_functions.monkey_patching_active = False


def is_using_fast_ecc():
    return _patched_functions.monkey_patching_active


_prepare_monkey_patching_of_python_ecdsa_internals_with_libsecp256k1()
