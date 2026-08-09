import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import game_mover_minecraft as minecraft


class FakeSocket:
    def __init__(self, response):
        self.response = bytearray(response)
        self.sent = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        return False

    def settimeout(self, _timeout):
        pass

    def sendall(self, payload):
        self.sent.extend(payload)

    def recv(self, length):
        chunk = self.response[:length]
        del self.response[:length]
        return bytes(chunk)


class MinecraftStatusTest(unittest.TestCase):
    def test_status_ping_reads_online_and_max_players(self):
        payload = json.dumps({"players": {"online": 3, "max": 20}}).encode("utf-8")
        body = b"\x00" + minecraft._encode_varint(len(payload)) + payload
        fake_socket = FakeSocket(minecraft._encode_varint(len(body)) + body)
        with patch.object(minecraft.socket, "create_connection", return_value=fake_socket):
            result = minecraft.query_server_status("127.0.0.1", 25570)
        self.assertEqual(result, {"online": 3, "max": 20})
        self.assertTrue(fake_socket.sent.endswith(b"\x01\x00"))

    def test_known_players_uses_configured_world_and_uuid_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data = Path(temporary_directory)
            (data / "server.properties").write_text("level-name=my-world\n", encoding="utf-8")
            player_data = data / "my-world" / "playerdata"
            player_data.mkdir(parents=True)
            (player_data / "17aeaf09-24d4-47b4-a1dd-2aa945960095.dat").touch()
            (player_data / "not-a-player.dat").touch()
            self.assertEqual(minecraft.count_known_players(str(data)), 1)

    def test_known_players_rejects_world_outside_data_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data = Path(temporary_directory)
            (data / "server.properties").write_text("level-name=../other\n", encoding="utf-8")
            self.assertIsNone(minecraft.count_known_players(str(data)))

    def test_known_players_falls_back_to_user_cache(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data = Path(temporary_directory)
            (data / "usercache.json").write_text(
                json.dumps([
                    {
                        "name": "Bernye",
                        "uuid": "17aeaf09-24d4-47b4-a1dd-2aa945960095",
                    }
                ]),
                encoding="utf-8",
            )
            self.assertEqual(minecraft.count_known_players(str(data)), 1)

    def test_known_players_deduplicates_world_and_user_cache(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data = Path(temporary_directory)
            player_id = "17aeaf09-24d4-47b4-a1dd-2aa945960095"
            player_data = data / "world" / "playerdata"
            player_data.mkdir(parents=True)
            (player_data / f"{player_id}.dat").touch()
            (data / "usercache.json").write_text(
                json.dumps([{"name": "Bernye", "uuid": player_id}]),
                encoding="utf-8",
            )
            self.assertEqual(minecraft.count_known_players(str(data)), 1)


if __name__ == "__main__":
    unittest.main()
