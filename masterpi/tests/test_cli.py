import unittest

from masterpi_control.cli import _parser


class CliTests(unittest.TestCase):
    def test_grab_accepts_supported_target_colors(self):
        args = _parser().parse_args(["grab", "green"])
        self.assertEqual(args.command, "grab")
        self.assertEqual(args.target, "green")

    def test_serve_accepts_https_certificate_options(self):
        args = _parser().parse_args(
            [
                "serve",
                "--tls-port",
                "8443",
                "--certfile",
                "/tls/server.crt",
                "--keyfile",
                "/tls/server.key",
                "--ca-certfile",
                "/tls/ca.crt",
            ]
        )
        self.assertEqual(args.tls_port, 8443)
        self.assertEqual(args.certfile, "/tls/server.crt")
        self.assertEqual(args.keyfile, "/tls/server.key")
        self.assertEqual(args.ca_certfile, "/tls/ca.crt")

    def test_sound_direction_accepts_array_orientation(self):
        args = _parser().parse_args(
            ["sound-direction", "--samples", "7", "--front-angle", "90", "--counterclockwise"]
        )
        self.assertEqual(args.samples, 7)
        self.assertEqual(args.front_angle, 90)
        self.assertTrue(args.counterclockwise)


if __name__ == "__main__":
    unittest.main()
