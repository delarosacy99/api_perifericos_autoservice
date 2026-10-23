from django.test import SimpleTestCase

from ..drivers.desfire import (
    card_type_from_software,
    decode_bcd,
    decode_storage_size,
    parse_version_component,
)


class DESFireParserTestCase(SimpleTestCase):
    def test_observed_ev3_version_frames_are_parsed(self):
        hardware = parse_version_component(bytes.fromhex("04010133001A05"))
        software = parse_version_component(bytes.fromhex("04010103001A05"))

        self.assertEqual(hardware.vendor_id, 0x04)
        self.assertEqual(hardware.storage_size_code, 0x1A)
        self.assertEqual(software.major, 3)
        self.assertEqual(card_type_from_software(software), "mifare_desfire_ev3")
        self.assertEqual(decode_storage_size(hardware.storage_size_code), 8192)

    def test_observed_manufacturing_date_uses_bcd(self):
        self.assertEqual(decode_bcd(0x23, field="semana"), 23)
        self.assertEqual(2000 + decode_bcd(0x23, field="año"), 2023)
