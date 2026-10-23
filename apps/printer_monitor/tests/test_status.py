from django.test import SimpleTestCase

from ..drivers.status import (
    COMMAND_ERROR,
    COVER_OPEN,
    CUTTER_ERROR,
    NEAR_PAPER_END,
    NO_PAPER,
    PAPER_END,
    PAPER_JAM,
    TICKET_OUT,
    VIRTUAL_PAPER_END,
    decode_ceprintlib_k80_status,
    decode_full_status,
)


class FullStatusDecoderTestCase(SimpleTestCase):
    def test_zero_status_is_ready_and_preserves_all_four_bytes(self):
        decoded = decode_full_status(0)

        self.assertEqual(decoded.state, "ready")
        self.assertTrue(decoded.ready)
        self.assertEqual(decoded.alerts, ())
        self.assertEqual(
            decoded.status_bytes,
            {
                "paper_status_hex": "00",
                "user_status_hex": "00",
                "recoverable_error_status_hex": "00",
                "unrecoverable_error_status_hex": "00",
            },
        )

    def test_near_end_and_ticket_out_are_warnings(self):
        decoded = decode_full_status(NEAR_PAPER_END | TICKET_OUT)

        self.assertEqual(decoded.state, "warning")
        self.assertTrue(decoded.ready)
        self.assertEqual(decoded.alerts, ("paper_near_end", "ticket_out"))

    def test_requested_faults_are_decoded_from_the_sdk_masks(self):
        status = COVER_OPEN | PAPER_JAM | CUTTER_ERROR | COMMAND_ERROR

        decoded = decode_full_status(status)

        self.assertEqual(decoded.state, "error")
        self.assertFalse(decoded.ready)
        self.assertTrue(decoded.cover_open)
        self.assertTrue(decoded.paper_jam)
        self.assertTrue(decoded.cutter_error)
        self.assertTrue(decoded.command_error)
        self.assertEqual(decoded.status_bytes["user_status_hex"], "02")
        self.assertEqual(decoded.status_bytes["recoverable_error_status_hex"], "60")
        self.assertEqual(decoded.status_bytes["unrecoverable_error_status_hex"], "01")

    def test_decodes_ceprintlib_k80_status_byte_order(self):
        paper = decode_ceprintlib_k80_status(0x21000000)
        cover = decode_ceprintlib_k80_status(0x00020000)
        cutter = decode_ceprintlib_k80_status(0x00000001)

        self.assertTrue(paper.paper_out)
        self.assertTrue(paper.ticket_out)
        self.assertFalse(paper.cover_open)
        self.assertFalse(paper.cutter_error)
        self.assertTrue(cover.cover_open)
        self.assertTrue(cutter.cutter_error)

    def test_all_documented_paper_end_forms_are_blocking(self):
        for status in (NO_PAPER, PAPER_END, VIRTUAL_PAPER_END):
            with self.subTest(status=status):
                decoded = decode_full_status(status)
                self.assertTrue(decoded.paper_out if status != VIRTUAL_PAPER_END else True)
                self.assertEqual(decoded.state, "error")
                self.assertFalse(decoded.ready)

    def test_rejects_values_outside_unsigned_dword(self):
        for status in (-1, 0x1_0000_0000):
            with self.subTest(status=status), self.assertRaises(ValueError):
                decode_full_status(status)

