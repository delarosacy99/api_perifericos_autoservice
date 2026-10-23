from django.test import SimpleTestCase

from ..drivers.desfire_security import (
    STATUS_ADDITIONAL_FRAME,
    STATUS_OK,
    _aes_cbc,
    _tdes_cbc,
    aes_cmac_with_iv,
    authenticate_aes,
    authenticate_iso_2k3des,
    change_aes_key,
    derive_card_key,
    desfire_crc32,
    diversify_aes128,
    require_status,
    rotate_left,
    split_native_response,
    wrap_native_apdu,
)


class DESFireSecurityTestCase(SimpleTestCase):
    def test_wrap_native_apdu(self):
        self.assertEqual(wrap_native_apdu(0x45), bytes.fromhex("9045000000"))
        self.assertEqual(
            wrap_native_apdu(0x1A, bytes.fromhex("00")),
            bytes.fromhex("901A0000010000"),
        )

    def test_response_helpers(self):
        self.assertEqual(
            split_native_response(bytes.fromhex("0F019100")),
            (bytes.fromhex("0F01"), STATUS_OK),
        )
        self.assertEqual(
            require_status(bytes.fromhex("AABB91AF"), STATUS_ADDITIONAL_FRAME),
            bytes.fromhex("AABB"),
        )

    def test_aes128_diversification_matches_nxp_an10922_vector(self):
        master_key = bytes.fromhex("00112233445566778899AABBCCDDEEFF")
        diversification_input = bytes.fromhex("04782E21801D803042F54E585020416275")

        diversified = diversify_aes128(master_key, diversification_input)

        self.assertEqual(
            diversified.hex().upper(),
            "A8DD63A3B89D54B37CA802473FDA9175",
        )

    def test_iso_2k3des_authentication_verifies_both_sides(self):
        key = bytes(16)
        rnd_a = bytes.fromhex("00112233445566778899AABBCCDDEEFF")
        rnd_b = bytes.fromhex("102132435465768798A9BACBDCEDFE0F")
        encrypted_rnd_b = _tdes_cbc(key, bytes(8), rnd_b, encrypt=True)
        calls = []

        def fake_exchange(apdu):
            calls.append(apdu)
            if len(calls) == 1:
                return encrypted_rnd_b + STATUS_ADDITIONAL_FRAME
            encrypted_challenge = apdu[5:-1]
            challenge = _tdes_cbc(
                key,
                encrypted_rnd_b[-8:],
                encrypted_challenge,
                encrypt=False,
            )
            self.assertEqual(challenge, rnd_a + rotate_left(rnd_b))
            response = _tdes_cbc(
                key,
                encrypted_challenge[-8:],
                rotate_left(rnd_a),
                encrypt=True,
            )
            return response + STATUS_OK

        session = authenticate_iso_2k3des(
            fake_exchange,
            key=key,
            random_bytes=lambda size: rnd_a,
        )

        self.assertEqual(session.key_number, 0)
        self.assertEqual(
            session.session_key,
            rnd_a[:4] + rnd_b[:4] + rnd_a[12:] + rnd_b[12:],
        )
        self.assertEqual(calls[0], bytes.fromhex("901A0000010000"))

    def test_iso_factory_des_authentication_accepts_eight_byte_challenge(self):
        key = bytes(16)
        rnd_a = bytes.fromhex("0011223344556677")
        rnd_b = bytes.fromhex("1021324354657687")
        encrypted_rnd_b = _tdes_cbc(key, bytes(8), rnd_b, encrypt=True)
        calls = []

        def fake_exchange(apdu):
            calls.append(apdu)
            if len(calls) == 1:
                return encrypted_rnd_b + STATUS_ADDITIONAL_FRAME
            encrypted_challenge = apdu[5:-1]
            challenge = _tdes_cbc(
                key,
                encrypted_rnd_b[-8:],
                encrypted_challenge,
                encrypt=False,
            )
            self.assertEqual(challenge, rnd_a + rotate_left(rnd_b))
            response = _tdes_cbc(
                key,
                encrypted_challenge[-8:],
                rotate_left(rnd_a),
                encrypt=True,
            )
            return response + STATUS_OK

        session = authenticate_iso_2k3des(
            fake_exchange,
            key=key,
            random_bytes=lambda size: rnd_a,
        )

        self.assertEqual(session.session_key, rnd_a[:4] + rnd_b[:4])
        self.assertEqual(len(calls), 2)

    def test_aes_authentication_verifies_both_sides_and_derives_session_key(self):
        key = bytes.fromhex("00112233445566778899AABBCCDDEEFF")
        rnd_a = bytes.fromhex("000102030405060708090A0B0C0D0E0F")
        rnd_b = bytes.fromhex("102132435465768798A9BACBDCEDFE0F")
        encrypted_rnd_b = _aes_cbc(key, bytes(16), rnd_b, encrypt=True)
        calls = []

        def fake_exchange(apdu):
            calls.append(apdu)
            if len(calls) == 1:
                return encrypted_rnd_b + STATUS_ADDITIONAL_FRAME
            encrypted_challenge = apdu[5:-1]
            challenge = _aes_cbc(
                key,
                encrypted_rnd_b,
                encrypted_challenge,
                encrypt=False,
            )
            self.assertEqual(challenge, rnd_a + rotate_left(rnd_b))
            response = _aes_cbc(
                key,
                encrypted_challenge[-16:],
                rotate_left(rnd_a),
                encrypt=True,
            )
            return response + STATUS_OK

        session = authenticate_aes(
            fake_exchange,
            key_number=1,
            key=key,
            random_bytes=lambda size: rnd_a,
        )

        self.assertEqual(calls[0], bytes.fromhex("90AA0000010100"))
        self.assertEqual(session.key_number, 1)
        self.assertEqual(
            session.session_key,
            rnd_a[:4] + rnd_b[:4] + rnd_a[12:] + rnd_b[12:],
        )

    def test_desfire_crc32_matches_reference_algorithm(self):
        self.assertEqual(desfire_crc32(b"123456789").hex().upper(), "D9C60B34")

    def test_aes_cmac_with_zero_iv_matches_standard_aes_cmac(self):
        key = bytes.fromhex("00112233445566778899AABBCCDDEEFF")
        self.assertEqual(
            aes_cmac_with_iv(key, bytes(16), b"hello").hex().upper(),
            "B93F0FA753C0CCE71F3A3E23F07B0C7A",
        )

    def test_card_key_derivation_separates_admin_and_authentication_keys(self):
        master_key = bytes.fromhex("00112233445566778899AABBCCDDEEFF")
        values = {
            key_number: derive_card_key(
                master_key,
                uid=bytes.fromhex("04C94E2A771890"),
                application_id=bytes.fromhex("D3E001"),
                key_number=key_number,
            )
            .hex()
            .upper()
            for key_number in (0, 1)
        }
        self.assertEqual(values[0], "72B43C60C8BB59A875B34A6AF76EE2B8")
        self.assertEqual(values[1], "56772DD9017CDD1BA96F7C9748712117")

    def test_change_aes_key_encrypts_key_material_and_both_crcs(self):
        session_key = bytes.fromhex("00112233445566778899AABBCCDDEEFF")
        new_key = bytes.fromhex("102132435465768798A9BACBDCEDFE0F")
        calls = []

        def fake_exchange(apdu):
            calls.append(apdu)
            target_key = apdu[5]
            encrypted = apdu[6:-1]
            protected = _aes_cbc(
                session_key,
                bytes(16),
                encrypted,
                encrypt=False,
            )
            clear_command = bytes((0xC4, target_key)) + new_key + b"\x01"
            expected = new_key + b"\x01" + desfire_crc32(clear_command) + desfire_crc32(new_key)
            expected += bytes((-len(expected)) % 16)
            self.assertEqual(protected, expected)
            return STATUS_OK

        change_aes_key(
            fake_exchange,
            authenticated_key_number=0,
            target_key_number=1,
            session_key=session_key,
            old_key=bytes(16),
            new_key=new_key,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:6], bytes.fromhex("90C400002101"))
