import json
import unittest

from masterpi_control.agent_client import HibotAgentClient


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({"ok": True, "result": {"done": True}}).encode()


class AgentClientTests(unittest.TestCase):
    def test_camera_vision_has_long_timeout_but_control_actions_remain_short(self):
        calls = []

        def opener(request, *, timeout):
            calls.append((request.full_url, timeout))
            return Response()

        client = HibotAgentClient(opener=opener)
        client.call("state")
        client.call("camera_analyze", {"samples": 3})

        self.assertEqual(calls[0][1], 15.0)
        self.assertEqual(calls[1][1], 180.0)


if __name__ == "__main__":
    unittest.main()
